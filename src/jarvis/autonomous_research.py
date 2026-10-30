"""Autonomous research session: Director decides, trusted executors act, Critic reviews.

Loop per `advance` call (one durable step each):
  1. collect finished background actions and register their artifacts
  2. review one completed action with the independent Critic
  3. otherwise ask the Director for one decision (respond to owner guidance, open
     branches/questions, choose an action), validate it against the contract, the
     active research policy and the stop guard, then execute or launch it.
Long actions (ANALYZE, BACKTEST, TRAIN_MODEL, IMPLEMENT) run in child campaigns on
background threads so the Director can keep working while they run.
"""

import functools
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jarvis.autonomy_contracts import (
    AnalyzeAction,
    AutonomyContract,
    AutonomyDecision,
    AutonomyReview,
    AutonomySessionSpec,
    BacktestAction,
    Candidate,
    CodePatch,
    CompareAction,
    ConsultAction,
    GuidanceEntry,
    ImplementAction,
    ModelArtifact,
    ObservedTable,
    ReadSourceAction,
    StopAction,
    TrainModelAction,
    WaitAction,
    setting_dict,
)
from jarvis.autonomy_jobs import (
    ActionRunner,
    ProviderUnavailable,
    is_quota_refusal,
    local_job,
    settle_ended_provider_calls,
)
from jarvis.autonomy_policy import ResearchPolicy, describe, enforce, q0
from jarvis.bounded_roles import evidence_role
from jarvis.campaign import code_provenance, environment, source_identity
from jarvis.candidates import compare_results, select_finalist
from jarvis.contracts import AdmissionError, BudgetExceeded, IntegrityError, JarvisError, Record
from jarvis.identity import digest, file_digest, raw_digest
from jarvis.lab_contracts import ExpertOpinion
from jarvis.native_execution import capability as native_capability
from jarvis.research_executors import Executors, resolve_candidate
from jarvis.researcher import AgentConfig
from jarvis.role_feedback import DecisionAdmissionError, correction_context, failure_category
from jarvis.runtime_controls import ContextEnvelopeExceeded
from jarvis.storage import Store
from jarvis.workspace_agent import WorkerStep, prepare_workspace, run_workspace_task

ASYNC_KINDS = {"ANALYZE", "BACKTEST", "TRAIN_MODEL", "IMPLEMENT"}
REVIEWED_KINDS = {"ANALYZE", "BACKTEST", "TRAIN_MODEL", "IMPLEMENT", "COMPARE"}
ROLES = ("director", "critic", "worker", "consultant")
MAX_CONSECUTIVE_FAILURES = 3
SOURCE_LIMIT = 60000


MEMORY_SHOWN = {
    "experiments": 40,
    "tables": 40,
    "candidates": 20,
}  # recalled items per role context
CONTEXT_BUDGET = 90000  # characters of Director context JSON before older detail is compacted


def clip(value: Any, text: int = 600, items: int = 40) -> Any:
    """Bounded view of a recorded value for prompts; the full value stays in the ledger."""
    if isinstance(value, str):
        return (
            value
            if len(value) <= text
            else value[:text] + f"... [{len(value) - text} chars at ref]"
        )
    if isinstance(value, list):
        head = [clip(v, text, items) for v in value[:items]]
        return head + (
            [f"... [{len(value) - items} more items at ref]"] if len(value) > items else []
        )
    if isinstance(value, dict):
        keys = list(value)
        out = {k: clip(value[k], text, items) for k in keys[:items]}
        if len(keys) > items:
            out["..."] = f"{len(keys) - items} more keys at ref"
        return out
    return value


def provenance_summary(observation: dict[str, Any]) -> dict[str, Any]:
    """Trusted execution facts shown to research roles so they need not re-derive them."""
    p = observation.get("provenance", {})
    keys = (
        "request_id",
        "reused_request",
        "commit",
        "raw_sha256",
        "evaluator_sha256",
        "calendar",
        "note",
    )
    return {
        "result_origin": observation.get("result_origin"),
        "evaluator_id": observation.get("evaluator_id"),
        "period": observation.get("period"),
        **{k: (p[k] if k != "calendar" else f"{len(p[k])} trading dates") for k in keys if k in p},
        "trades_table_is_raw_iris_output": "trades" in observation.get("tables", {}),
    }


def payloads(store: Store, owner: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in store.events(owner) if e["event_type"] == kind]


def default_role_config() -> dict[str, Any]:
    base = AgentConfig(
        model="gpt-6.1-sol",
        reasoning_effort="high",
        max_calls=1000,
        reserve_tokens=120000,
        timeout_seconds=900,
    )
    return {
        "director": base.model_dump(mode="json"),
        "critic": base.model_dump(mode="json"),
        "worker": base.model_copy(update={"timeout_seconds": 600}).model_dump(mode="json"),
        "consultant": base.model_dump(mode="json"),
    }


def role_config(spec: AutonomySessionSpec, role: str) -> AgentConfig:
    merged = {**default_role_config(), **spec.role_config}
    config = AgentConfig.model_validate(merged[role])
    if spec.runtime:
        config = config.model_copy(
            update={
                "max_context_bytes": spec.runtime.max_context_bytes,
                "max_prompt_bytes": spec.runtime.max_prompt_bytes,
                "provider_retries": spec.runtime.provider_retries,
            }
        )
    return config


def contract_of(store: Store, spec: AutonomySessionSpec) -> AutonomyContract:
    return AutonomyContract.model_validate(store.artifacts.get(spec.contract_ref))


def policy_of(store: Store, spec: AutonomySessionSpec) -> ResearchPolicy:
    return (
        ResearchPolicy.model_validate(store.artifacts.get(spec.policy_ref))
        if spec.policy_ref
        else q0()
    )


def table_record(
    store: Store,
    path: Path,
    *,
    name: str,
    description: str,
    lineage: dict[str, Any],
    exposure: str,
) -> str:
    data = path.read_bytes()
    blob = store.blobs.put(data)
    if path.suffix == ".json":
        columns: tuple[str, ...] = ()
        rows = 0
        fmt = "json"
    else:
        text = data.decode("utf-8-sig", errors="replace").splitlines()
        columns = tuple(c.strip() for c in text[0].split(","))[:512] if text else ()
        rows = max(0, len(text) - 1)
        fmt = "csv"
    return store.artifacts.put(
        ObservedTable(
            name=name,
            blob_ref=blob,
            format=fmt,  # type: ignore[arg-type]
            columns=columns,
            rows=rows,
            description=description[:1000],
            lineage=lineage,
            exposure=exposure,  # type: ignore[arg-type]
        )
    )


class AutonomousResearch:
    def __init__(
        self,
        store: Store,
        executors: Executors,
        provider_factory: Callable[[AgentConfig, str], Any],
        *,
        runner: ActionRunner | None = None,
        consolidate: bool = False,
    ):
        """`consolidate=True`: every run that ends (closed, budget, attention, pause, cancel)
        is written into the domain knowledge base so later sessions are affected."""
        self.store = store
        self.executors = executors
        self.provider_factory = provider_factory
        self.runner = runner or ActionRunner()
        self.consolidate = consolidate

    # ------------------------------------------------------------------ creation
    def create(self, spec: AutonomySessionSpec, *, budget_owner: str | None = None) -> str:
        contract = contract_of(self.store, spec)
        if spec.runtime and contract.analysis_delivery and "ANALYZE" in contract.grants.actions:
            if spec.budget.max_parallel_jobs < spec.max_concurrent_actions + 1:
                raise AdmissionError(
                    "runtime preflight: strict analysis finish/rerun needs a nested job slot in addition to concurrent actions"
                )
        if spec.runtime:
            if contract.analysis_environment.native != self.executors.workbench.environment.native:
                raise AdmissionError("runtime preflight: native declaration differs from executor")
            if (
                contract.analysis_environment.native
                and not native_capability(self.executors.workbench)["available"]
            ):
                raise AdmissionError(
                    "runtime preflight: declared native capability unavailable or changed"
                )
        baseline = Candidate.model_validate(
            self.store.artifacts.get(contract.baseline_candidate_ref)
        )
        if baseline.origin != "owner_baseline":
            raise AdmissionError("baseline candidate must be owner supplied")
        for ref in spec.input_refs:
            table = ObservedTable.model_validate(self.store.artifacts.get(ref))
            if table.exposure != contract.exposure:
                raise AdmissionError("input exposure escaped the contract")
            self.store.blobs.get(table.blob_ref)
        for ref in spec.prior_evidence_refs:
            self.store.artifacts.get(ref)
        for scope in contract.source_scopes:
            if file_digest(Path(scope.path)) != scope.sha256:
                raise AdmissionError("declared source changed: " + scope.name)
        policy_of(self.store, spec)
        providers = {
            role: self.provider_factory(role_config(spec, role), role).identity() for role in ROLES
        }
        identity = {
            "spec": spec.model_dump(mode="json"),
            "providers": providers,
            "executors": self.executors.identity(),
            "environment": environment(),
        }
        return self.store.create(
            {
                "kind": "AutonomousResearchSession",
                "spec": spec.model_dump(mode="json"),
                "world_id": digest(identity),
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
                "environment": identity["environment"],
                "providers": providers,
                "executors": identity["executors"],
                "task": {"budget": spec.budget.model_dump()},
                **({"budget_owner": budget_owner} if budget_owner else {}),
            }
        )

    def memory(self, spec: AutonomySessionSpec) -> dict[str, Any] | None:
        return self.store.artifacts.get(spec.memory_ref) if spec.memory_ref else None

    def spec(self, owner: str) -> AutonomySessionSpec:
        return AutonomySessionSpec.model_validate(self.store.manifest(owner)["spec"])

    def provider(self, owner: str, role: str) -> Any:
        provider = self.provider_factory(role_config(self.spec(owner), role), role)
        if provider.identity() != self.store.manifest(owner)["providers"][role]:
            raise IntegrityError(f"{role} provider changed; declare a new session")
        return provider

    # ------------------------------------------------------------------ owner input
    def advise(self, owner: str, text: str, *, mode: str = "advise") -> str:
        existing = payloads(self.store, owner, "GUIDANCE_RECORDED")
        entry = GuidanceEntry(guidance_id=f"g{len(existing) + 1}", mode=mode, text=text)  # type: ignore[arg-type]
        ref = self.store.artifacts.put(entry)
        self.store.append(
            owner, "GUIDANCE_RECORDED", {"guidance_ref": ref, "guidance_id": entry.guidance_id}
        )
        return entry.guidance_id

    # ------------------------------------------------------------------ derived state
    def state(self, owner: str, *, before_sequence: int | None = None) -> dict[str, Any]:
        """Derived session state; `before_sequence` reconstructs what was known earlier."""
        spec = self.spec(owner)
        contract = contract_of(self.store, spec)
        visible = [
            spec.contract_ref,
            contract.baseline_candidate_ref,
            *spec.input_refs,
            *spec.prior_evidence_refs,
        ]
        state: dict[str, Any] = {
            "decisions": [],
            "launched": {},
            "completed": {},
            "reviews": {},
            "review_evidence": {},
            "branches": {},
            "questions": {},
            "guidance": {},
            "responses": {},
            "candidates": [contract.baseline_candidate_ref],
            "tables": list(spec.input_refs),
            "observations": [],
            "patches": [],
            "models": [],
            "failures": [],
            "completion_order": [],
            "planning_review": None,
            "owner_checkpoint": None,
        }
        for event in self.store.events(owner):
            if before_sequence is not None and event["sequence"] >= before_sequence:
                break
            kind, p = event["event_type"], event["payload"]
            if kind == "GUIDANCE_RECORDED":
                state["guidance"][p["guidance_id"]] = self.store.artifacts.get(p["guidance_ref"])
                visible.append(p["guidance_ref"])
                if state["owner_checkpoint"] is not None:
                    state["owner_checkpoint"]["has_owner_reply"] = True
            elif kind == "AUTONOMY_DECISION":
                state["decisions"].append(p)
                decision = self.store.artifacts.get(p["decision_ref"])
                if state["owner_checkpoint"] is not None:
                    state["owner_checkpoint"] = None
                if decision.get("planning_review") is not None:
                    state["planning_review"] = {
                        "index": p["index"],
                        "review": decision["planning_review"],
                    }
                for response in decision["guidance_responses"]:
                    state["responses"][response["guidance_id"]] = {**response, "index": p["index"]}
                if decision["branch"]:
                    state["branches"][decision["branch"]["branch_id"]] = {
                        **decision["branch"],
                        "status": "open",
                        "opened_at": p["index"],
                        "closure": None,
                    }
                if decision["question"]:
                    state["questions"][decision["question"]["question_id"]] = {
                        **decision["question"],
                        "branch_id": decision["action"]["branch_id"],
                    }
            elif kind == "DIRECTOR_FAILURE":
                state["failures"].append(p)
            elif kind == "ACTION_LAUNCHED":
                state["launched"][p["index"]] = p["child"]
            elif kind == "ACTION_COMPLETED":
                state["completed"][p["index"]] = p
                state["completion_order"].append(p["index"])
                visible.append(p["result_ref"])
                if p["kind"] == "STOP" and contract.planning:
                    decision = self.store.artifacts.get(
                        state["decisions"][p["index"]]["decision_ref"]
                    )
                    if decision["action"]["scope"] == "checkpoint":
                        state["owner_checkpoint"] = {
                            "index": p["index"],
                            "question": decision["planning_review"]["owner_question"],
                            "has_owner_reply": False,
                        }
            elif kind == "ARTIFACTS_REGISTERED":
                for key in ("tables", "candidates", "observations", "patches", "models"):
                    state[key].extend(p[key])
                    visible.extend(p[key])
            elif kind == "ACTION_REVIEWED":
                state["reviews"][p["index"]] = p
                visible.append(p["review_ref"])
            elif kind == "REVIEW_EVIDENCE_PACKED":
                state["review_evidence"][p["index"]] = p
                visible.append(p["evidence_ref"])
            elif kind == "BRANCH_CLOSED":
                if p["branch_id"] in state["branches"]:
                    state["branches"][p["branch_id"]]["status"] = "closed"
                    state["branches"][p["branch_id"]]["closure"] = p
            elif kind == "OWNER_CHECKPOINT_REQUESTED":
                state["owner_checkpoint"] = {
                    "index": p["index"],
                    "question": p["question"],
                    "has_owner_reply": False,
                }
        memory = self.memory(spec)
        if memory:
            state["candidates"] = list(dict.fromkeys([*state["candidates"], *memory["candidates"]]))
            state["tables"] = list(dict.fromkeys([*memory["tables"], *state["tables"]]))
            state["observations"] = list(
                dict.fromkeys([*memory["experiments"], *state["observations"]])
            )
            visible[1:1] = [
                str(spec.memory_ref),
                *[x["ref"] for x in memory["lessons"]],
                *[x["ref"] for x in memory["skills"]],
                *memory["candidates"],
                *memory["tables"],
                *memory["experiments"],
                *[g["ref"] for g in memory.get("owner_guidance", [])],
            ]
        state["memory"] = memory
        state["visible"] = list(dict.fromkeys(visible))
        state["running"] = [i for i in state["launched"] if i not in state["completed"]]
        state["pending_guidance"] = [g for g in state["guidance"] if g not in state["responses"]]
        return state

    def history(self, owner: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for p in state["decisions"]:
            decision = self.store.artifacts.get(p["decision_ref"])
            branch = state["branches"].get(decision["action"]["branch_id"], {})
            rows.append(
                {
                    "index": p["index"],
                    "kind": decision["action"]["kind"],
                    "branch_id": decision["action"]["branch_id"],
                    "branch_kind": branch.get("kind"),
                }
            )
        return rows

    def policy_state(self, owner: str, state: dict[str, Any]) -> dict[str, Any]:
        contract = contract_of(self.store, self.spec(owner))
        shifting = {s.name for s in contract.settings if s.affects_model_inputs}
        analyzed: set[str] = set()
        for p in state["decisions"]:
            action = self.store.artifacts.get(p["decision_ref"])["action"]
            if action["kind"] == "ANALYZE" and p["index"] in state["completed"]:
                analyzed.update(action["input_refs"])
        baseline = self.baseline_observation(state)
        unexplained: list[str] = []
        input_shift = False
        values: dict[str, set[str]] = {}
        # Policy rules judge this session's own actions; remembered experiments are reused evidence.
        remembered = set((state.get("memory") or {}).get("experiments", []))
        for ref in state["observations"]:
            if ref in remembered:
                continue
            obs = self.store.artifacts.get(ref)
            for name, value in obs["changes"].items():
                values.setdefault(name, set()).add(repr(value))
            if obs["status"] != "SUCCEEDED" or baseline is None or ref == baseline["result_ref"]:
                continue
            if any(v["verdict"] == "PASS" for v in self.profile_verdicts(contract, obs, baseline)):
                tables = obs["tables"]
                if tables and not set(tables.values()) & analyzed:
                    unexplained.append(
                        obs["label"]
                        + "("
                        + ", ".join(f"{k}={v}" for k, v in sorted(tables.items()))
                        + ")"
                    )
                if set(obs["changes"]) & shifting:
                    input_shift = True
        defaults = {s.name: s.baseline for s in contract.settings}
        return {
            "history": self.history(owner, state),
            "candidate_settings": {
                ref: {**defaults, **self.store.artifacts.get(ref)["settings"]}
                for ref in state["candidates"]
            },
            "branch_kinds": {k: v["kind"] for k, v in state["branches"].items()},
            "unexplained_gains": unexplained,
            "input_shifting_gain": input_shift,
            "trainer_available": self.executors.trainer is not None,
            # Earlier sessions that already trained models or closed a model branch count.
            "model_research_in_memory": bool(
                state.get("memory")
                and (
                    any(
                        self.store.artifacts.get(c)["origin"] == "agent_trained"
                        for c in state["memory"]["candidates"]
                    )
                    or any(
                        b.get("kind") == "model"
                        for sess in state["memory"]["sessions"]
                        for b in sess.get("branch_closures", [])
                    )
                )
            ),
            "setting_values": {k: sorted(v) for k, v in values.items()},
        }

    def baseline_observation(self, state: dict[str, Any]) -> dict[str, Any] | None:
        for ref in state["observations"]:
            obs = self.store.artifacts.get(ref)
            if (
                obs["candidate_origin"] == "owner_baseline"
                and not obs["changes"]
                and obs["status"] == "SUCCEEDED"
            ):
                return {**obs, "result_ref": ref}
        return None

    def profile_verdicts(
        self, contract: AutonomyContract, obs: dict[str, Any], baseline: dict[str, Any]
    ) -> list[dict[str, Any]]:
        from jarvis.candidates import assess_profile

        if obs["period"] != baseline["period"]:
            return []
        return [
            assess_profile(contract, p, obs["metrics"], baseline["metrics"])
            for p in contract.acceptance_profiles
        ]

    # ------------------------------------------------------------------ contexts
    def summarize(
        self, owner: str, state: dict[str, Any], index: int, full: bool, *, detail: int = 1
    ) -> dict[str, Any]:
        """detail 2 = latest (near-complete), 1 = recent, 0 = compact."""
        p = state["completed"][index]
        result = self.store.artifacts.get(p["result_ref"])
        review = state["reviews"].get(index)
        summary: dict[str, Any] = {
            "index": index,
            "kind": p["kind"],
            "branch_id": p["branch_id"],
            "result_ref": p["result_ref"],
            "status": result.get("status"),
        }
        if full and detail >= 2:
            summary["result"] = clip(result, SOURCE_LIMIT, 200)
        elif full:
            summary["result"] = clip(result, 4000, 60)
        elif detail and p["kind"] in {"BACKTEST", "COMPARE", "TRAIN_MODEL", "IMPLEMENT"}:
            summary["result"] = clip(
                {
                    k: v
                    for k, v in result.items()
                    if k not in {"sources", "diff", "transcript_owner"}
                },
                300,
                24,
            )
        else:
            summary["result_summary"] = str(result.get("summary") or result.get("status"))[:300]
        if review and not detail:
            value = self.store.artifacts.get(review["review_ref"])
            summary["review"] = {
                "review_ref": review["review_ref"],
                "recommendation": value["recommendation"],
            }
        elif review:
            value = self.store.artifacts.get(review["review_ref"])
            summary["review"] = {
                "review_ref": review["review_ref"],
                "recommendation": value["recommendation"],
                "summary": value["summary"],
                "concerns": value["concerns"],
                "next_questions": value["next_questions"],
            }
        if detail >= 2 and index in state["review_evidence"]:
            summary["trusted_review_evidence"] = self.store.artifacts.get(
                state["review_evidence"][index]["evidence_ref"]
            )
        return summary

    def director_context(self, owner: str, state: dict[str, Any]) -> dict[str, Any]:
        """Bounded Director view: older detail is compacted to stay within CONTEXT_BUDGET."""
        for window, older in ((6, 1), (4, 1), (2, 0), (1, 0)):
            context = self._director_context(owner, state, window, older)
            if len(json.dumps(context, ensure_ascii=False)) <= CONTEXT_BUDGET:
                break
        return context

    def _director_context(
        self, owner: str, state: dict[str, Any], window: int, older: int
    ) -> dict[str, Any]:
        spec = self.spec(owner)
        contract = contract_of(self.store, spec)
        policy = policy_of(self.store, spec)
        completed = sorted(state["completed"])
        recent = set(completed[-window:])
        latest = completed[-1] if completed else None
        resources = self.store.resource_totals_tree(owner)
        shown = self._memory_shown(contract, state)
        hidden = set(shown["all"]) - set(shown["refs"])
        context = {
            "research_contract": contract.model_dump(mode="json"),
            "contract_ref": spec.contract_ref,
            "research_policy": describe(policy),
            "baseline_candidate": {
                "ref": contract.baseline_candidate_ref,
                "value": self.store.artifacts.get(contract.baseline_candidate_ref),
            },
            "prior_evidence": [
                {"ref": r, "value": self.store.artifacts.get(r)} for r in spec.prior_evidence_refs
            ],
            "research_plan": self.plan(owner, state),
            "candidates": [
                {"ref": r, **self._candidate_summary(r), "from_memory": r in shown["candidates"]}
                for r in state["candidates"]
                if r not in hidden
            ],
            "experiment_ledger": [
                {
                    **self._experiment_row(r),
                    "from_memory": r in shown["experiments"],
                    # Only same-evaluator, same-dataset rows can enter a COMPARE with this contract.
                    "comparable": self._comparable(contract, r),
                }
                for r in state["observations"]
                if r not in hidden
            ],
            "research_memory": self._memory_view(state, shown),
            "tables": [
                {**self._table_summary(r, compact=True), "from_memory": True}
                if r in shown["tables"]
                else self._table_summary(r)
                for r in state["tables"]
                if r not in hidden
            ],
            "code_patches": [
                {"ref": r, "target": self.store.artifacts.get(r)["target"]}
                for r in state["patches"]
            ],
            "observations": [
                self.summarize(
                    owner,
                    state,
                    i,
                    i in recent,
                    detail=2 if i == latest else (1 if i in recent else older),
                )
                for i in completed
            ],
            "running_actions": [
                {"index": i, "kind": self._decision(state, i)["action"]["kind"]}
                for i in state["running"]
            ],
            "owner_guidance": [
                {
                    **g,
                    "status": "pending" if gid in state["pending_guidance"] else "responded",
                    "response": state["responses"].get(gid),
                }
                for gid, g in state["guidance"].items()
            ],
            "pending_guidance_ids": state["pending_guidance"],
            "available_actions": list(contract.grants.actions),
            "analyzable_evidence_ids": sorted(self._analyzable(state) - hidden),
            "executors_available": {
                "backtest": self.executors.backtester is not None,
                "train_model": self.executors.trainer is not None,
                "implement_targets": sorted(self.executors.validators),
                "analysis_workbench": True,
                "native_execution": native_capability(self.executors.workbench),
            },
            "budget": {
                "limits": spec.budget.model_dump(mode="json"),
                "used": resources,
                "decisions_used": sum(h["kind"] != "WAIT" for h in self.history(owner, state)),
                "waits": sum(h["kind"] == "WAIT" for h in self.history(owner, state)),
                "max_decisions": spec.max_decisions,
            },
            "stop_rule": "A session STOP must close every open branch with evidence; research_complete requires a compared finalist. Running actions must finish first.",
            "visible_evidence_ids": [r for r in state["visible"] if r not in hidden],
        }
        if contract.stages:
            from jarvis.autonomy_report import session_outcome
            from jarvis.autonomy_stages import frozen_candidate

            outcome = session_outcome(self.store, owner)
            context["stage_progress"] = {
                "plan": contract.stages.model_dump(mode="json"),
                "gate": outcome["stage_gate"],
                "selection_finalist": outcome["selection_finalist"],
                "selection_settings": outcome["selection_settings"],
                "confirmation": outcome["confirmation"],
                "frozen_candidate": frozen_candidate(self.store, owner),
                "independent_validation": "NOT_RUN",
            }
        return context

    def _memory_shown(self, contract: AutonomyContract, state: dict[str, Any]) -> dict[str, Any]:
        """Bounded recall: the latest same-dataset experiments with their tables and candidates.

        Older or other-dataset items stay valid evidence but are summarized by sessions/lessons,
        so role contexts do not grow with the number of remembered sessions."""
        memory = state.get("memory") or {}
        if not memory:
            return {"experiments": [], "tables": [], "candidates": [], "refs": [], "all": []}
        cap = MEMORY_SHOWN
        experiments = [r for r in memory["experiments"] if self._comparable(contract, r)][
            -cap["experiments"] :
        ]
        values = [self.store.artifacts.get(r) for r in experiments]
        attached = [t for o in values for t in o["tables"].values() if t in memory["tables"]]
        tables = list(dict.fromkeys([*attached, *memory["tables"][::-1]]))[: cap["tables"]]
        used = [o["candidate_ref"] for o in values if o["candidate_ref"] in memory["candidates"]]
        candidates = list(dict.fromkeys([*used, *memory["candidates"][::-1]]))[
            : max(cap["candidates"], len(set(used)))
        ]
        listed = [*memory["experiments"], *memory["tables"], *memory["candidates"]]
        return {
            "experiments": experiments,
            "tables": tables,
            "candidates": candidates,
            "refs": [*experiments, *tables, *candidates],
            "all": listed,
        }

    @staticmethod
    def _memory_view(state: dict[str, Any], shown: dict[str, Any]) -> dict[str, Any] | None:
        memory = state.get("memory")
        if not memory:
            return None
        return {
            "note": "Earlier sessions in this domain. Their latest same-dataset experiments are in "
            "experiment_ledger (from_memory) and are not repeated; their candidates and tables can be used "
            "directly; skills are mounted under skills/ in every analysis workspace. Older or other-dataset "
            "items are summarized by sessions and lessons only. owner_guidance and owner_preference lessons "
            "are the owner's standing preferences; apply them unless current owner guidance says otherwise.",
            "sessions": clip(memory["sessions"][-20:], 300, 20),
            "lessons": clip(memory["lessons"], 600, 60),
            "skills": memory["skills"],
            "open_questions": memory["open_questions"],
            # Standing owner preferences: follow them unless this session's guidance overrides.
            "owner_guidance": clip(
                [
                    {k: g.get(k) for k in ("source", "ref", "text", "adopted", "response")}
                    for g in memory.get("owner_guidance", [])
                ][-20:],
                500,
                20,
            ),
            "not_shown": {
                k: len(memory[k]) - len(shown[k]) for k in ("experiments", "tables", "candidates")
            },
        }

    def _comparable(self, contract: AutonomyContract, ref: str) -> bool:
        o = self.store.artifacts.get(ref)
        return bool(
            o.get("evaluator_id") == contract.evaluator_id
            and o.get("dataset_id") == contract.dataset_id
        )

    def _experiment_row(self, ref: str) -> dict[str, Any]:
        """Every backtest stays visible compactly, however long the session gets."""
        o = self.store.artifacts.get(ref)
        return {
            "result_ref": ref,
            "label": o["label"],
            "action_index": o["action_index"],
            "candidate_ref": o["candidate_ref"],
            "model_id": o["model_id"],
            "changes": o["changes"],
            "period": o["period"],
            "status": o["status"],
            "metrics": o["metrics"],
            "tables": o["tables"],
            "provenance": provenance_summary(o),
        }

    def _table_summary(self, ref: str, *, compact: bool = False) -> dict[str, Any]:
        t = self.store.artifacts.get(ref)
        lineage = t["lineage"]
        return {
            "ref": ref,
            "name": t["name"],
            "rows": t["rows"],
            **({} if compact else {"columns": list(t["columns"][:60])}),
            "description": t["description"][: 120 if compact else 240],
            "lineage": {
                k: lineage[k]
                for k in ("action_index", "experiment", "candidate_ref", "period", "kind", "origin")
                if k in lineage
            },
        }

    def _decision(self, state: dict[str, Any], index: int) -> dict[str, Any]:
        return self.store.artifacts.get(state["decisions"][index]["decision_ref"])

    def _candidate_summary(self, ref: str) -> dict[str, Any]:
        c = self.store.artifacts.get(ref)
        model = self.store.artifacts.get(c["model_ref"]) if c["model_ref"] else None
        return {
            "label": c["label"],
            "origin": c["origin"],
            "parent_ref": c["parent_ref"],
            "settings": c["settings"],
            "model_id": model["model_id"] if model else None,
            "model_validation": model["validation"] if model else None,
            "patches": [self.store.artifacts.get(r)["target"] for r in c["patch_refs"]],
            "data_policy": c["data_policy"],
        }

    def plan(self, owner: str, state: dict[str, Any]) -> dict[str, Any]:
        branches = []
        for branch_id, branch in state["branches"].items():
            indices = [
                p["index"]
                for p in state["decisions"]
                if self.store.artifacts.get(p["decision_ref"])["action"]["branch_id"] == branch_id
            ]
            branches.append(
                {
                    "branch_id": branch_id,
                    "kind": branch["kind"],
                    "goal": branch["goal"],
                    "status": branch["status"],
                    "actions": indices,
                    "closure": branch["closure"],
                }
            )
        questions = []
        for q in state["questions"].values():
            verdicts = []
            for review in state["reviews"].values():
                for a in self.store.artifacts.get(review["review_ref"])["assessments"]:
                    if a["hypothesis_id"] in {h["hypothesis_id"] for h in q["hypotheses"]}:
                        verdicts.append(a)
            questions.append({**q, "verdicts": verdicts})
        plan: dict[str, Any] = {"branches": branches, "questions": questions}
        if contract_of(self.store, self.spec(owner)).planning is not None:
            from jarvis.autonomy_planning import planning_view

            plan["planning"] = planning_view(self.store, state)
        return plan

    # ------------------------------------------------------------------ validation
    def validate_decision(self, owner: str, answer: Record, frozen: dict[str, Any]) -> None:
        assert isinstance(answer, AutonomyDecision)
        state = self.state(owner)
        spec = self.spec(owner)
        contract = contract_of(self.store, spec)
        action = answer.action
        visible = set(frozen["visible_evidence_ids"])
        if action.kind not in contract.grants.actions:
            raise ValueError(f"{action.kind} is not granted by the contract")
        responded = [r.guidance_id for r in answer.guidance_responses]
        if sorted(responded) != sorted(frozen["pending_guidance_ids"]):
            raise ValueError("respond to exactly every pending owner guidance item")
        branches = {b["branch_id"]: b for b in frozen["research_plan"]["branches"]}
        if answer.branch:
            if answer.branch.branch_id in branches:
                raise ValueError("branch identity already exists")
            if action.branch_id != answer.branch.branch_id:
                raise ValueError("a new branch must be the action's branch")
            branches[answer.branch.branch_id] = {"status": "open", "kind": answer.branch.kind}
        if action.branch_id not in branches:
            raise ValueError("action must belong to a declared branch; open one first")
        if branches[action.branch_id]["status"] != "open":
            raise ValueError("branch is closed; open a new branch")
        questions = {q["question_id"]: q for q in frozen["research_plan"]["questions"]}
        if answer.question:
            known_h = {h["hypothesis_id"] for q in questions.values() for h in q["hypotheses"]}
            catalog = {
                q: [h["hypothesis_id"] for h in v["hypotheses"]] for q, v in questions.items()
            }
            duplicate_h = sorted(
                h.hypothesis_id for h in answer.question.hypotheses if h.hypothesis_id in known_h
            )
            if answer.question.question_id in questions or duplicate_h:
                raise DecisionAdmissionError(
                    "EXISTING_RESEARCH_IDENTITY",
                    "question/hypothesis identity already exists; reuse its question_id with question=null, or declare new unique IDs",
                    {
                        "proposed_question_id": answer.question.question_id,
                        "conflicting_hypothesis_ids": duplicate_h,
                        "existing_questions": catalog,
                        "instruction": "A new branch can address an existing question without redeclaring it.",
                    },
                )
            bad_refs = sorted(
                {r for h in answer.question.hypotheses for r in h.evidence_refs} - visible
            )
            if bad_refs:
                raise DecisionAdmissionError(
                    "INVISIBLE_HYPOTHESIS_EVIDENCE",
                    "question cites invisible evidence",
                    {"invalid_refs": bad_refs},
                )
            questions[answer.question.question_id] = answer.question.model_dump(mode="json")
        if action.question_id not in questions or not set(action.hypothesis_ids) <= {
            h["hypothesis_id"] for h in questions[action.question_id]["hypotheses"]
        }:
            raise ValueError("action must address hypotheses of a declared question")
        if not set(action.evidence_refs) <= visible:
            raise ValueError("cite only visible evidence")
        from jarvis.autonomy_planning import validate_planning

        validate_planning(self.store, contract, state, answer, frozen)
        if contract.stages:
            from jarvis.autonomy_stages import frozen_candidate

            if frozen_candidate(self.store, owner) and action.kind in {"TRAIN_MODEL", "IMPLEMENT"}:
                raise ValueError(
                    "confirmation froze candidate generation; declare a new session to tune"
                )
        decisions = [
            self.store.artifacts.get(p["decision_ref"])["action"] for p in state["decisions"]
        ]
        if isinstance(action, ReadSourceAction):
            names = {s.name for s in contract.source_scopes}
            if not set(action.scopes) <= names:
                raise ValueError("unknown source scope")
        elif isinstance(action, AnalyzeAction):
            allowed = self._analyzable(state) & visible
            if not set(action.input_refs) <= allowed:  # all are recorded, immutable artifacts
                raise ValueError(
                    "ANALYZE inputs must be registered tables, backtest observations, action results, "
                    "candidates, models or patches; not: "
                    + ", ".join(sorted(set(action.input_refs) - allowed))
                    + ". Choose input_refs from analyzable_evidence_ids; role/critique receipts "
                    "may be cited as evidence but are not workspace input records."
                )
            if any(
                a["kind"] == "ANALYZE"
                and a["question"].strip() == action.question.strip()
                and sorted(a["input_refs"]) == sorted(action.input_refs)
                for a in decisions
            ):
                raise ValueError(
                    "this analysis repeats an earlier one; ask a new question or use other inputs"
                )
        elif isinstance(action, BacktestAction):
            self._check_backtest(contract, state, action, visible=visible)
            if contract.stages:
                from jarvis.autonomy_report import session_outcome
                from jarvis.autonomy_stages import admit_staged_backtest

                if state["running"] and any(
                    e.period in contract.stages.confirmation for e in action.experiments
                ):
                    raise ValueError("wait for running actions before freezing confirmation")
                admit_staged_backtest(
                    self.store, owner, contract, action, session_outcome(self.store, owner)
                )
        elif isinstance(action, TrainModelAction):
            if self.executors.trainer is None:
                raise ValueError("no training executor is available")
            if (
                sum(a["kind"] == "TRAIN_MODEL" for a in decisions)
                >= contract.grants.max_training_runs
            ):
                raise ValueError("training run grant exhausted")
            if action.base_candidate_ref not in state["candidates"]:
                raise ValueError("unknown base candidate")
            if action.training_data_ref and action.training_data_ref not in state["tables"]:
                raise ValueError("training data must be a registered observed table")
            if action.pipeline_patch_ref and (
                action.pipeline_patch_ref not in state["patches"]
                or self.store.artifacts.get(action.pipeline_patch_ref)["target_kind"]
                != "pipeline_patch"
            ):
                raise ValueError("pipeline patch must be a registered pipeline_patch")
            self.executors.trainer.validate_config(
                contract.training_space, setting_dict(action.config)
            )
        elif isinstance(action, ImplementAction):
            targets = {t.name: t for t in contract.implement_targets}
            if action.target not in targets or action.target not in self.executors.validators:
                raise ValueError("implementation target is not declared or has no validator")
            if (
                sum(a["kind"] == "IMPLEMENT" for a in decisions)
                >= contract.grants.max_implementations
            ):
                raise ValueError("implementation grant exhausted")
            if action.base_candidate_ref not in state["candidates"]:
                raise ValueError("unknown base candidate")
        elif isinstance(action, CompareAction):
            refs = [action.baseline_result_ref, *action.result_refs]
            if not set(refs) <= set(state["observations"]) or len(set(refs)) != len(refs):
                raise ValueError("COMPARE uses distinct registered backtest observations")
            if any(self.store.artifacts.get(r)["status"] != "SUCCEEDED" for r in refs):
                raise ValueError("only succeeded observations can be compared")
            if len({self.store.artifacts.get(r)["period"] for r in refs}) != 1:
                raise ValueError("compared observations must share one period")
            if not all(self._comparable(contract, r) for r in refs):
                raise ValueError(
                    "compared observations must use this contract's evaluator and dataset "
                    "(experiment_ledger.comparable)"
                )
        elif isinstance(action, WaitAction):
            if not state["running"]:
                raise ValueError("WAIT requires a running action")
        elif isinstance(action, StopAction):
            self._check_stop(owner, contract, state, action, branches, visible)
        enforce(policy_of(self.store, spec), self.policy_state(owner, state), answer)

    @staticmethod
    def _analyzable(state: dict[str, Any]) -> set[str]:
        results = {p["result_ref"] for p in state["completed"].values()}
        keys = ("tables", "observations", "candidates", "models", "patches")
        return set().union(*(set(state[k]) for k in keys)) | results

    def _check_backtest(
        self,
        contract: AutonomyContract,
        state: dict[str, Any],
        action: BacktestAction,
        *,
        visible: set[str] | None = None,
    ) -> None:
        if self.executors.backtester is None:
            raise ValueError("no backtest executor is available")
        if len(action.experiments) > contract.grants.max_experiments_per_backtest:
            raise ValueError("too many experiments in one backtest action")
        specs = {s.name: s for s in contract.settings}
        seen: dict[tuple[str, str, str], list[str]] = {}
        for ref in state["observations"]:
            observation = self.store.artifacts.get(ref)
            if not self._comparable(contract, ref):
                continue  # other datasets/evaluators cannot substitute for this experiment
            key = (
                observation["candidate_ref"],
                json.dumps(observation["settings"], sort_keys=True),
                observation["period"],
            )
            seen.setdefault(key, []).append(ref)
        labels = set()
        for experiment in action.experiments:
            if experiment.label in labels:
                raise ValueError("duplicate experiment label")
            labels.add(experiment.label)
            if experiment.candidate_ref not in state["candidates"]:
                raise ValueError("experiment candidate is not registered")
            if experiment.period not in contract.periods:
                raise ValueError("undeclared period")
            candidate = self.store.artifacts.get(experiment.candidate_ref)
            changes = setting_dict(experiment.settings)
            for name, value in changes.items():
                if name not in specs:
                    raise ValueError(f"undeclared setting {name}")
                specs[name].check(value)
            before = {**{s.name: s.baseline for s in contract.settings}, **candidate["settings"]}
            effective = {**before, **changes}
            changed = {k for k, v in changes.items() if before.get(k) != v}
            if len(changed) > contract.grants.max_setting_changes:
                raise ValueError("too many simultaneous setting changes")
            key = (
                experiment.candidate_ref,
                json.dumps(effective, sort_keys=True),
                experiment.period,
            )
            if key in seen:
                if not seen[key]:
                    raise DecisionAdmissionError(
                        "DUPLICATE_BATCH_CONFIGURATION",
                        "same configuration appears twice in this batch; keep one copy",
                        {
                            "label": experiment.label,
                            "candidate_ref": experiment.candidate_ref,
                            "period": experiment.period,
                        },
                    )
                refs = [r for r in seen[key] if visible is not None and r in visible]
                raise DecisionAdmissionError(
                    "RECORDED_BACKTEST_CONFIGURATION",
                    f"experiment {experiment.label} repeats an observed configuration; omit it from experiments and cite its visible result_ref as the matched control",
                    {
                        "label": experiment.label,
                        "candidate_ref": experiment.candidate_ref,
                        "period": experiment.period,
                        "effective_settings": effective,
                        "existing_result_refs": refs,
                        "instruction": "Submit only unobserved configurations. A label containing reuse does not bypass duplicate admission. Inspect a recorded failed/unknown job before any retry; do not assume it is a successful control.",
                    },
                )
            seen[key] = []

    def _check_stop(
        self,
        owner: str,
        contract: AutonomyContract,
        state: dict[str, Any],
        action: StopAction,
        branches: dict[str, Any],
        visible: set[str],
    ) -> None:
        open_ids = {b for b, v in branches.items() if v["status"] == "open"}
        closures = {c.branch_id for c in action.closures}
        if not closures <= open_ids:
            raise ValueError("closures may only close open branches")
        for c in action.closures:
            if not set(c.evidence_refs) <= visible:
                raise ValueError("closure evidence must be visible")
        if action.scope == "branch":
            if action.branch_id not in closures:
                raise ValueError("a branch STOP must close its own branch")
            return
        if state["running"]:
            raise ValueError("stop guard: actions are still running; WAIT for them first")
        if action.scope == "checkpoint":
            return  # validated planning review; keep branches open for owner collaboration
        if closures != open_ids:
            raise ValueError(
                "stop guard: close every open branch with evidence before stopping: "
                + ", ".join(sorted(open_ids - closures))
            )
        if action.conclusion == "research_complete":
            if contract.stages:
                from jarvis.autonomy_report import session_outcome

                outcome = session_outcome(self.store, owner)
                if outcome.get("stage_gate") != "PASS" or action.finalist_result_ref != outcome[
                    "finalist"
                ].get("result_ref"):
                    raise ValueError(
                        "stop guard: selection and every required confirmation must pass"
                    )
            if action.finalist_result_ref not in state["observations"]:
                raise ValueError(
                    "stop guard: research_complete needs a finalist backtest observation"
                )
            compared = [
                self.store.artifacts.get(state["completed"][i]["result_ref"])
                for i in state["completed"]
                if state["completed"][i]["kind"] == "COMPARE"
            ]
            if not any(
                action.finalist_result_ref in {r["result_ref"] for r in c.get("rows", [])}
                for c in compared
            ):
                raise ValueError("stop guard: the finalist must appear in a completed COMPARE")

    # ------------------------------------------------------------------ main step
    def advance(self, owner: str) -> dict[str, Any]:
        with self.store.campaign_lock(owner, scope="autonomy"):
            control = self.store.get_control(owner)
            if control in {"PAUSED", "CANCELLED"}:
                return {"status": control}
            status = self.store.status(owner)
            if status == "NEEDS_RECONCILIATION":
                # Provably ended provider calls are settled at their reservation; anything
                # still unknown keeps the session stopped for manual reconciliation.
                settle_ended_provider_calls(self.store, owner)
                status = self.store.status(owner)
            if status == "PROVIDER_UNAVAILABLE":
                # Each new advance after a provider refusal tries again exactly once.
                self.store.append(owner, "PROVIDER_RESUMED", {})
                for child in self.state(owner)["launched"].values():
                    if self._child_paused(child):
                        self.store.append(child, "CHILD_PROVIDER_RESUMED", {})
                self.store.set_status(owner, "RUNNING")
                status = "RUNNING"
            if status in {"CLOSED", "BUDGET_EXHAUSTED", "NEEDS_RECONCILIATION", "NEEDS_ATTENTION"}:
                return {"status": status}
            m = self.store.manifest(owner)
            if m["source_identity"] != source_identity() or m["environment"] != environment():
                raise IntegrityError("research runtime changed; declare a new session")
            if m["executors"] != self.executors.identity():
                raise IntegrityError("declared executors changed; declare a new session")
            try:
                return self._advance(owner)
            except ContextEnvelopeExceeded as exc:
                self.store.append(owner, "SESSION_PARTIAL_STOP", {"reason": str(exc)})
                self.store.set_status(owner, "NEEDS_ATTENTION")
                return {"status": "NEEDS_ATTENTION", "reason": str(exc)}
            except BudgetExceeded as exc:
                self.store.append(owner, "SESSION_PARTIAL_STOP", {"reason": str(exc)})
                self.store.set_status(owner, "BUDGET_EXHAUSTED")
                return {"status": "BUDGET_EXHAUSTED", "reason": str(exc)}

    def _advance(self, owner: str) -> dict[str, Any]:
        state = self.state(owner)
        if contract_of(self.store, self.spec(owner)).planning and state["decisions"]:
            index = len(state["decisions"]) - 1
            stop_decision = AutonomyDecision.model_validate(self._decision(state, index))
            if isinstance(stop_decision.action, StopAction) and index in state["completed"]:
                missing = any(
                    state["branches"][c.branch_id]["status"] != "closed"
                    for c in stop_decision.action.closures
                )
                if missing or stop_decision.action.scope == "session":
                    return self._finish_stop(owner, index, stop_decision)
        checkpoint = state.get("owner_checkpoint")
        if checkpoint and not checkpoint["has_owner_reply"]:
            self.store.control(owner, "PAUSED")
            return {"status": "PAUSED", "owner_question": checkpoint["question"]}
        collected = self._collect(owner, state)
        if collected:
            return collected
        for index in sorted(state["completed"]):
            if (
                state["completed"][index]["kind"] in REVIEWED_KINDS
                and index not in state["reviews"]
            ):
                return self._review(owner, state, index)
        spec = self.spec(owner)
        if state["decisions"]:
            last = len(state["decisions"]) - 1
            if last not in state["completed"] and last not in state["launched"]:
                if self._decision(state, last)["action"]["kind"] in ASYNC_KINDS:
                    child = self._launch(owner, last, state["decisions"][last]["decision_ref"])
                    return {"status": "ACTION_LAUNCHED", "index": last, "child": child}
                return self._finish_sync(owner, last)
            if (
                self._decision(state, last)["action"]["kind"] == "WAIT"
                and last in state["completed"]
            ):
                order = state["completion_order"]
                if not order[order.index(last) + 1 :] and state["running"]:
                    return {"status": "WAITING", "running": state["running"]}
        if len(state["running"]) >= spec.max_concurrent_actions:
            return {"status": "WAITING", "running": state["running"]}
        substantive = sum(h["kind"] != "WAIT" for h in self.history(owner, state))
        if substantive >= spec.max_decisions:
            if state["running"]:
                return {"status": "WAITING", "running": state["running"]}
            self.store.append(owner, "SESSION_PARTIAL_STOP", {"reason": "declared decision bound"})
            self.store.set_status(owner, "BUDGET_EXHAUSTED")
            return {"status": "BUDGET_EXHAUSTED", "reason": "declared decision bound"}
        return self._decide(owner, state)

    def _decide(self, owner: str, state: dict[str, Any]) -> dict[str, Any]:
        index = len(state["decisions"])
        failures = [f for f in state["failures"] if f["index"] == index]
        attempt = len(failures)  # monotonic key; never re-dispatch a settled request
        stopped = self._role_failure_stop(owner, "director", index, failures)
        if stopped:
            return stopped
        context = self.director_context(owner, state)
        if attempt:
            context["retry_attempt"] = attempt  # a retry is a distinct, separately receipted call
            self._carry_rejection(owner, f"decision:{index}:", context)
        pauses = self._pauses(owner, "director", index)
        if pauses:
            context["provider_retry"] = pauses
        policy = policy_of(self.store, self.spec(owner))
        try:
            decision = evidence_role(
                self.store,
                owner,
                f"decision:{index}:{attempt}" + (f":q{pauses}" if pauses else ""),
                "autonomy_director",
                context,
                AutonomyDecision,
                self.provider(owner, "director"),
                role_config(self.spec(owner), "director"),
                policy.guidance,
                validate=lambda a, f: self.validate_decision(owner, a, f),
            )
        except ContextEnvelopeExceeded:
            raise
        except AdmissionError as exc:
            if is_quota_refusal(exc):
                return self._pause_provider(owner, "director", index, exc)
            self.store.append(
                owner,
                "DIRECTOR_FAILURE",
                {
                    "index": index,
                    "attempt": attempt,
                    "category": failure_category({"reason": str(exc)}),
                    "reason": str(exc)[:1500],
                },
            )
            return {"status": "DIRECTOR_RETRY", "reason": str(exc)[:300]}
        except JarvisError as exc:
            if not settle_ended_provider_calls(self.store, owner):
                raise
            self.store.append(
                owner,
                "DIRECTOR_FAILURE",
                {
                    "index": index,
                    "attempt": attempt,
                    "category": "provider_ended",
                    "reason": "provider ended without usage; settled at reservation: "
                    + str(exc)[:500],
                },
            )
            return {"status": "DIRECTOR_RETRY", "reason": "provider call ended without usage"}
        assert isinstance(decision, AutonomyDecision)
        record = [
            p
            for p in payloads(self.store, owner, "LAB_ROLE_RECORDED")
            if p["answer_ref"] == digest(decision)
        ][-1]
        decision_ref = self.store.artifacts.put(decision)
        self.store.append(
            owner,
            "AUTONOMY_DECISION",
            {"index": index, "decision_ref": decision_ref, "context_ref": record["context_ref"]},
        )
        action = decision.action
        if action.kind in ASYNC_KINDS:
            child = self._launch(owner, index, decision_ref)
            return {
                "status": "ACTION_LAUNCHED",
                "action": action.kind,
                "index": index,
                "child": child,
            }
        return self._finish_sync(owner, index)

    def _finish_sync(self, owner: str, index: int) -> dict[str, Any]:
        """Execute (or, after an interruption, re-execute) a recorded inline action."""
        state = self.state(owner)
        decision = AutonomyDecision.model_validate(self._decision(state, index))
        action = decision.action
        try:
            result = self._execute_sync(owner, index, decision)
        except ProviderUnavailable as exc:
            return self._pause_provider(owner, "consultant", index, exc)
        self._complete(owner, index, decision, self.store.artifacts.put(result), {})
        if isinstance(action, StopAction):
            return self._finish_stop(owner, index, decision)
        return {"status": "ACTION_COMPLETED", "action": action.kind, "index": index}

    def _finish_stop(self, owner: str, index: int, decision: AutonomyDecision) -> dict[str, Any]:
        """Finalize a recorded stop idempotently, including recovery after completion."""
        action = decision.action
        assert isinstance(action, StopAction)
        if isinstance(action, StopAction):
            if action.scope == "checkpoint":
                assert decision.planning_review is not None
                question = decision.planning_review.owner_question
                self.store.control(owner, "PAUSED")
                return {"status": "PAUSED", "owner_question": question}
            closed = {
                (p["index"], p["branch_id"]) for p in payloads(self.store, owner, "BRANCH_CLOSED")
            }
            for closure in action.closures:
                if (index, closure.branch_id) in closed:
                    continue
                self.store.append(
                    owner, "BRANCH_CLOSED", {**closure.model_dump(mode="json"), "index": index}
                )
            if action.scope == "session":
                if not payloads(self.store, owner, "SESSION_STOPPED"):
                    self.store.append(
                        owner,
                        "SESSION_STOPPED",
                        {
                            "index": index,
                            "conclusion": action.conclusion,
                            "finalist_result_ref": action.finalist_result_ref,
                        },
                    )
                self.store.set_status(owner, "CLOSED")
                return {"status": "CLOSED", "conclusion": action.conclusion}
        return {"status": "ACTION_COMPLETED", "action": action.kind, "index": index}

    def _pauses(self, owner: str, role: str, index: int) -> int:
        return sum(
            1
            for p in payloads(self.store, owner, "PROVIDER_PAUSED")
            if p["role"] == role and p["index"] == index
        )

    def _pause_provider(
        self, owner: str, role: str, index: int, exc: BaseException
    ) -> dict[str, Any]:
        self.store.append(
            owner, "PROVIDER_PAUSED", {"role": role, "index": index, "reason": str(exc)[:600]}
        )
        self.store.set_status(owner, "PROVIDER_UNAVAILABLE")
        return {"status": "PROVIDER_UNAVAILABLE", "reason": str(exc)[:300]}

    def _complete(
        self,
        owner: str,
        index: int,
        decision: AutonomyDecision,
        result_ref: str,
        register: dict[str, list[str]],
    ) -> None:
        self.store.append(
            owner,
            "ACTION_COMPLETED",
            {
                "index": index,
                "kind": decision.action.kind,
                "branch_id": decision.action.branch_id,
                "result_ref": result_ref,
            },
        )
        if any(register.values()):
            self.store.append(
                owner,
                "ARTIFACTS_REGISTERED",
                {
                    "index": index,
                    **{
                        k: register.get(k, [])
                        for k in ("tables", "candidates", "observations", "patches", "models")
                    },
                },
            )

    # ------------------------------------------------------------------ background actions
    def _launch(self, owner: str, index: int, decision_ref: str) -> str:
        spec = self.spec(owner)
        contract = contract_of(self.store, spec)
        action = AutonomyDecision.model_validate(self.store.artifacts.get(decision_ref)).action
        if contract.stages and isinstance(action, BacktestAction):
            from jarvis.autonomy_report import session_outcome
            from jarvis.autonomy_stages import admit_staged_backtest

            freeze = admit_staged_backtest(
                self.store, owner, contract, action, session_outcome(self.store, owner)
            )
            if freeze:
                self.store.append(
                    owner, "CONFIRMATION_CANDIDATE_FROZEN", {**freeze, "decision_index": index}
                )
        child = self.store.create(
            {
                "kind": "AutonomousAction",
                "parent": owner,
                "index": index,
                "decision_ref": decision_ref,
                "world_id": digest(
                    {"parent": self.store.manifest(owner)["world_id"], "index": index}
                ),
                "task": {"budget": spec.budget.model_dump()},
                "budget_owner": owner,
            }
        )
        self.store.append(owner, "ACTION_LAUNCHED", {"index": index, "child": child})
        self.runner.submit(child, lambda: self._run_child(owner, child))
        return child

    def _run_child(self, owner: str, child: str) -> None:
        if payloads(self.store, child, "ACTION_RESULT"):
            return
        m = self.store.manifest(child)
        decision = AutonomyDecision.model_validate(self.store.artifacts.get(m["decision_ref"]))
        try:
            value, register = self._execute_async(owner, child, m["index"], decision)
        except ProviderUnavailable as exc:
            self.store.append(child, "CHILD_PROVIDER_PAUSED", {"reason": str(exc)[:600]})
            return
        except BudgetExceeded as exc:
            value, register = {"status": "BUDGET_EXCEEDED", "error": str(exc)}, {}
        except (AdmissionError, IntegrityError) as exc:
            value, register = (
                {"status": "FAILED", "error": type(exc).__name__ + ": " + str(exc)[:2000]},
                {},
            )
        self.store.append(
            child,
            "ACTION_RESULT",
            {"result_ref": self.store.artifacts.put(value), "register": register},
        )
        self.store.set_status(child, "CLOSED")

    def _collect(self, owner: str, state: dict[str, Any]) -> dict[str, Any] | None:
        for index in state["running"]:
            child = state["launched"][index]
            done = payloads(self.store, child, "ACTION_RESULT")
            decision = AutonomyDecision.model_validate(self._decision(state, index))
            if done:
                self._complete(owner, index, decision, done[0]["result_ref"], done[0]["register"])
                return {
                    "status": "ACTION_COMPLETED",
                    "action": decision.action.kind,
                    "index": index,
                }
            if self._child_paused(child) and not self.runner.running(child):
                return self._pause_provider(
                    owner, "worker", index, ProviderUnavailable("worker paused")
                )
            failure = self.runner.failure(child)
            if failure is not None:
                if (
                    isinstance(self.runner.futures[child].exception(), JarvisError)
                    and self.store.status(child) == "NEEDS_RECONCILIATION"
                ):
                    self.store.set_status(owner, "NEEDS_RECONCILIATION")
                    return {"status": "NEEDS_RECONCILIATION", "child": child}
                value = {"status": "FAILED", "error": failure}
                self.store.append(
                    child,
                    "ACTION_RESULT",
                    {"result_ref": self.store.artifacts.put(value), "register": {}},
                )
                self.store.set_status(child, "CLOSED")
                continue
            if not self.runner.running(child):
                # Resumed in a new process: continue the same child idempotently.
                self.runner.submit(child, functools.partial(self._run_child, owner, child))
        return None

    def _child_paused(self, child: str) -> bool:
        events = [e["event_type"] for e in self.store.events(child)]
        return events.count("CHILD_PROVIDER_PAUSED") > events.count("CHILD_PROVIDER_RESUMED")

    def workdir(self, child: str) -> Path:
        path = self.store.root / "action-work" / child
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def _execute_async(
        self, owner: str, child: str, index: int, decision: AutonomyDecision
    ) -> tuple[dict[str, Any], dict[str, list[str]]]:
        action = decision.action
        if isinstance(action, AnalyzeAction):
            return self._analyze(owner, child, index, action)
        if isinstance(action, BacktestAction):
            return self._backtest(owner, child, index, action)
        if isinstance(action, TrainModelAction):
            return self._train(owner, child, index, action)
        assert isinstance(action, ImplementAction)
        return self._implement(owner, child, index, action)

    def _skill_files(
        self, spec: AutonomySessionSpec
    ) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
        memory = self.memory(spec)
        files: dict[str, bytes] = {}
        manifest = []
        for entry in (memory or {}).get("skills", []):
            skill = self.store.artifacts.get(entry["ref"])
            folder = skill["name"]
            for name, ref in skill["files"].items():
                files[f"{folder}/{name}"] = self.store.blobs.get(ref)
            files[f"{folder}/README.md"] = (
                f"# {skill['name']} v{skill['version']}\n\n{skill['description']}\n\nInputs: {skill['inputs']}\n\n"
                f"Usage: {skill['usage']}\n\nOrigin: {json.dumps(skill['origin'])}\n"
            ).encode()
            manifest.append(
                {
                    "name": skill["name"],
                    "version": skill["version"],
                    "path": f"skills/{folder}/",
                    "files": sorted(skill["files"]),
                    "description": skill["description"],
                    "usage": skill["usage"],
                    "inputs": skill["inputs"],
                }
            )
        return files, manifest

    def _sources(self, contract: AutonomyContract) -> dict[str, bytes]:
        files = {}
        for scope in contract.source_scopes:
            data = Path(scope.path).read_bytes()
            if raw_digest(data) != scope.sha256:
                raise IntegrityError("declared source changed: " + scope.name)
            files[scope.name + Path(scope.path).suffix] = data
        return files

    def _evidence_inputs(
        self, refs: tuple[str, ...]
    ) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
        inputs, manifest = {}, []
        for ref in refs:
            value = self.store.artifacts.get(ref)
            if value.get("kind") == "ObservedTable":
                table = ObservedTable.model_validate(value)
                name = f"{table.name}-{ref[7:15]}.{table.format}"
                inputs[name] = self.store.blobs.get(table.blob_ref)
                manifest.append(
                    {
                        "path": "inputs/" + name,
                        "ref": ref,
                        "artifact_ref": ref,
                        "blob_ref": table.blob_ref,
                        "identity_semantics": "artifact_ref/ref hash the canonical ObservedTable metadata; blob_ref hashes the delivered file bytes. Compare file SHA-256 only with blob_ref; cite the artifact ref as evidence.",
                        "columns": table.columns,
                        "rows": table.rows,
                        "description": table.description,
                    }
                )
            else:
                name = f"{str(value.get('kind', 'record')).lower()}-{ref[7:15]}.json"
                inputs[name] = json.dumps(value, ensure_ascii=False, indent=1).encode()
                manifest.append(
                    {
                        "path": "inputs/" + name,
                        "ref": ref,
                        "kind": value.get("kind"),
                        "format": "json",
                    }
                )
        return inputs, manifest

    def _analyze(
        self, owner: str, child: str, index: int, action: AnalyzeAction
    ) -> tuple[dict[str, Any], dict[str, list[str]]]:
        spec = self.spec(owner)
        contract = contract_of(self.store, spec)
        inputs, manifest = self._evidence_inputs(action.input_refs)
        skill_files, skill_manifest = self._skill_files(spec)
        workspace = prepare_workspace(
            self.workdir(child) / "workspace",
            inputs=inputs,
            sources=self._sources(contract),
            base={},
            skills=skill_files,
        )

        def verify_delivery(step: WorkerStep, finish_index: int) -> dict[str, Any]:
            assert contract.analysis_delivery is not None
            errors = []
            if not step.final_script or not step.final_script.endswith(".py"):
                errors.append(
                    "Provide final_script: a Python script regenerating all output_files."
                )
            tables = [name for name in step.output_files if Path(name).suffix in {".csv", ".json"}]
            if not step.output_files or (contract.analysis_delivery.require_tables and not tables):
                errors.append("Provide at least one CSV/JSON evidence table in output_files.")
            if errors:
                return {"status": "INVALID_DELIVERY", "corrections": errors}
            reproduction = self._rerun(
                child, workspace, step.model_dump(mode="json"), attempt=finish_index
            )
            passed = (
                reproduction.get("status") == "PASS" and reproduction.get("all_identical") is True
            )
            return {
                "status": "OK"
                if passed or not contract.analysis_delivery.require_reproduction
                else "NONREPRODUCIBLE_OUTPUTS",
                "reproducible": reproduction,
                "corrections": []
                if passed
                else [
                    "Fix final_script in this workspace so a clean rerun regenerates every declared output identically."
                ],
            }

        result = run_workspace_task(
            self.store,
            child,
            task={
                "kind": "analysis",
                "available_skills": skill_manifest,
                "question": action.question,
                "rationale": action.rationale,
                "expected_observation": action.expected_observation,
                "inputs": manifest,
                "source_manifest": [
                    {
                        "name": scope.name,
                        "path": "sources/" + scope.name + Path(scope.path).suffix,
                        "sha256": scope.sha256,
                        "validation": "trusted harness checked declared bytes before workspace copy",
                    }
                    for scope in contract.source_scopes
                ],
                "metrics": [m.model_dump(mode="json") for m in contract.metrics],
                "workspace_rules": "Scripts run with the workspace root as the current directory: read inputs/ and sources/, write only under work/. run_python accepts inline code in content (saved to path, default work/step_N.py) or runs an existing work/ script.",
                "deliver": "Save the tables you rely on as CSV under work/, then finish with concrete numeric findings, output_files and final_script (the one script that regenerates them).",
                **(
                    {"delivery_contract": contract.analysis_delivery.model_dump(mode="json")}
                    if contract.analysis_delivery
                    else {}
                ),
            },
            workspace=workspace,
            provider=self.provider(owner, "worker"),
            config=role_config(spec, "worker"),
            workbench=self.executors.workbench,
            max_steps=contract.grants.max_analysis_steps,
            finish_validator=verify_delivery if contract.analysis_delivery else None,
        )
        register: dict[str, list[str]] = {"tables": []}
        outputs = {}
        reproducible = None
        if result["status"] == "FINISHED":
            work = workspace / "work"
            if contract.analysis_delivery:
                reproducible = result["finish_check"].get("reproducible")
            elif result["final_script"]:
                reproducible = self._rerun(child, workspace, result)
            for name in result["output_files"]:
                path = work / name.removeprefix("work/")
                outputs[name] = self.store.blobs.put_file(path)
                if path.suffix in {".csv", ".json"}:
                    register["tables"].append(
                        table_record(
                            self.store,
                            path,
                            name=Path(name).stem[:60].replace(".", "-") or "analysis",
                            description=f"Analysis output for action {index}: {action.question[:300]}",
                            lineage={
                                "action_index": index,
                                "kind": "analysis_output",
                                "inputs": list(action.input_refs),
                            },
                            exposure=contract.exposure,
                        )
                    )
            scripts = {
                str(p.relative_to(work)): self.store.blobs.put_file(p)
                for p in sorted(work.rglob("*.py"))
            }
        else:
            scripts = {}
        return (
            {
                "kind": "AnalysisResult",
                "status": "OBSERVED" if result["status"] == "FINISHED" else result["status"],
                "question": action.question,
                "summary": result.get("summary"),
                "findings": result.get("findings", []),
                "outputs": outputs,
                "output_tables": register["tables"],
                "scripts": scripts,
                "reproducible": reproducible,
                "steps": result["steps"],
                "transcript_owner": child,
                "evidence_level": "descriptive",
                **(
                    {
                        "delivery_status": (
                            "VERIFIED"
                            if result["status"] == "FINISHED"
                            and reproducible
                            and reproducible.get("status") == "PASS"
                            and reproducible.get("all_identical") is True
                            else "UNVERIFIED"
                            if result["status"] == "FINISHED"
                            else "INVALID"
                        ),
                        "finish_check": result.get("finish_check"),
                    }
                    if contract.analysis_delivery
                    else {}
                ),
                "skills_used": sorted(
                    {
                        s["name"]
                        for s in skill_manifest
                        for p in (workspace / "work").rglob("*.py")
                        if f"skills/{s['name']}" in p.read_text(errors="replace")
                    }
                ),
            },
            register,
        )

    def _rerun(
        self, child: str, workspace: Path, result: dict[str, Any], *, attempt: int | None = None
    ) -> dict[str, Any]:
        work = workspace / "work"
        fresh = workspace.parent / "rerun"

        def rerun() -> dict[str, Any]:
            shutil.rmtree(fresh, ignore_errors=True)
            (fresh / "work").mkdir(parents=True, mode=0o700)
            for section in ("inputs", "sources", "base", "skills"):
                shutil.copytree(workspace / section, fresh / section)
            for script in work.rglob("*.py"):
                target = fresh / "work" / script.relative_to(work)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(script, target)
            script = result["final_script"].removeprefix("./")
            run = self.executors.workbench.execute(
                "python",
                [script if script.startswith("work/") else "work/" + script],
                fresh / "work",
                readonly=(fresh, *(fresh / s for s in ("inputs", "sources", "base", "skills"))),
                cwd=fresh,
                log_name="rerun",
            )
            same = {}
            for name in result["output_files"]:
                a = work / name.removeprefix("work/")
                b = fresh / "work" / name.removeprefix("work/")
                same[name] = b.is_file() and file_digest(a) == file_digest(b)
            return {
                "status": run["status"],
                "origin": "trusted_harness",
                "execution": {
                    "clean_workspace": True,
                    "fresh_process_started": run.get("exit_code") is not None,
                    "exit_code": run.get("exit_code"),
                    "script": script if script.startswith("work/") else "work/" + script,
                    "workbench_id": digest(self.executors.workbench.identity()),
                    "scope": "Regeneration of declared analysis outputs; separate from worker self-checks. No engine/backtest repetition or scientific-correctness claim.",
                },
                "outputs_identical": same,
                "all_identical": bool(same) and all(same.values()),
            }

        identity: dict[str, Any] = {"final_script": result["final_script"]}
        if attempt is not None:
            identity.update(
                scripts={
                    str(p.relative_to(work)): file_digest(p) for p in sorted(work.rglob("*.py"))
                },
                outputs={
                    name: file_digest(work / name.removeprefix("work/"))
                    for name in result["output_files"]
                },
            )
        outcome = local_job(
            self.store, child, "rerun" if attempt is None else f"rerun:{attempt}", identity, rerun
        )
        return outcome["value"] or {"status": "ERROR", "error": outcome["error"]}

    def _backtest(
        self, owner: str, child: str, index: int, action: BacktestAction
    ) -> tuple[dict[str, Any], dict[str, list[str]]]:
        assert self.executors.backtester is not None
        contract = contract_of(self.store, self.spec(owner))
        register: dict[str, list[str]] = {"tables": [], "observations": []}
        rows = []
        for experiment in action.experiments:
            resolved = resolve_candidate(
                self.store, experiment.candidate_ref, self.workdir(child) / "candidates"
            )
            requested = setting_dict(experiment.settings)
            defaults = {s.name: s.baseline for s in contract.settings}
            before = {**defaults, **resolved.settings}
            settings = {**before, **requested}
            changes = {k: v for k, v in requested.items() if before.get(k) != v}
            workdir = self.workdir(child) / experiment.label
            outcome = local_job(
                self.store,
                child,
                f"backtest:{experiment.label}",
                {
                    "candidate_ref": experiment.candidate_ref,
                    "settings": settings,
                    "period": experiment.period,
                },
                functools.partial(
                    self.executors.backtester.run, resolved, settings, experiment.period, workdir
                ),
            )
            value = outcome["value"] or {"status": "FAILED", "error": outcome["error"]}
            if (
                contract.analysis_delivery
                and contract.analysis_delivery.capture_submitted_source
                and value.get("status") == "SUCCEEDED"
            ):
                from jarvis.autonomy_adapters import IrisRegionBacktester
                from jarvis.autonomy_source import source_evidence

                if isinstance(self.executors.backtester, IrisRegionBacktester):
                    try:
                        capture = source_evidence(
                            self.executors.backtester,
                            resolved,
                            settings,
                            experiment.period,
                            workdir,
                            value.get("provenance", {}),
                        )
                        value = {**value, "tables": [*value.get("tables", []), capture]}
                    except IntegrityError as exc:
                        value = {**value, "status": "INVALID_EVIDENCE", "error": str(exc)}
            tables = {}
            for table in value.get("tables", []):
                ref = table_record(
                    self.store,
                    Path(table["path"]),
                    name=table["name"],
                    description=f"{table['description']} | experiment {experiment.label}",
                    lineage={
                        "action_index": index,
                        "experiment": experiment.label,
                        "candidate_ref": experiment.candidate_ref,
                        "settings": settings,
                        "period": experiment.period,
                        "evaluator_id": contract.evaluator_id,
                        "dataset_id": contract.dataset_id,
                    },
                    exposure=contract.exposure,
                )
                tables[table["name"]] = ref
                register["tables"].append(ref)
            observation = {
                "kind": "BacktestObservation",
                "label": experiment.label,
                "action_index": index,
                "candidate_ref": experiment.candidate_ref,
                "candidate_origin": resolved.candidate.origin,
                "model_id": resolved.model.model_id if resolved.model else None,
                "data_policy": resolved.candidate.data_policy,
                "settings": settings,
                "changes": changes,
                "period": experiment.period,
                "status": value.get("status", "FAILED"),
                "error": value.get("error"),
                "metrics": value.get("metrics", {}),
                "daily": value.get("daily", {}),
                "tables": tables,
                "evaluator_id": contract.evaluator_id,
                "dataset_id": contract.dataset_id,
                "result_origin": value.get("result_origin", "UNSPECIFIED"),
                "provenance": value.get("provenance", {}),
                "hypothesis": experiment.hypothesis,
                "falsification": experiment.falsification,
            }
            ref = self.store.artifacts.put(observation)
            register["observations"].append(ref)
            rows.append(
                {
                    "result_ref": ref,
                    **{
                        k: observation[k]
                        for k in (
                            "label",
                            "status",
                            "metrics",
                            "changes",
                            "tables",
                            "model_id",
                            "error",
                        )
                    },
                    "provenance": provenance_summary(observation),
                }
            )
        return (
            {
                "kind": "BacktestResult",
                "status": "OBSERVED",
                "design": action.design,
                "experiments": rows,
            },
            register,
        )

    def _train(
        self, owner: str, child: str, index: int, action: TrainModelAction
    ) -> tuple[dict[str, Any], dict[str, list[str]]]:
        assert self.executors.trainer is not None
        base = resolve_candidate(
            self.store, action.base_candidate_ref, self.workdir(child) / "candidates"
        )
        data_path = None
        if action.training_data_ref:
            table = ObservedTable.model_validate(self.store.artifacts.get(action.training_data_ref))
            data_path = self.workdir(child) / f"training-data.{table.format}"
            data_path.write_bytes(self.store.blobs.get(table.blob_ref))
        patch_path = None
        if action.pipeline_patch_ref:
            from jarvis.research_executors import materialize_files

            patch = self.store.artifacts.get(action.pipeline_patch_ref)
            patch_path = materialize_files(
                self.store, patch["files"], self.workdir(child) / "pipeline-patch"
            )
        config = setting_dict(action.config)
        outcome = local_job(
            self.store,
            child,
            "train",
            {
                "base": action.base_candidate_ref,
                "config": config,
                "data": action.training_data_ref,
                "patch": action.pipeline_patch_ref,
            },
            lambda: self.executors.trainer.run(  # type: ignore[union-attr]
                base, config, data_path, patch_path, self.workdir(child) / "train"
            ),
            reserved=None,
        )
        value = outcome["value"] or {"status": "FAILED", "error": outcome["error"]}
        if value.get("status") != "SUCCEEDED":
            return {
                "kind": "TrainingResult",
                "status": "FAILED",
                "error": value.get("error"),
                "detail": value,
            }, {}
        files = {f["name"]: self.store.blobs.put_file(Path(f["path"])) for f in value["files"]}
        source_table = (
            self.store.artifacts.get(action.training_data_ref) if action.training_data_ref else None
        )
        if source_table:
            lineage = source_table["lineage"]
            default_policy = (
                f"trained on table '{source_table['name']}' from experiment {lineage.get('experiment', '?')} "
                f"(candidate {str(lineage.get('candidate_ref', '?'))[7:15]}, settings {json.dumps(lineage.get('settings', {}), sort_keys=True)})"
            )
        else:
            default_policy = "trained on the trainer's declared default data"
        data_policy = value.get("data_policy") or default_policy
        model = ModelArtifact(
            model_id=value["model_id"],
            files=files,
            training_config=config,
            training_data_ref=action.training_data_ref,
            data_policy=data_policy[:600],
            pipeline_patch_ref=action.pipeline_patch_ref,
            validation=value.get("validation", {}),
            origin="agent_trained",
            trainer=self.executors.trainer.identity(),
        )
        model_ref = self.store.artifacts.put(model)
        candidate = Candidate(
            label=action.label,
            parent_ref=action.base_candidate_ref,
            model_ref=model_ref,
            patch_refs=base.candidate.patch_refs,
            settings=base.candidate.settings,
            origin="agent_trained",
            data_policy=data_policy[:600],
            produced_by={"action_index": index, "kind": "TRAIN_MODEL"},
        )
        candidate_ref = self.store.artifacts.put(candidate)
        return (
            {
                "kind": "TrainingResult",
                "status": "SUCCEEDED",
                "model_ref": model_ref,
                "model_id": model.model_id,
                "candidate_ref": candidate_ref,
                "validation": model.validation,
                "data_policy": model.data_policy,
                "config": config,
            },
            {"models": [model_ref], "candidates": [candidate_ref]},
        )

    def _implement(
        self, owner: str, child: str, index: int, action: ImplementAction
    ) -> tuple[dict[str, Any], dict[str, list[str]]]:
        spec = self.spec(owner)
        contract = contract_of(self.store, spec)
        target = next(t for t in contract.implement_targets if t.name == action.target)
        validator = self.executors.validators[action.target]
        base = resolve_candidate(
            self.store, action.base_candidate_ref, self.workdir(child) / "candidates"
        )
        base_files = validator.base_files(target, base)
        if set(base_files) != set(target.files):
            raise IntegrityError("validator base files differ from the declared target files")
        state = self.state(owner)
        context = self.store.artifacts.get(self._decision(state, index)["context_id"])
        visible = set(context["visible_evidence_ids"])
        allowed = self._analyzable(state) & visible
        refs = [r for r in action.evidence_refs if r in allowed]
        # Carry the cited analysis's actual registered tables, not just its prose summary.
        # Never traverse an unregistered reference or expose future/hidden evidence.
        for ref in tuple(refs):
            value = self.store.artifacts.get(ref)
            refs.extend(
                r for r in value.get("output_tables", []) if r in state["tables"] and r in visible
            )
        inputs, input_manifest = self._evidence_inputs(tuple(dict.fromkeys(refs)))
        workspace = prepare_workspace(
            self.workdir(child) / "workspace",
            inputs=inputs,
            sources=self._sources(contract),
            base=base_files,
        )
        work = workspace / "work"

        def check(path: Path) -> dict[str, Any]:
            return validator.validate(target, base, path, self.executors.workbench)

        result = run_workspace_task(
            self.store,
            child,
            task={
                "kind": "implementation",
                "target": target.model_dump(mode="json"),
                "requirement": action.requirement,
                "inputs": input_manifest,
                "rationale": action.rationale,
                "editable_files": ["work/" + f for f in target.files],
                "base_files": ["base/" + f for f in target.files],
                "workspace_rules": "Scripts run with the workspace root as the current directory; only work/ is writable. base/ holds the original files.",
                "deliver": "Edit only the editable files, run validate until it passes, then finish with a summary of the change and output_files listing the edited files.",
            },
            workspace=workspace,
            provider=self.provider(owner, "worker"),
            config=role_config(spec, "worker"),
            workbench=self.executors.workbench,
            max_steps=contract.grants.max_analysis_steps,
            validator=check,
        )
        extra = sorted(
            str(p.relative_to(work))
            for p in work.rglob("*")
            if p.is_file() and str(p.relative_to(work)) not in target.files and p.suffix != ".py"
        )
        final = local_job(
            self.store, child, "final-validation", {"target": target.name}, lambda: check(work)
        )
        validation = final["value"] or {"status": "FAIL", "error": final["error"]}
        changed = {
            f: (work / f).read_bytes()
            for f in target.files
            if (work / f).is_file() and (work / f).read_bytes() != base_files[f]
        }
        diff = "\n".join(self._diff(f, base_files[f], changed[f]) for f in sorted(changed))
        if result["status"] != "FINISHED" or validation.get("status") != "PASS" or not changed:
            return (
                {
                    "kind": "ImplementationResult",
                    "status": "REJECTED",
                    "worker_status": result["status"],
                    "rejection_reasons": [
                        reason
                        for reason, failed in (
                            ("WORKER_NOT_FINISHED", result["status"] != "FINISHED"),
                            ("VALIDATION_FAILED", validation.get("status") != "PASS"),
                            ("NO_CHANGED_TARGET_FILES", not changed),
                        )
                        if failed
                    ],
                    "summary": result.get("summary"),
                    "findings": result.get("findings", []),
                    "finish_check": result.get("finish_check"),
                    "input_refs": list(dict.fromkeys(refs)),
                    "validation": validation,
                    "changed_files": sorted(changed),
                    "diff": diff[:20000],
                    "untracked_outputs": extra[:20],
                    "transcript_owner": child,
                },
                {},
            )
        files = {f: self.store.blobs.put((work / f).read_bytes()) for f in target.files}
        patch = CodePatch(
            target=target.name,
            target_kind=target.kind,
            files=files,
            diff_ref=self.store.blobs.put(diff.encode()),
            validation=validation,
        )
        patch_ref = self.store.artifacts.put(patch)
        register: dict[str, list[str]] = {"patches": [patch_ref]}
        candidate_ref = None
        if target.kind == "strategy_patch":
            candidate = Candidate(
                label=action.label,
                parent_ref=action.base_candidate_ref,
                model_ref=base.candidate.model_ref,
                patch_refs=(
                    *[
                        p
                        for p in base.candidate.patch_refs
                        if self.store.artifacts.get(p)["target"] != target.name
                    ],
                    patch_ref,
                ),
                settings=base.candidate.settings,
                origin="agent_implemented",
                data_policy=base.candidate.data_policy,
                produced_by={"action_index": index, "kind": "IMPLEMENT", "target": target.name},
            )
            candidate_ref = self.store.artifacts.put(candidate)
            register["candidates"] = [candidate_ref]
        return (
            {
                "kind": "ImplementationResult",
                "status": "ACCEPTED",
                "summary": result.get("summary"),
                "patch_ref": patch_ref,
                "candidate_ref": candidate_ref,
                "input_refs": list(dict.fromkeys(refs)),
                "validation": validation,
                "changed_files": sorted(changed),
                "diff": diff[:20000],
                "transcript_owner": child,
            },
            register,
        )

    @staticmethod
    def _diff(name: str, old: bytes, new: bytes) -> str:
        import difflib

        return "".join(
            difflib.unified_diff(
                old.decode(errors="replace").splitlines(keepends=True),
                new.decode(errors="replace").splitlines(keepends=True),
                fromfile="base/" + name,
                tofile="candidate/" + name,
            )
        )

    # ------------------------------------------------------------------ synchronous actions
    def _execute_sync(self, owner: str, index: int, decision: AutonomyDecision) -> dict[str, Any]:
        action = decision.action
        contract = contract_of(self.store, self.spec(owner))
        if isinstance(action, ReadSourceAction):
            sources = {}
            for scope in contract.source_scopes:
                if scope.name in action.scopes:
                    data = Path(scope.path).read_bytes()
                    if raw_digest(data) != scope.sha256:
                        raise IntegrityError("declared source changed: " + scope.name)
                    sources[scope.name] = {
                        "kind": scope.kind,
                        "description": scope.description,
                        "sha256": scope.sha256,
                        "bytes": len(data),
                        "content": data[:SOURCE_LIMIT].decode(errors="replace"),
                        "truncated": len(data) > SOURCE_LIMIT,
                    }
            return {"kind": "SourceReading", "status": "OBSERVED", "sources": sources}
        if isinstance(action, CompareAction):
            state = self.state(owner)

            def row(ref: str) -> dict[str, Any]:
                return {**self.store.artifacts.get(ref), "result_ref": ref}

            comparison = compare_results(
                contract, row(action.baseline_result_ref), [row(r) for r in action.result_refs]
            )
            if contract.stages:
                period = comparison["period"]
                comparison["research_stage"] = (
                    "selection"
                    if period == contract.stages.selection
                    else "confirmation"
                    if period in contract.stages.confirmation
                    else "screening"
                )
                comparison["eligible_for_completion"] = period == contract.stages.selection
            del state
            return {
                **comparison,
                "status": "OBSERVED",
                "finalist_by_declared_rule": select_finalist(contract, comparison),
            }
        if isinstance(action, ConsultAction):
            context = {
                **self.director_context(owner, self.state(owner)),
                "specialist": action.specialist,
                "question": action.question,
            }
            pauses = self._pauses(owner, "consultant", index)
            if pauses:
                context["provider_retry"] = pauses
            try:
                opinion = evidence_role(
                    self.store,
                    owner,
                    f"consult:{index}" + (f":q{pauses}" if pauses else ""),
                    "autonomy_consultant",
                    context,
                    ExpertOpinion,
                    self.provider(owner, "consultant"),
                    role_config(self.spec(owner), "consultant"),
                    "",
                )
            except JarvisError as exc:
                if is_quota_refusal(exc):
                    raise ProviderUnavailable(str(exc)) from exc
                if isinstance(exc, BudgetExceeded) or not (
                    isinstance(exc, AdmissionError)
                    or settle_ended_provider_calls(self.store, owner)
                ):
                    raise
                return {
                    "kind": "Consultation",
                    "status": "FAILED",
                    "specialist": action.specialist,
                    "error": str(exc)[:500],
                    "measured_result": False,
                }
            return {
                "kind": "Consultation",
                "status": "ADVISORY",
                "specialist": action.specialist,
                "opinion": opinion.model_dump(mode="json"),
                "measured_result": False,
            }
        if isinstance(action, WaitAction):
            return {"kind": "Wait", "status": "WAITED", "running": self.state(owner)["running"]}
        assert isinstance(action, StopAction)
        return {
            "kind": "Stop",
            "status": "CHECKPOINT" if action.scope == "checkpoint" else "STOPPED",
            "scope": action.scope,
            "conclusion": action.conclusion,
            "closures": [c.model_dump(mode="json") for c in action.closures],
            "finalist_result_ref": action.finalist_result_ref,
        }

    # ------------------------------------------------------------------ review
    def _carry_rejection(self, owner: str, prefix: str, context: dict[str, Any]) -> None:
        rejected = [
            p
            for p in payloads(self.store, owner, "LAB_ROLE_REJECTED")
            if p["key"].startswith(prefix)
        ]
        if rejected:
            context["recovery_feedback"] = correction_context(self.store, rejected[-1])

    def _role_failure_stop(
        self, owner: str, role: str, index: int, failures: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        counts = {
            kind: sum(failure_category(f) == kind for f in failures)
            for kind in ("invalid_decision", "provider_ended")
        }
        controls = self.spec(owner).runtime
        limits = {
            "invalid_decision": MAX_CONSECUTIVE_FAILURES,
            "provider_ended": controls.provider_retries + 1
            if controls
            else MAX_CONSECUTIVE_FAILURES,
        }
        exhausted = next((kind for kind in limits if counts[kind] >= limits[kind]), None)
        if exhausted is None:
            return None
        code = (
            "PROVIDER_RETRY_EXHAUSTED"
            if exhausted == "provider_ended"
            else "DECISION_REPAIR_EXHAUSTED"
        )
        reason = f"{role}: {code}; provider timeouts and invalid proposals have separate finite allowances"
        self.store.append(
            owner,
            "SESSION_PARTIAL_STOP",
            {
                "reason": reason,
                "code": code,
                "role": role,
                "index": index,
                "failure_counts": counts,
                "failure_limits": limits,
                "scientific_conclusion": "NOT_REACHED",
            },
        )
        self.store.set_status(owner, "NEEDS_ATTENTION")
        return {"status": "NEEDS_ATTENTION", "reason": reason, "code": code}

    def _review(self, owner: str, state: dict[str, Any], index: int) -> dict[str, Any]:
        failures = [p for p in payloads(self.store, owner, "CRITIC_FAILURE") if p["index"] == index]
        attempt = len(failures)
        stopped = self._role_failure_stop(owner, "critic", index, failures)
        if stopped:
            return stopped
        spec = self.spec(owner)
        contract = contract_of(self.store, spec)
        completed = state["completed"][index]
        shown = self._memory_shown(contract, state)
        hidden = set(shown["all"]) - set(shown["refs"])
        evidence = None
        if contract.analysis_delivery and contract.analysis_delivery.review_evidence:
            from jarvis.autonomy_evidence import build_review_bundle

            packed = state["review_evidence"].get(index)
            if packed is None:
                evidence = build_review_bundle(
                    self.store,
                    owner,
                    index,
                    completed["result_ref"],
                    self._decision(state, index)["action"],
                    state["visible"],
                    state["launched"].get(index),
                )
                evidence_ref = self.store.artifacts.put(evidence)
                self.store.append(
                    owner,
                    "REVIEW_EVIDENCE_PACKED",
                    {
                        "index": index,
                        "result_ref": completed["result_ref"],
                        "evidence_ref": evidence_ref,
                    },
                )
                state = self.state(owner)
            else:
                evidence_ref = packed["evidence_ref"]
                evidence = self.store.artifacts.get(evidence_ref)
        context = {
            "research_contract": {
                k: v
                for k, v in contract.model_dump(mode="json").items()
                if k
                in {
                    "instruction",
                    "objective",
                    "focus",
                    "metrics",
                    "acceptance_profiles",
                    "constraints",
                    "exposure",
                }
            },
            "decision": self._decision(state, index),
            "result_ref": completed["result_ref"],
            "result": self.store.artifacts.get(completed["result_ref"]),
            "research_plan": self.plan(owner, state),
            "research_memory_lessons": clip(
                ((state.get("memory") or {}).get("lessons") or []), 400, 40
            ),
            "owner_standing_guidance": [
                str(g["text"])[:400]
                for g in ((state.get("memory") or {}).get("owner_guidance") or [])[-10:]
            ],
            "earlier_observations": [
                self.summarize(owner, state, i, False)
                for i in sorted(state["completed"])
                if i < index
            ][-8:],
            "visible_evidence_ids": [r for r in state["visible"] if r not in hidden],
            **(
                {"trusted_evidence_ref": evidence_ref, "trusted_evidence": evidence}
                if evidence
                else {}
            ),
            **({"retry_attempt": attempt} if attempt else {}),
            **(
                {"provider_retry": self._pauses(owner, "critic", index)}
                if self._pauses(owner, "critic", index)
                else {}
            ),
        }
        if attempt:
            self._carry_rejection(owner, f"review:{index}:", context)

        def check(value: Record, frozen: dict[str, Any]) -> None:
            assert isinstance(value, AutonomyReview)
            if frozen["result_ref"] not in value.evidence_refs:
                raise ValueError("review must cite the reviewed result")
            known = {
                h["hypothesis_id"]
                for q in frozen["research_plan"]["questions"]
                for h in q["hypotheses"]
            }
            for a in value.assessments:
                if a.hypothesis_id not in known or not set(a.evidence_refs) <= set(
                    frozen["visible_evidence_ids"]
                ):
                    raise ValueError("assessment escaped the hypothesis tree or visible evidence")

        try:
            review = evidence_role(
                self.store,
                owner,
                f"review:{index}:{attempt}"
                + (
                    f":q{self._pauses(owner, 'critic', index)}"
                    if self._pauses(owner, "critic", index)
                    else ""
                ),
                "autonomy_critic",
                context,
                AutonomyReview,
                self.provider(owner, "critic"),
                role_config(spec, "critic"),
                "",
                validate=check,
            )
        except ContextEnvelopeExceeded:
            raise
        except AdmissionError as exc:
            if is_quota_refusal(exc):
                return self._pause_provider(owner, "critic", index, exc)
            self.store.append(
                owner,
                "CRITIC_FAILURE",
                {
                    "index": index,
                    "attempt": attempt,
                    "category": failure_category({"reason": str(exc)}),
                    "reason": str(exc)[:1500],
                },
            )
            return {"status": "CRITIC_RETRY"}
        except JarvisError as exc:
            if not settle_ended_provider_calls(self.store, owner):
                raise
            self.store.append(
                owner,
                "CRITIC_FAILURE",
                {
                    "index": index,
                    "attempt": attempt,
                    "category": "provider_ended",
                    "reason": "provider ended without usage; settled at reservation: "
                    + str(exc)[:500],
                },
            )
            return {"status": "CRITIC_RETRY"}
        self.store.append(
            owner,
            "ACTION_REVIEWED",
            {"index": index, "review_ref": self.store.artifacts.put(review)},
        )
        return {"status": "REVIEWED", "index": index}

    # ------------------------------------------------------------------ runner
    def run(
        self,
        owner: str,
        *,
        max_steps: int = 10000,
        wall_seconds: float = 3600,
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        from jarvis.runtime_delivery import export_exit

        # One coordinator owns a complete run, including final delivery.
        with self.store.campaign_lock(owner, scope="autonomy-run"):
            try:
                result = self._run_loop(
                    owner, max_steps=max_steps, wall_seconds=wall_seconds, on_step=on_step
                )
            except Exception as exc:
                result = {
                    "status": "INFRASTRUCTURE_INTERRUPTED",
                    "error": type(exc).__name__ + ": " + str(exc)[:1000],
                }
            try:
                return {**result, "delivery": export_exit(self.store, owner, result)}
            except Exception as exc:
                return {**result, "delivery_error": type(exc).__name__ + ": " + str(exc)[:1000]}

    def _run_loop(
        self,
        owner: str,
        *,
        max_steps: int = 10000,
        wall_seconds: float = 3600,
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        import time

        started = time.monotonic()
        result: dict[str, Any] = {"status": "NOT_STARTED"}
        for _ in range(max_steps):
            if time.monotonic() - started > wall_seconds:
                return {**result, "status": "RUNNER_LIMIT"}
            try:
                result = self.advance(owner)
            except JarvisError as exc:
                status = self.store.status(owner)
                return {
                    "status": status
                    if status in {"NEEDS_RECONCILIATION", "BUDGET_EXHAUSTED", "NEEDS_ATTENTION"}
                    else "NEEDS_ATTENTION",
                    "error": str(exc)[:500],
                }
            if on_step:
                on_step(result)
            if result["status"] in {
                "CLOSED",
                "BUDGET_EXHAUSTED",
                "NEEDS_RECONCILIATION",
                "NEEDS_ATTENTION",
                "PAUSED",
                "CANCELLED",
                "PROVIDER_UNAVAILABLE",
            }:
                controls = self.spec(owner).runtime
                retries = payloads(self.store, owner, "RUNTIME_PROVIDER_RETRY")
                if (
                    result["status"] == "PROVIDER_UNAVAILABLE"
                    and controls
                    and len(retries) < controls.provider_retries
                    and self.store.get_control(owner) not in {"PAUSED", "CANCELLED"}
                ):
                    delay = controls.provider_backoff_seconds * (len(retries) + 1)
                    self.store.append(
                        owner,
                        "RUNTIME_PROVIDER_RETRY",
                        {
                            "attempt": len(retries) + 1,
                            "backoff_seconds": delay,
                            "reason": "provider refusal; no work or budget is recreated",
                        },
                    )
                    # Poll the owner's stop signal while backing off, bounded to 60s.
                    end = min(started + wall_seconds, time.monotonic() + delay)
                    while time.monotonic() < end:
                        if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                            break
                        time.sleep(min(0.25, max(0, end - time.monotonic())))
                    continue
                if result["status"] in {"CLOSED", "BUDGET_EXHAUSTED"}:
                    self._drain(
                        owner, timeout=min(30, max(0, wall_seconds - (time.monotonic() - started)))
                    )
                if (
                    self.consolidate
                    and result["status"]
                    in {
                        "CLOSED",
                        "BUDGET_EXHAUSTED",
                        "NEEDS_ATTENTION",
                        "PAUSED",
                        "CANCELLED",
                    }
                    and not (
                        result["status"] == "PAUSED" and self.state(owner).get("owner_checkpoint")
                    )
                ):
                    result = {**result, "memory": self.remember(owner)}
                return result
            if result["status"] == "WAITING":
                state = self.state(owner)
                self.runner.wait_any([state["launched"][i] for i in state["running"]], timeout=30)
        return {**result, "status": "RUNNER_LIMIT"}

    def remember(self, owner: str) -> dict[str, Any]:
        """Write this session into the domain knowledge base (facts, lessons, skills)."""
        from jarvis.research_memory import KnowledgeBase

        spec = self.spec(owner)
        config = AgentConfig.model_validate(
            {**default_role_config(), **spec.role_config}.get(
                "curator", {**default_role_config(), **spec.role_config}["critic"]
            )
        )
        if spec.runtime:
            config = config.model_copy(
                update={
                    "max_context_bytes": spec.runtime.max_context_bytes,
                    "max_prompt_bytes": spec.runtime.max_prompt_bytes,
                    "provider_retries": spec.runtime.provider_retries,
                }
            )
        try:
            value = KnowledgeBase(self.store).consolidate(
                owner, provider_factory=self.provider_factory, config=config
            )
        except JarvisError as exc:  # curator refused/unavailable: keep trusted facts at least
            value = KnowledgeBase(self.store).consolidate(owner)
            value = {**value, "curator_error": str(exc)[:300]}
        return {k: (len(v) if isinstance(v, list) else v) for k, v in value.items()}

    def _drain(self, owner: str, *, timeout: float = 30) -> None:
        state = self.state(owner)
        children = [state["launched"][i] for i in state["running"]]
        self.runner.wait_any(children, timeout=timeout)
        state = self.state(owner)
        while state["running"]:
            if not self._collect(owner, state):
                break
            state = self.state(owner)
