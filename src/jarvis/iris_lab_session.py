"""Finite foreground session joining real Research Lab episodes to shared Iris quota.

Old watchers are never resumed. Each stage is immutable and separately resumable;
all preparation roles and reviewed programs execute through the original kernel.
"""

from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.campaign import source_identity
from jarvis.contracts import Budget, IntegrityError, JarvisError, Record
from jarvis.identity import digest
from jarvis.iris_contracts import IrisAuthorization
from jarvis.iris_quota import IrisQuotaPolicy, IrisQuotaSnapshot, QuotaLedger, verify_quota
from jarvis.lab_contracts import LabSpec
from jarvis.lab_memory import pin_board
from jarvis.lab_tools import run_tool
from jarvis.research_lab import ResearchLab, lab_report, payloads
from jarvis.research_queue import queue_tasks
from jarvis.storage import Store


class IrisLabSessionSpec(Record):
    kind: Literal["IrisLabSessionSpec"] = "IrisLabSessionSpec"
    stages: tuple[LabSpec, ...] = Field(min_length=1, max_length=16)
    phases: tuple[Literal["smoke", "development", "retrospective"], ...]
    quota_policy: IrisQuotaPolicy
    owner_instruction: str = Field(min_length=1, max_length=6000)
    model: str
    reasoning_effort: str
    budget: Budget
    shadow_review: bool = False
    propose_capability: bool = False

    @model_validator(mode="after")
    def controls(self) -> "IrisLabSessionSpec":
        if len(self.stages) != len(self.phases):
            raise ValueError("each immutable stage needs an explicit phase")
        seen_development = False
        for phase, stage in zip(self.phases, self.stages, strict=True):
            if phase != "smoke":
                seen_development = True
            elif seen_development:
                raise ValueError("complete all integration smokes before development")
            configs = [stage.role_config, stage.program.agent, stage.program.critic]
            if any(
                c is None or c.model != self.model or c.reasoning_effort != self.reasoning_effort
                for c in configs
            ):
                raise ValueError("all session LLM roles must use the declared model and effort")
            if stage.program.queue.operating_owner is not None:
                raise ValueError("session sets its own quota owner")
        return self


class IrisLabSession:
    def __init__(self, store: Store, lab: ResearchLab | None = None):
        self.store = store
        self.lab = lab or ResearchLab(store)

    def create(self, spec: IrisLabSessionSpec, *, budget_owner: str | None = None) -> str:
        spec = IrisLabSessionSpec.model_validate(
            spec.model_copy(
                update={"stages": tuple(pin_board(self.store, s) for s in spec.stages)}
            ).model_dump(mode="json")
        )
        return self.store.create(
            {
                "kind": "IrisLabSession",
                **({"budget_owner": budget_owner} if budget_owner else {}),
                "spec": spec.model_dump(mode="json"),
                "world_id": digest(spec),
                "source_identity": source_identity(),
                "task": {"budget": spec.budget.model_dump()},
                "provider": self.lab.provider_factory(spec.stages[0].role_config).identity(),
            }
        )

    def refresh_quota(self, owner: str, known_ids: list[str]) -> dict[str, Any]:
        if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
            return {"status": self.store.get_control(owner), "session": owner}
        if self.store.manifest(owner)["source_identity"] != source_identity():
            raise IntegrityError("active session runtime changed")
        spec = IrisLabSessionSpec.model_validate(self.store.manifest(owner)["spec"])
        ledger = QuotaLedger(self.store, owner, spec.quota_policy)
        records = payloads(self.store, owner, "SESSION_QUOTA_REQUESTED")
        completed = payloads(self.store, owner, "SESSION_QUOTA_FINISHED")
        if len(records) == len(completed):
            request: dict[str, Any] = {
                "key": f"quota:{len(records)}",
                "known_ids": sorted(set(known_ids)),
            }
            self.store.append(owner, "SESSION_QUOTA_REQUESTED", request)
        request = payloads(self.store, owner, "SESSION_QUOTA_REQUESTED")[-1]
        value = run_tool(
            self.store,
            owner,
            request["key"],
            request,
            lambda: self.lab.program.queue.transport_factory(
                spec.stages[0].program.queue.environment
            ).quota(
                request["known_ids"],
                self.store.root / "quota-reads" / owner / request["key"].replace(":", "-"),
            ),
        )
        if value.get("status") == "QUOTA_OBSERVED":
            snap = IrisQuotaSnapshot.model_validate(value["snapshot"])
            observed = (
                spec.stages[0].program.queue.environment.result_origin == "OBSERVED_IRIS_BACKTEST"
            )
            if observed != (snap.source == "IRIS_QUEUE_ACK"):
                raise IntegrityError("quota origin mismatches declared backend")
            ledger.observe(snap)
        if not any(
            p["key"] == request["key"]
            for p in payloads(self.store, owner, "SESSION_QUOTA_FINISHED")
        ):
            self.store.append(
                owner,
                "SESSION_QUOTA_FINISHED",
                {"key": request["key"], "result_ref": self.store.artifacts.put(value)},
            )
        return ledger.status()

    def known_requests(self, owner: str) -> list[str]:
        import json
        from pathlib import Path

        spec = IrisLabSessionSpec.model_validate(self.store.manifest(owner)["spec"])
        requests: list[str] = []
        for stage in spec.stages:
            if stage.program.queue.baseline:
                manifest = json.loads(Path(stage.program.queue.baseline.manifest).read_text())
                requests.extend(c["request_id"] for c in manifest["cases"] if c.get("request_id"))
        for member in self.store.resource_members(owner):
            if self.store.manifest(member)["kind"] == "IrisResearchQueue":
                requests.extend(
                    r["request_id"] for r in queue_tasks(self.store, member) if r.get("request_id")
                )
        return list(dict.fromkeys(requests))

    def advance(self, owner: str) -> dict[str, Any]:
        with self.store.campaign_lock(owner, scope="lab-session"):
            if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                return {"status": self.store.get_control(owner), "session": owner}
            if self.store.status(owner) in {
                "CLOSED",
                "NEEDS_RECONCILIATION",
                "SMOKE_NEEDS_ATTENTION",
            }:
                return self.report(owner)
            manifest = self.store.manifest(owner)
            if manifest["source_identity"] != source_identity():
                raise IntegrityError("active session runtime changed")
            spec = IrisLabSessionSpec.model_validate(manifest["spec"])
            if (
                self.lab.provider_factory(spec.stages[0].role_config).identity()
                != manifest["provider"]
            ):
                raise IntegrityError("session provider changed")
            links = payloads(self.store, owner, "SESSION_STAGE_CREATED")
            for i, declared in enumerate(spec.stages):
                known = next((p for p in links if p["index"] == i), None)
                if known is None:
                    completed_experience = [
                        x["experience_ref"]
                        for link in links
                        for x in payloads(self.store, link["episode"], "LAB_EXPERIENCE_CREATED")
                    ]
                    q = declared.program.queue
                    q = q.model_copy(
                        update={
                            "operating_owner": owner,
                            "authorization": IrisAuthorization(
                                environment_id=digest(q.environment),
                                max_submissions=32,
                                expires_at=None,
                                owner_note="Explicit owner research authorization in session "
                                + owner,
                            ),
                        }
                    )
                    episode_spec = declared.model_copy(
                        update={
                            "program": declared.program.model_copy(update={"queue": q}),
                            "experience_refs": tuple(
                                dict.fromkeys([*declared.experience_refs, *completed_experience])
                            ),
                        }
                    )
                    episode = self.lab.create(episode_spec, budget_owner=owner)
                    self.store.append(
                        owner,
                        "SESSION_STAGE_CREATED",
                        {"index": i, "phase": spec.phases[i], "episode": episode},
                    )
                    known = {"index": i, "episode": episode}
                episode = known["episode"]
                if self.store.status(episode) != "CLOSED":
                    try:
                        result = self.lab.advance(episode)
                    except (JarvisError, ValueError):
                        self.store.set_status(owner, "NEEDS_RECONCILIATION")
                        raise
                    if result["status"] != "CLOSED":
                        if result["status"] in {
                            "NEEDS_RECONCILIATION",
                            "BUDGET_EXHAUSTED",
                            "LAB_NEEDS_ATTENTION",
                            "PAUSED",
                            "CANCELLED",
                        }:
                            self.store.set_status(owner, "NEEDS_RECONCILIATION")
                        return {"session": owner, "stage": i, "episode": episode, **result}
                measured = next(
                    (
                        p
                        for p in payloads(self.store, owner, "SESSION_STAGE_VERIFIED")
                        if p["index"] == i
                    ),
                    None,
                )
                if measured is None:
                    report = lab_report(self.store, episode)
                    actual = report["program"]["experiments"]
                    baseline = next(
                        r["result"] for r in actual if r["experiment"]["family"] == "baseline"
                    )
                    interventions = [
                        r
                        for r in actual
                        if r["experiment"]["family"] != "baseline"
                        and r["result"]["status"] == "SUCCEEDED"
                    ]
                    effect = any(
                        any(
                            r["result"][k] != baseline[k]
                            for k in ["entry_only_pl", "count", "daily_pl"]
                        )
                        or r["result"].get("provenance", {}).get("entry_execution_sha256")
                        != baseline.get("provenance", {}).get("entry_execution_sha256")
                        for r in interventions
                    )
                    self.store.append(
                        owner,
                        "SESSION_STAGE_VERIFIED",
                        {
                            "index": i,
                            "report_ref": self.store.artifacts.put(report),
                            "interventions": len(interventions),
                            "observed_effect": effect,
                        },
                    )
                    if spec.phases[i] == "smoke" and not effect:
                        self.store.set_status(owner, "SMOKE_NEEDS_ATTENTION")
                        return {
                            "status": "SMOKE_NEEDS_ATTENTION",
                            "session": owner,
                            "stage": i,
                            "reason": "No measured integration effect; do not assume parameter consumption",
                        }
            if spec.propose_capability and not payloads(
                self.store, owner, "SESSION_CAPABILITY_CANDIDATE"
            ):
                last = payloads(self.store, owner, "SESSION_STAGE_CREATED")[-1]["episode"]
                ref = self.lab.propose_capability(last)
                self.store.append(
                    owner,
                    "SESSION_CAPABILITY_CANDIDATE",
                    {
                        "episode": last,
                        "capability_ref": ref,
                        "status": "FIDELITY_TESTED_UTILITY_UNVERIFIED",
                        "promoted": False,
                    },
                )
            if spec.shadow_review:
                self.shadow_review(owner, spec)
            if not payloads(self.store, owner, "SESSION_COMPLETED"):
                self.store.append(
                    owner,
                    "SESSION_COMPLETED",
                    {
                        "reason": "DECLARED_STAGES_COMPLETED",
                        "dream_status": "INDEPENDENT_HISTORY_COVERAGE_REQUIRES_ASSESSMENT",
                    },
                )
            self.store.set_status(owner, "CLOSED")
            return self.report(owner)

    def shadow_review(self, owner: str, spec: IrisLabSessionSpec) -> None:
        from jarvis.lab_contracts import MethodCandidates, PolicyCandidates
        from jarvis.research_roles import RoleCaller, strict_schema

        links = payloads(self.store, owner, "SESSION_STAGE_CREATED")
        if not payloads(self.store, owner, "SESSION_DREAM_COVERAGE"):
            families = sorted({stage.family for stage in spec.stages})
            self.store.append(
                owner,
                "SESSION_DREAM_COVERAGE",
                {
                    "completed_lab_histories": len(links),
                    "declared_task_families": families,
                    "independent_validation_available": False,
                    "promotion_eligible": False,
                    "reason": "Exposed development study; independent heldout/fresh/canary and distinct-history coverage required",
                },
            )
        review_models: tuple[tuple[str, type[MethodCandidates] | type[PolicyCandidates]], ...] = (
            ("method", MethodCandidates),
            ("policy", PolicyCandidates),
        )
        for axis, model in review_models:
            if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                raise IntegrityError("owner stopped session before shadow review")
            if any(p["axis"] == axis for p in payloads(self.store, owner, "SESSION_SHADOW_REVIEW")):
                continue
            histories = []
            for link in links:
                report = lab_report(self.store, link["episode"])
                histories.append(
                    {
                        "episode": link["episode"],
                        "phase": spec.phases[link["index"]],
                        "program": report["program"],
                        "usage": report["full_usage"],
                    }
                )
            context = {
                "axis": axis,
                "cycle": 1,
                "incumbent": getattr(spec.stages[0], axis).model_dump(mode="json"),
                "development_only": histories,
                "promotion_eligible": False,
                "goal": "Review cowork/search/experiment failures and propose bounded changes for a future independently validated study. Current task is PL with PR/risk constraints; do not claim Dream effectiveness.",
            }
            response = RoleCaller(
                self.store,
                self.lab.provider_factory(spec.stages[0].role_config),
                spec.stages[0].role_config,
            ).call(
                owner,
                "shadow_" + axis + "_developer",
                context,
                strict_schema(model),
                role_budget=True,
                guidance="Development-only retrospective proposal. No promotion, evaluator/permission/budget changes, or hidden validation access. Distinguish actual observations from hypotheses.",
            )
            answer = model.model_validate(response["answer"])
            if answer.context_id != digest(context):
                raise IntegrityError("shadow review used stale context")
            self.store.append(
                owner,
                "SESSION_SHADOW_REVIEW",
                {
                    "axis": axis,
                    "context_ref": self.store.artifacts.put(context),
                    "answer_ref": self.store.artifacts.put(answer),
                    "receipt_ref": response["receipt_ref"],
                    "status": "PROPOSED_NOT_VALIDATED",
                    "promoted": False,
                },
            )

    def report(self, owner: str) -> dict[str, Any]:
        spec = IrisLabSessionSpec.model_validate(self.store.manifest(owner)["spec"])
        links = payloads(self.store, owner, "SESSION_STAGE_CREATED")
        if [p["index"] for p in links] != list(range(len(links))):
            raise IntegrityError("session stage lineage duplicated or reordered")
        prior_experience: list[str] = []
        for link in links:
            stage = spec.stages[link["index"]]
            actual = LabSpec.model_validate(self.store.manifest(link["episode"])["spec"])
            original_q = stage.program.queue
            grant = IrisAuthorization(
                environment_id=digest(original_q.environment),
                max_submissions=32,
                expires_at=None,
                owner_note="Explicit owner research authorization in session " + owner,
            )
            expected = stage.model_copy(
                update={
                    "program": stage.program.model_copy(
                        update={
                            "queue": original_q.model_copy(
                                update={"operating_owner": owner, "authorization": grant}
                            )
                        }
                    ),
                    "experience_refs": tuple(
                        dict.fromkeys([*stage.experience_refs, *prior_experience])
                    ),
                }
            )
            if (
                actual != expected
                or self.store.manifest(link["episode"]).get("budget_owner") != owner
            ):
                raise IntegrityError("session child escaped task/model/cost scope")
            prior_experience.extend(
                p["experience_ref"]
                for p in payloads(self.store, link["episode"], "LAB_EXPERIENCE_CREATED")
            )
            if self.store.status(link["episode"]) == "CLOSED":
                lab_report(self.store, link["episode"])
        if self.store.status(owner) == "CLOSED" and (
            len(links) != len(spec.stages)
            or any(self.store.resource_totals_tree(owner)["pending"].values())
        ):
            raise IntegrityError("closed session omitted stages or has pending work")
        return {
            "session": owner,
            "status": self.store.status(owner),
            "stages": payloads(self.store, owner, "SESSION_STAGE_CREATED"),
            "verified_stages": payloads(self.store, owner, "SESSION_STAGE_VERIFIED"),
            "usage": self.store.resource_totals_tree(owner),
            "quota": QuotaLedger(self.store, owner, spec.quota_policy).status(),
            "quota_verification": verify_quota(self.store, owner, spec.quota_policy),
        }


def run_session_fixture(store: Store) -> dict[str, Any]:
    from jarvis.lab_fixture import fixture_lab_engine, fixture_lab_spec
    from jarvis.researcher import AgentConfig

    lab, _ = fixture_lab_engine(store)
    config = AgentConfig(
        model="gpt-6.1-sol", reasoning_effort="high", reserve_tokens=1000, max_calls=16
    )
    task = fixture_lab_spec(store)
    task = task.model_copy(
        update={
            "role_config": config,
            "program": task.program.model_copy(update={"agent": config, "critic": config}),
        }
    )
    spec = IrisLabSessionSpec(
        stages=(task, task.model_copy(update={"task_id": "following-fixture"})),
        phases=("smoke", "development"),
        quota_policy=IrisQuotaPolicy(scope="declared-session-fixture"),
        owner_instruction="Declared synthetic engineering acceptance only",
        model="gpt-6.1-sol",
        reasoning_effort="high",
        budget=Budget(
            max_actions=1000,
            max_tool_calls=1000,
            max_llm_tokens=100000,
            max_compute_units=1000,
            max_wall_seconds=10000,
            max_parallel_jobs=2,
        ),
        shadow_review=True,
        propose_capability=True,
    )
    engine = IrisLabSession(store, lab)
    owner = engine.create(spec)
    engine.refresh_quota(owner, [])
    for _ in range(100):
        report = engine.advance(owner)
        if report["status"] == "CLOSED":
            return {
                **report,
                "hermes_research": "NOT_STARTED",
                "external_llm_calls": 0,
                "remote_iris_submissions": 0,
                "token_usage_origin": "SIMULATED_TOKEN_UNITS",
            }
        if report["status"] in {"NEEDS_RECONCILIATION", "SMOKE_NEEDS_ATTENTION"}:
            raise IntegrityError("synthetic session acceptance stopped")
    raise IntegrityError("bounded synthetic session did not complete")
