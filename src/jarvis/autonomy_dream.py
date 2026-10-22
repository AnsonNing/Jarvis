"""Dream-RSI over autonomous research sessions: improve HOW Jarvis researches.

Round:
  1. development sessions under the active policy -> sealed history pool
  2. developer role proposes bounded ResearchPolicy candidates from development only
  3. exact-replay screen: where would each candidate have rejected a recorded decision?
     (screen only; divergent branches are INSUFFICIENT_COVERAGE, never estimated)
  4. paired fresh validation sessions (incumbent vs candidate, same budget) on
     disjoint tasks; utility is measured by a hidden trusted holdout evaluation
  5. adopt in the policy registry only if validation passes; then a fresh canary
     task; regression rolls the registry back. All new histories join the pool.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from jarvis.autonomy_contracts import AutonomySessionSpec
from jarvis.autonomy_policy import ResearchPolicy, enforce, q0
from jarvis.autonomy_report import session_outcome, verify_session
from jarvis.bounded_roles import evidence_role
from jarvis.campaign import code_provenance, source_identity
from jarvis.contracts import AdmissionError, Budget, IntegrityError, Record
from jarvis.identity import digest
from jarvis.research_contract import Name, Ref
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


class PolicyProposalBatch(Record):
    context_id: Ref
    analysis: str = Field(min_length=1, max_length=4000)
    candidates: tuple[ResearchPolicy, ...] = Field(min_length=1, max_length=3)
    evidence_refs: tuple[Ref, ...] = Field(min_length=1, max_length=16)


class DreamTask(Record):
    task_id: Name
    family: str = Field(min_length=1, max_length=80)
    seed: int


class AutonomyDreamSpec(Record):
    kind: Literal["AutonomyDreamSpec"] = "AutonomyDreamSpec"
    registry: str = Field(min_length=1)
    development: tuple[DreamTask, ...] = Field(default=(), max_length=8)
    # Learn from research that already happened (real sessions in this store) instead of,
    # or in addition to, fresh development tasks.
    development_sessions: tuple[str, ...] = Field(default=(), max_length=64)
    validation: tuple[DreamTask, ...] = Field(min_length=1, max_length=8)
    canary: tuple[DreamTask, ...] = Field(min_length=1, max_length=4)
    session_max_decisions: int = Field(default=30, ge=4, le=500)
    session_budget: Budget = Budget(
        max_actions=200,
        max_tool_calls=200,
        max_llm_tokens=2000000,
        max_wall_seconds=36000,
        max_compute_units=1000,
        max_parallel_jobs=8,
    )
    developer: AgentConfig = AgentConfig(
        model="gpt-6.1-sol",
        reasoning_effort="high",
        max_calls=8,
        reserve_tokens=120000,
        timeout_seconds=900,
    )
    min_mean_gain: float = Field(default=0.01, ge=0)
    canary_tolerance: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def disjoint(self) -> "AutonomyDreamSpec":
        if not self.development and not self.development_sessions:
            raise ValueError("Dream needs development tasks or existing development sessions")
        groups = [self.development, self.validation, self.canary]
        ids = [t.task_id for g in groups for t in g]
        seeds = [(t.family, t.seed) for g in groups for t in g]
        if len(ids) != len(set(ids)) or len(seeds) != len(set(seeds)):
            raise ValueError("development, validation and canary tasks must be disjoint")
        return self


class TaskFactory(Protocol):
    """Builds a research task world. `holdout` is hidden from every research role."""

    def identity(self) -> dict[str, Any]: ...

    def build(self, store: Store, task: DreamTask, root: Path) -> dict[str, Any]:
        """Return contract_ref, executors and holdout(candidate_ref, settings) -> metrics."""
        ...


class PolicyRegistry:
    """Scoped, append-only active research-policy registry with explicit rollback."""

    def __init__(self, store: Store):
        self.store = store

    def create(self, initial: ResearchPolicy | None = None, *, scope: str = "autonomy") -> str:
        ref = self.store.artifacts.put(initial or q0())
        registry = self.store.create(
            {
                "kind": "AutonomyPolicyRegistry",
                "scope": scope,
                "world_id": digest({"scope": scope, "initial": ref}),
                "task": {"budget": Budget().model_dump()},
            }
        )
        self.store.append(
            registry,
            "POLICY_ACTIVATED",
            {"policy_ref": ref, "reason": "initial incumbent", "proof_ref": None},
        )
        return registry

    def history(self, registry: str) -> list[dict[str, Any]]:
        return [
            {**e["payload"], "event": e["event_type"], "sequence": e["sequence"]}
            for e in self.store.events(registry)
            if e["event_type"] in {"POLICY_ACTIVATED", "POLICY_ROLLED_BACK"}
        ]

    def active(self, registry: str) -> str:
        return str(self.history(registry)[-1]["policy_ref"])

    def activate(self, registry: str, policy_ref: str, reason: str, proof_ref: str) -> None:
        ResearchPolicy.model_validate(self.store.artifacts.get(policy_ref))
        self.store.artifacts.get(proof_ref)
        self.store.append(
            registry,
            "POLICY_ACTIVATED",
            {"policy_ref": policy_ref, "reason": reason, "proof_ref": proof_ref},
        )

    def rollback(self, registry: str, restore_ref: str, proof_ref: str) -> None:
        if restore_ref not in {h["policy_ref"] for h in self.history(registry)}:
            raise IntegrityError("rollback target was never active")
        self.store.append(
            registry,
            "POLICY_ROLLED_BACK",
            {"policy_ref": restore_ref, "from_ref": self.active(registry), "proof_ref": proof_ref},
        )


def _payloads(store: Store, owner: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in store.events(owner) if e["event_type"] == kind]


def utility(outcome: dict[str, Any], holdout: dict[str, Any] | None) -> float:
    """Relative holdout gain of the declared-rule finalist on its profile's first objective."""
    if not holdout or holdout.get("status") != "EVALUATED":
        return 0.0
    base, cand = holdout["baseline"][holdout["metric"]], holdout["finalist"][holdout["metric"]]
    sign = 1 if holdout["direction"] == "maximize" else -1
    return sign * (cand - base) / abs(base) if base else sign * (cand - base)


class AutonomyDream:
    def __init__(
        self,
        store: Store,
        factory: TaskFactory,
        provider_factory: Callable[[AgentConfig, str], Any],
        *,
        session_engine: Callable[[Store, Any, Callable[[AgentConfig, str], Any]], Any]
        | None = None,
    ):
        from jarvis.autonomous_research import AutonomousResearch

        self.store = store
        self.factory = factory
        self.provider_factory = provider_factory
        self.session_engine = session_engine or (lambda s, e, p: AutonomousResearch(s, e, p))
        self.registry = PolicyRegistry(store)

    def create(self, spec: AutonomyDreamSpec) -> str:
        if self.store.manifest(spec.registry)["kind"] != "AutonomyPolicyRegistry":
            raise AdmissionError("registry must be an AutonomyPolicyRegistry")
        developer = self.provider_factory(spec.developer, "policy").identity()
        return self.store.create(
            {
                "kind": "AutonomyDreamRound",
                "spec": spec.model_dump(mode="json"),
                "world_id": digest(
                    {
                        "spec": spec.model_dump(mode="json"),
                        "factory": self.factory.identity(),
                        "developer": developer,
                    }
                ),
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
                "developer": developer,
                "incumbent_ref": self.registry.active(spec.registry),
                "task": {
                    "budget": Budget(
                        max_actions=100000,
                        max_tool_calls=100000,
                        max_llm_tokens=200000000,
                        max_wall_seconds=10**7,
                        max_compute_units=10**6,
                        max_parallel_jobs=16,
                    ).model_dump()
                },
            }
        )

    def spec(self, round_id: str) -> AutonomyDreamSpec:
        return AutonomyDreamSpec.model_validate(self.store.manifest(round_id)["spec"])

    # ------------------------------------------------------------------ sessions
    def _session(
        self, round_id: str, cohort: str, task: DreamTask, policy_ref: str
    ) -> dict[str, Any]:
        key = {"cohort": cohort, "task_id": task.task_id, "policy_ref": policy_ref}
        done = [
            p
            for p in _payloads(self.store, round_id, "DREAM_SESSION_OUTCOME")
            if {k: p[k] for k in key} == key
        ]
        if done:
            return dict(self.store.artifacts.get(done[0]["outcome_ref"]))
        root = (
            self.store.root
            / "dream-worlds"
            / round_id
            / cohort
            / f"{task.task_id}-{policy_ref[7:15]}"
        )
        world = self.factory.build(self.store, task, root)
        engine = self.session_engine(self.store, world["executors"], self.provider_factory)
        created = [
            p
            for p in _payloads(self.store, round_id, "DREAM_SESSION_CREATED")
            if {k: p[k] for k in key} == key
        ]
        spec = self.spec(round_id)
        if created:
            session = created[0]["session"]
        else:
            session = engine.create(
                AutonomySessionSpec(
                    contract_ref=world["contract_ref"],
                    policy_ref=policy_ref,
                    budget=spec.session_budget,
                    max_decisions=spec.session_max_decisions,
                    max_concurrent_actions=1,
                ),
                budget_owner=round_id,
            )
            self.store.append(round_id, "DREAM_SESSION_CREATED", {**key, "session": session})
        status = engine.run(session, wall_seconds=spec.session_budget.max_wall_seconds)
        if status["status"] not in {"CLOSED", "BUDGET_EXHAUSTED", "NEEDS_ATTENTION"}:
            # Unknown paid work or a runner limit is never scored as a policy outcome.
            raise IntegrityError(f"Dream session did not settle: {status}")
        audit = verify_session(self.store, session)
        outcome = session_outcome(self.store, session)
        holdout = None
        finalist = outcome.get("finalist", {})
        if finalist.get("status") == "SELECTED":
            contract = self.store.artifacts.get(world["contract_ref"])
            profile = next(
                p for p in contract["acceptance_profiles"] if p["name"] == finalist["profile"]
            )
            metric = profile["objectives"][0]["metric"]
            direction = next(m["direction"] for m in contract["metrics"] if m["name"] == metric)
            obs = self.store.artifacts.get(finalist["result_ref"])
            base = self.store.artifacts.get(outcome["baseline_result_ref"])
            holdout = {
                "status": "EVALUATED",
                "metric": metric,
                "direction": direction,
                "finalist": world["holdout"](obs["candidate_ref"], obs["settings"]),
                "baseline": world["holdout"](base["candidate_ref"], base["settings"]),
                "scope": "trusted holdout period hidden from every research role",
            }
        value = {
            **key,
            "session": session,
            "session_status": status["status"],
            "audit": audit["validation"],
            "outcome_status": outcome["status"],
            "finalist": finalist,
            "development_gain": self._dev_gain(outcome),
            "holdout": holdout,
            "utility": utility(outcome, holdout),
            "decisions": outcome["decisions"],
            "usage": outcome["usage"]["actual"],
        }
        self.store.append(
            round_id,
            "DREAM_SESSION_OUTCOME",
            {**key, "session": session, "outcome_ref": self.store.artifacts.put(value)},
        )
        return value

    def _existing(self, session: str) -> dict[str, Any]:
        """A finished real session as a development history (no holdout is claimed)."""
        if self.store.manifest(session).get("kind") != "AutonomousResearchSession":
            raise AdmissionError("development sessions must be autonomous research sessions")
        if self.store.status(session) not in {"CLOSED", "BUDGET_EXHAUSTED", "NEEDS_ATTENTION"}:
            raise AdmissionError("development sessions must have finished")
        verify_session(self.store, session)
        outcome = session_outcome(self.store, session)
        return {
            "cohort": "development",
            "task_id": self.store.artifacts.get(
                self.store.manifest(session)["spec"]["contract_ref"]
            )["task_id"],
            "policy_ref": self.store.manifest(session)["spec"].get("policy_ref"),
            "session": session,
            "development_gain": self._dev_gain(outcome)
            if outcome["status"] == "COMPARED"
            else None,
            "decisions": outcome["decisions"],
            "usage": outcome["usage"]["actual"],
            "utility": None,
            "source": "existing session",
        }

    @staticmethod
    def _dev_gain(outcome: dict[str, Any]) -> float | None:
        finalist = outcome.get("finalist", {})
        if finalist.get("status") != "SELECTED":
            return None
        row = next(
            r for r in outcome["comparison"]["rows"] if r["result_ref"] == finalist["result_ref"]
        )
        profile = next(p for p in row["profiles"] if p["profile"] == finalist["profile"])
        return float(profile["objectives"][0]["gain"])

    # ------------------------------------------------------------------ history and proposals
    def _history_pool(self, round_id: str, outcomes: list[dict[str, Any]]) -> str:
        sealed = _payloads(self.store, round_id, "HISTORY_POOL_SEALED")
        if sealed:
            return str(sealed[0]["pool_ref"])
        sessions = []
        for o in outcomes:
            decisions = _payloads(self.store, o["session"], "AUTONOMY_DECISION")
            kinds = [
                self.store.artifacts.get(p["decision_ref"])["action"]["kind"] for p in decisions
            ]
            counts: dict[str, int] = {}
            for k in kinds:
                counts[k] = counts.get(k, 0) + 1
            reviews = [
                self.store.artifacts.get(p["review_ref"])
                for p in _payloads(self.store, o["session"], "ACTION_REVIEWED")
            ]
            sessions.append(
                {
                    "task_id": o["task_id"],
                    "session": o["session"],
                    "action_sequence": kinds,
                    "action_counts": counts,
                    "development_gain": o["development_gain"],
                    "decisions": o["decisions"],
                    "usage": o["usage"],
                    "critic_concerns": sorted({c for r in reviews for c in r["concerns"]})[:12],
                }
            )
        pool = {
            "kind": "AutonomyHistoryPool",
            "cohort": "development",
            "sessions": sessions,
            "scope": "development histories only; validation/canary outcomes are withheld from the developer",
        }
        ref = self.store.artifacts.put(pool)
        self.store.append(round_id, "HISTORY_POOL_SEALED", {"pool_ref": ref})
        return ref

    def _propose(self, round_id: str, pool_ref: str, incumbent_ref: str) -> list[str]:
        recorded = _payloads(self.store, round_id, "POLICY_PROPOSALS_RECORDED")
        if recorded:
            return list(recorded[0]["candidate_refs"])
        spec = self.spec(round_id)
        from jarvis.research_memory import KnowledgeBase, session_domain

        pool = self.store.artifacts.get(pool_ref)
        domains = {session_domain(self.store, s["session"]) for s in pool["sessions"]}
        lessons = [
            x for d in sorted(domains) for x in KnowledgeBase(self.store).snapshot(d)["lessons"]
        ]
        context = {
            "research_memory_lessons": [
                {k: x[k] for k in ("statement", "kind", "confidence")} for x in lessons
            ][:60],
            "development_summary": pool,
            "incumbent_policy": self.store.artifacts.get(incumbent_ref),
            "policy_language": ResearchPolicy.model_json_schema(),
            "boundary": "Propose research-method rules only; no strategy parameters, evaluator, data or held-out results.",
            "visible_evidence_ids": [pool_ref, incumbent_ref],
        }

        def check(answer: Record, frozen: dict[str, Any]) -> None:
            assert isinstance(answer, PolicyProposalBatch)
            names = [c.name for c in answer.candidates]
            if len(names) != len(set(names)):
                raise ValueError("candidate policy names must be unique")
            for c in answer.candidates:
                if c.origin != "agent_proposed":
                    raise ValueError("developer candidates must be agent_proposed")
                if digest(
                    c.model_copy(update={"name": "x", "rationale": "x", "origin": "human_provided"})
                ) == digest(
                    ResearchPolicy.model_validate(frozen["incumbent_policy"]).model_copy(
                        update={"name": "x", "rationale": "x", "origin": "human_provided"}
                    )
                ):
                    raise ValueError("candidate must differ from the incumbent")

        batch = evidence_role(
            self.store,
            round_id,
            "policy-proposals",
            "autonomy_policy",
            context,
            PolicyProposalBatch,
            self.provider_factory(spec.developer, "policy"),
            spec.developer,
            "",
            validate=check,
        )
        assert isinstance(batch, PolicyProposalBatch)
        refs = [self.store.artifacts.put(c) for c in batch.candidates]
        self.store.append(
            round_id,
            "POLICY_PROPOSALS_RECORDED",
            {"batch_ref": self.store.artifacts.put(batch), "candidate_refs": refs},
        )
        return refs

    def screen(
        self,
        round_id: str,
        candidates: list[str],
        outcomes: list[dict[str, Any]],
        incumbent_ref: str,
    ) -> dict[str, Any]:
        """Replay each recorded decision under each candidate; record the first divergence."""
        from jarvis.autonomous_research import AutonomousResearch
        from jarvis.autonomy_contracts import AutonomyDecision

        engine = AutonomousResearch.__new__(AutonomousResearch)
        engine.store = self.store
        engine.executors = type("NoExecutors", (), {"trainer": True})()  # trainer presence only
        rows = []
        incumbent = ResearchPolicy.model_validate(self.store.artifacts.get(incumbent_ref))
        for ref in candidates:
            policy = ResearchPolicy.model_validate(self.store.artifacts.get(ref))
            per_session = []
            for o in outcomes:
                session = o["session"]
                divergence = None
                decisions = [
                    e for e in self.store.events(session) if e["event_type"] == "AUTONOMY_DECISION"
                ]
                for event in decisions:
                    state = engine.state(session, before_sequence=event["sequence"])
                    decision = AutonomyDecision.model_validate(
                        self.store.artifacts.get(event["payload"]["decision_ref"])
                    )
                    try:
                        enforce(policy, engine.policy_state(session, state), decision)
                    except ValueError as exc:
                        divergence = {"index": event["payload"]["index"], "reason": str(exc)[:300]}
                        break
                per_session.append(
                    {
                        "session": session,
                        "decisions": len(decisions),
                        "consistent_prefix": divergence["index"] if divergence else len(decisions),
                        "divergence": divergence,
                        "replay": "COMPLETE" if divergence is None else "INSUFFICIENT_COVERAGE",
                    }
                )
            rows.append(
                {
                    "policy_ref": ref,
                    "name": policy.name,
                    "sessions": per_session,
                    "behaviour_changes": any(s["divergence"] for s in per_session),
                    # Guidance text can change an LLM Director in ways replay cannot see.
                    "guidance_changed": policy.guidance != incumbent.guidance,
                    "requires_fresh_execution": any(s["divergence"] for s in per_session)
                    or policy.guidance != incumbent.guidance,
                }
            )
        return {
            "kind": "PolicyReplayScreen",
            "candidates": rows,
            "external_calls": 0,
            "scope": "Exact replay identifies where behaviour would change; it never estimates unexecuted outcomes.",
        }

    # ------------------------------------------------------------------ round
    def run(self, round_id: str) -> dict[str, Any]:
        with self.store.campaign_lock(round_id, scope="autonomy-dream"):
            closed = _payloads(self.store, round_id, "DREAM_ROUND_CLOSED")
            if closed:
                return dict(closed[0])
            spec = self.spec(round_id)
            incumbent = self.store.manifest(round_id)["incumbent_ref"]
            if self.registry.active(spec.registry) != incumbent:
                raise IntegrityError("registry changed since this round was declared")
            dev = [self._session(round_id, "development", t, incumbent) for t in spec.development]
            dev += [self._existing(s) for s in spec.development_sessions]
            pool = self._history_pool(round_id, dev)
            candidates = self._propose(round_id, pool, incumbent)
            screens = _payloads(self.store, round_id, "POLICY_SCREEN_RECORDED")
            if screens:
                screen = self.store.artifacts.get(screens[0]["screen_ref"])
            else:
                screen = self.screen(round_id, candidates, dev, incumbent)
                self.store.append(
                    round_id,
                    "POLICY_SCREEN_RECORDED",
                    {"screen_ref": self.store.artifacts.put(screen)},
                )
            changing = [
                c["policy_ref"] for c in screen["candidates"] if c["requires_fresh_execution"]
            ]
            if not changing:
                return self._close(round_id, "NO_BEHAVIOUR_CHANGE", None)
            evaluations = []
            for ref in changing:
                pairs = []
                for task in spec.validation:
                    a = self._session(round_id, "validation", task, incumbent)
                    b = self._session(round_id, "validation", task, ref)
                    pairs.append(
                        {
                            "task_id": task.task_id,
                            "incumbent": a["utility"],
                            "candidate": b["utility"],
                            "incumbent_decisions": a["decisions"],
                            "candidate_decisions": b["decisions"],
                        }
                    )
                mean = sum(p["candidate"] - p["incumbent"] for p in pairs) / len(pairs)
                wins = sum(p["candidate"] > p["incumbent"] for p in pairs)
                evaluations.append(
                    {
                        "policy_ref": ref,
                        "pairs": pairs,
                        "mean_gain": mean,
                        "wins": wins,
                        "passes": mean > spec.min_mean_gain and wins * 2 > len(pairs),
                    }
                )
            verdicts = _payloads(self.store, round_id, "VALIDATION_RECORDED")
            if not verdicts:
                ref = self.store.artifacts.put(
                    {
                        "kind": "PolicyValidation",
                        "evaluations": evaluations,
                        "rule": "mean holdout utility gain above min_mean_gain and a majority of paired wins",
                    }
                )
                self.store.append(round_id, "VALIDATION_RECORDED", {"validation_ref": ref})
                verdicts = _payloads(self.store, round_id, "VALIDATION_RECORDED")
            validation_ref = verdicts[0]["validation_ref"]
            passing = sorted(
                (e for e in evaluations if e["passes"]),
                key=lambda e: (-e["mean_gain"], e["policy_ref"]),
            )
            if not passing:
                return self._close(round_id, "NOT_ADOPTED", None)
            chosen = passing[0]["policy_ref"]
            if self.registry.active(spec.registry) == incumbent:
                self.registry.activate(
                    spec.registry, chosen, "passed paired fresh validation", validation_ref
                )
                self.store.append(
                    round_id,
                    "POLICY_ADOPTED",
                    {"policy_ref": chosen, "validation_ref": validation_ref},
                )
            return self._canary(round_id, spec, incumbent, chosen)

    def _canary(
        self, round_id: str, spec: AutonomyDreamSpec, incumbent: str, chosen: str
    ) -> dict[str, Any]:
        pairs = []
        for task in spec.canary:
            a = self._session(round_id, "canary", task, incumbent)
            b = self._session(round_id, "canary", task, chosen)
            pairs.append(
                {"task_id": task.task_id, "incumbent": a["utility"], "adopted": b["utility"]}
            )
        regressed = any(p["adopted"] < p["incumbent"] - spec.canary_tolerance for p in pairs)
        ref = self.store.artifacts.put(
            {"kind": "PolicyCanary", "pairs": pairs, "regressed": regressed}
        )
        self.store.append(round_id, "CANARY_RECORDED", {"canary_ref": ref, "regressed": regressed})
        if regressed:
            if self.registry.active(spec.registry) == chosen:
                self.registry.rollback(spec.registry, incumbent, ref)
            return self._close(round_id, "ROLLED_BACK", chosen)
        return self._close(round_id, "ADOPTED", chosen)

    def _close(self, round_id: str, outcome: str, policy_ref: str | None) -> dict[str, Any]:
        value = {
            "outcome": outcome,
            "policy_ref": policy_ref,
            "active_policy_ref": self.registry.active(self.spec(round_id).registry),
        }
        self.store.append(round_id, "DREAM_ROUND_CLOSED", value)
        self.store.set_status(round_id, "CLOSED")
        return value


def dream_report(store: Store, round_id: str) -> dict[str, Any]:
    events = store.events(round_id)
    outcomes = [
        store.artifacts.get(e["payload"]["outcome_ref"])
        for e in events
        if e["event_type"] == "DREAM_SESSION_OUTCOME"
    ]
    for o in outcomes:
        verify_session(store, o["session"])
    spec = AutonomyDreamSpec.model_validate(store.manifest(round_id)["spec"])
    pool = [e["payload"]["pool_ref"] for e in events if e["event_type"] == "HISTORY_POOL_SEALED"]
    developer_contexts = [
        store.artifacts.get(e["payload"]["context_ref"])
        for e in events
        if e["event_type"] == "LAB_ROLE_REQUESTED"
    ]
    dev_sessions = {o["session"] for o in outcomes if o["cohort"] == "development"} | set(
        spec.development_sessions
    )
    for context in developer_contexts:
        seen = {s["session"] for s in context["development_summary"]["sessions"]}
        if not seen <= dev_sessions:
            raise IntegrityError("developer saw non-development histories")
    closed = [e["payload"] for e in events if e["event_type"] == "DREAM_ROUND_CLOSED"]
    verify_roles_round = __import__("jarvis.research_roles", fromlist=["verify_roles"]).verify_roles
    verify_roles_round(store, round_id)
    return {
        "round": round_id,
        "status": store.status(round_id),
        "closed": closed[0] if closed else None,
        "registry_history": PolicyRegistry(store).history(spec.registry),
        "sessions": [
            {
                k: o[k]
                for k in (
                    "cohort",
                    "task_id",
                    "policy_ref",
                    "session",
                    "utility",
                    "development_gain",
                    "decisions",
                )
            }
            for o in outcomes
        ],
        "history_pool_ref": pool[0] if pool else None,
        "screen": next(
            (
                store.artifacts.get(e["payload"]["screen_ref"])
                for e in events
                if e["event_type"] == "POLICY_SCREEN_RECORDED"
            ),
            None,
        ),
        "validation": next(
            (
                store.artifacts.get(e["payload"]["validation_ref"])
                for e in events
                if e["event_type"] == "VALIDATION_RECORDED"
            ),
            None,
        ),
        "canary": next(
            (
                store.artifacts.get(e["payload"]["canary_ref"])
                for e in events
                if e["event_type"] == "CANARY_RECORDED"
            ),
            None,
        ),
        "validation_scope": "Paired fresh sessions on disjoint tasks with trusted hidden holdout utility; developer saw development histories only.",
        "external_calls": 0,
    }
