"""Dream selection -> full-cost reviewed arms -> continuous adoption -> rollback.

Catalog replay is a zero-call screening stage. Changed Director/Critic contexts
are evaluated by NEW programs, never predicted by counterfactual replay.
"""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.campaign import source_identity
from jarvis.contracts import AdmissionError, Budget, IntegrityError, JarvisError, Record
from jarvis.hermes_dream import (
    HermesDreamSpec,
    HermesPolicyBatch,
    cohort_key,
    development_context,
    history_key,
    policy_rank,
)
from jarvis.hermes_policy import HermesPolicy, compare_policies, q0, verify_world
from jarvis.identity import digest
from jarvis.iris import IrisTransport, LocalIrisTransport
from jarvis.iris_continuous import IrisContinuousSpec, IrisContinuousStudy, continuous_report
from jarvis.program_costs import compare_full_programs, protocol_key
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.research_program import ResearchProgram, ResearchProgramSpec, program_report
from jarvis.research_roles import RoleCaller, strict_schema, verify_roles
from jarvis.researcher import AgentConfig
from jarvis.reviewed_registry import ReviewedPolicyRegistry
from jarvis.storage import Store


class ReviewedTask(Record):
    family_id: str = Field(min_length=1)
    program: ResearchProgramSpec


class ReviewedDreamSpec(Record):
    kind: Literal["ReviewedDreamSpec"] = "ReviewedDreamSpec"
    development: tuple[ReviewedTask, ...] = Field(min_length=1, max_length=24)
    heldout: tuple[ReviewedTask, ...] = Field(min_length=1, max_length=24)
    fresh: tuple[ReviewedTask, ...] = Field(min_length=1, max_length=8)
    canary: tuple[ReviewedTask, ...] = Field(min_length=1, max_length=8)
    continuation: IrisContinuousSpec
    incumbent: HermesPolicy = q0()
    policy_agent: AgentConfig = AgentConfig(reserve_tokens=65536, max_calls=2)
    catalog_budget: int = Field(default=1, ge=1, le=8, strict=True)
    allow_observed: bool = False

    @model_validator(mode="after")
    def scope(self) -> "ReviewedDreamSpec":
        if (
            len(self.development) + len(self.heldout) < 10
            or len({t.family_id for t in self.development}) < 3
        ):
            raise ValueError("ten distinct histories and three development families required")
        keys: set[str] = set()
        for tasks in [self.development, self.heldout, self.fresh, self.canary]:
            local = set()
            for task in tasks:
                p = task.program
                if p.critic is None or p.execution_budget is None or p.research_policy is None:
                    raise ValueError("all tasks require reviewed policy and shared budget")
                env = p.queue.environment
                if task.family_id != env.period.group_id:
                    raise ValueError("task family must match its sealed-world group")
                if not self.allow_observed and env.result_origin != "SIMULATED_FIXTURE":
                    raise ValueError(
                        "observed research requires an explicit separate scope declaration"
                    )
                key = cohort_key(
                    {"environment": env.model_dump(mode="json"), "family_id": task.family_id}
                )
                if key in keys:
                    raise ValueError("overlapping policy evaluation cohorts")
                local.add(key)
            keys |= local
        if (
            len(
                {
                    history_key(
                        {
                            "environment": t.program.queue.environment.model_dump(mode="json"),
                            "family_id": t.family_id,
                        }
                    )
                    for t in [*self.development, *self.heldout]
                }
            )
            < 10
        ):
            raise ValueError("renaming tasks does not create distinct history")
        for t in [*self.heldout, *self.fresh, *self.canary]:
            if t.program.queue.environment.period.exposure != "sealed_unseen":
                raise ValueError("heldout/fresh/canary tasks must be unopened")
        # Real market dates must also be disjoint across cohorts, including
        # partially overlapping intervals; labels do not grant independence.
        seen_dates: set[str] = set()
        for tasks in [self.development, self.heldout, self.fresh, self.canary]:
            dates = {
                str(d)
                for t in tasks
                if t.program.queue.environment.result_origin != "SIMULATED_FIXTURE"
                for d in t.program.queue.environment.period.trading_dates
            }
            if dates & seen_dates:
                raise ValueError("market dates overlap across policy cohorts")
            seen_dates |= dates
        template = self.fresh[0].program
        if any(
            protocol_key(t.program) != protocol_key(template) for t in [*self.fresh, *self.canary]
        ):
            raise ValueError("fresh/canary research protocols and budgets differ")
        return self


def _payloads(store: Store, owner: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in store.events(owner) if e["event_type"] == kind]


class ReviewedDreamLoop:
    def __init__(
        self,
        store: Store,
        *,
        provider_factory: Callable[[AgentConfig], ResearchProvider] = CodexProvider,
        critic_provider_factory: Callable[[AgentConfig], ResearchProvider] | None = None,
        policy_provider_factory: Callable[[AgentConfig], ResearchProvider] | None = None,
        transport_factory: Callable[..., IrisTransport] = LocalIrisTransport,
    ):
        self.store = store
        self.program = ResearchProgram(
            store,
            provider_factory=provider_factory,
            critic_provider_factory=critic_provider_factory,
            transport_factory=transport_factory,
        )
        self.continuous = IrisContinuousStudy(
            store, provider_factory=provider_factory, transport_factory=transport_factory
        )
        self.continuous.program.critic_provider_factory = self.program.critic_provider_factory
        self.policy_provider_factory = policy_provider_factory or provider_factory
        self.registry = ReviewedPolicyRegistry(store)

    def create(self, spec: ReviewedDreamSpec) -> str:
        provider = self.policy_provider_factory(spec.policy_agent).identity()
        raw = spec.model_dump(mode="json")
        return self.store.create(
            {
                "kind": "ReviewedDreamLoop",
                "spec": raw,
                "provider": provider,
                "source_identity": source_identity(),
                "world_id": digest({"spec": raw, "provider": provider}),
                "task": {
                    "budget": Budget(
                        max_actions=100000,
                        max_tool_calls=100000,
                        max_compute_units=100000,
                        max_llm_tokens=100000000,
                        max_wall_seconds=100000000,
                        max_parallel_jobs=16,
                    ).model_dump()
                },
            }
        )

    def _program(self, owner: str, key: str, spec: ResearchProgramSpec) -> str:
        known = next(
            (
                p
                for p in _payloads(self.store, owner, "REVIEWED_PROGRAM_REGISTERED")
                if p["key"] == key
            ),
            None,
        )
        if known:
            return str(known["program_id"])
        pid = self.program.create(spec, budget_owner=owner)
        self.store.append(
            owner,
            "REVIEWED_PROGRAM_REGISTERED",
            {"key": key, "program_id": pid, "spec_ref": self.store.artifacts.put(spec)},
        )
        return pid

    def _advance_program(self, pid: str) -> bool:
        status = self.program.advance(pid)["status"]
        if status in {
            "NEEDS_RECONCILIATION",
            "CRITIC_NEEDS_ATTENTION",
            "BUDGET_EXHAUSTED",
            "PAUSED",
            "CANCELLED",
        }:
            raise JarvisError(
                f"reviewed policy arm requires attention: {status}; no automatic retry"
            )
        return status == "CLOSED"

    def _collect(self, owner: str, task: ReviewedTask, key: str) -> str | None:
        known = next(
            (
                p
                for p in _payloads(self.store, owner, "REVIEWED_HISTORY_COLLECTED")
                if p["key"] == key
            ),
            None,
        )
        if known:
            return str(known["world_ref"])
        cohort = cohort_key(
            {
                "environment": task.program.queue.environment.model_dump(mode="json"),
                "family_id": task.family_id,
            }
        )
        if not self.store.claim_policy_families(owner, [cohort], key.split(":")[0]):
            raise IntegrityError("collection task family has prior policy exposure")
        pid = self._program(
            owner,
            key,
            task.program.model_copy(update={"research_policy": q0(), "experiments_per_epoch": 3}),
        )
        if not self._advance_program(pid):
            return None
        known = next(
            (
                p
                for p in _payloads(self.store, owner, "REVIEWED_HISTORY_COLLECTED")
                if p["key"] == key
            ),
            None,
        )
        if known:
            return str(known["world_ref"])
        report = program_report(self.store, pid)
        if len(report["world_refs"]) != 1:
            raise IntegrityError("collection task must seal exactly one predeclared epoch")
        ref = report["world_refs"][0]
        if verify_world(self.store, ref)["family_id"] != task.family_id:
            raise IntegrityError("collected family differs from declared task")
        self.store.append(
            owner, "REVIEWED_HISTORY_COLLECTED", {"key": key, "program_id": pid, "world_ref": ref}
        )
        return ref

    def _develop(
        self,
        owner: str,
        spec: ReviewedDreamSpec,
        refs: tuple[str, ...],
        cycle: int,
        incumbent: HermesPolicy,
    ) -> dict[str, Any]:
        known = next(
            (
                p
                for p in _payloads(self.store, owner, "REVIEWED_DREAM_SCREENED")
                if p["cycle"] == cycle
            ),
            None,
        )
        if known:
            return known
        context = development_context(
            self.store,
            HermesDreamSpec(
                development=refs, incumbent=incumbent, experiments_per_world=spec.catalog_budget
            ),
        )
        context["screening_boundary"] = (
            "Catalog ordering screen only. Tool path cost is not full Director/Critic counterfactual cost. Full new paired programs are mandatory before activation."
        )
        provider = self.policy_provider_factory(spec.policy_agent)
        if provider.identity() != self.store.manifest(owner)["provider"]:
            raise IntegrityError("Dream policy provider changed")
        schema = strict_schema(HermesPolicyBatch)
        schema["properties"]["context_id"]["enum"] = [digest(context)]
        response = RoleCaller(self.store, provider, spec.policy_agent).call(
            owner, "policy_developer", context, schema
        )
        batch = HermesPolicyBatch.model_validate(response["answer"])
        if batch.context_id != digest(context):
            raise AdmissionError("Dream used stale history")
        comparisons = [
            {
                "policy": p.model_dump(mode="json"),
                "comparison": compare_policies(
                    self.store, refs, p, incumbent, spec.catalog_budget, strict=True
                ),
            }
            for p in batch.proposals
        ]
        eligible = [p for p in comparisons if p["comparison"]["accepted"]]
        payload = {
            "cycle": cycle,
            "history_refs": refs,
            "context_id": digest(context),
            "receipt_ref": response["receipt_ref"],
            "comparisons_ref": self.store.artifacts.put(comparisons),
            "finalist_ref": self.store.artifacts.put(min(eligible, key=policy_rank)["policy"])
            if eligible
            else None,
            "scope": "zero-call recorded-catalog screen; no full-program counterfactual claim",
        }
        self.store.append(owner, "REVIEWED_DREAM_SCREENED", payload)
        return payload

    def _pairs(
        self, owner: str, spec: ReviewedDreamSpec, phase: str, candidate: HermesPolicy
    ) -> dict[str, Any] | None:
        known = _payloads(self.store, owner, "REVIEWED_PAIRED_PHASE_COMPLETED")
        saved = next((p for p in known if p["phase"] == phase), None)
        if saved:
            return self.store.artifacts.get(saved["result_ref"])
        tasks = spec.fresh if phase == "fresh" else spec.canary
        keys = [
            cohort_key(
                {
                    "environment": t.program.queue.environment.model_dump(mode="json"),
                    "family_id": t.family_id,
                }
            )
            for t in tasks
        ]
        if not self.store.claim_policy_families(owner, keys, phase):
            raise IntegrityError("prospective family has prior policy exposure")
        pairs = []
        for i, task in enumerate(tasks):
            arm_ids = {}
            for arm, policy in [("incumbent", spec.incumbent), ("candidate", candidate)]:
                program_spec = task.program.model_copy(update={"research_policy": policy})
                pid = self._program(owner, f"{phase}:{i}:{arm}", program_spec)
                if not self._advance_program(pid):
                    return None
                arm_ids[arm] = pid
            pairs.append(arm_ids)
        comparisons = [
            compare_full_programs(self.store, p["incumbent"], p["candidate"], strict=False)
            for p in pairs
        ]
        accepted = all(p["nonregression"] for p in comparisons) and (
            phase == "canary" or any(p["strict_gain"] for p in comparisons)
        )
        value = {"phase": phase, "pairs": pairs, "comparisons": comparisons, "accepted": accepted}
        self.store.append(
            owner,
            "REVIEWED_PAIRED_PHASE_COMPLETED",
            {"phase": phase, "result_ref": self.store.artifacts.put(value)},
        )
        return value

    def _rollout(self, owner: str, spec: ReviewedDreamSpec, registry: str, index: int) -> bool:
        known = _payloads(self.store, owner, "REVIEWED_CONTINUATION_CREATED")
        if known:
            sid = known[0]["study_id"]
        else:
            declaration = spec.continuation.model_copy(update={"policy_registry": registry})
            sid = self.continuous.create(declaration, budget_owner=owner)
            self.store.append(owner, "REVIEWED_CONTINUATION_CREATED", {"study_id": sid})
        if not _payloads(self.store, owner, "REVIEWED_CONTINUATION_QUOTA"):
            observed = self.continuous.refresh_quota(sid, [])
            self.store.append(owner, "REVIEWED_CONTINUATION_QUOTA", {"observation": observed})
        links = _payloads(self.store, sid, "IRIS_PROGRAM_REGISTERED")
        if len(links) <= index or self.store.status(links[index]["program_id"]) != "CLOSED":
            progress = self.continuous.advance(sid)
            if progress["status"] in {
                "NEEDS_RECONCILIATION",
                "TRACK_NEEDS_ATTENTION",
                "CLOSED",
                "TRACK_STOPPED",
            }:
                raise JarvisError("continuous policy adoption stopped before its declared program")
            return False
        recorded = _payloads(self.store, owner, "REVIEWED_CONTINUATION_OBSERVED")
        if not any(p["index"] == index for p in recorded):
            pid = links[index]["program_id"]
            self.store.append(
                owner,
                "REVIEWED_CONTINUATION_OBSERVED",
                {
                    "index": index,
                    "program_id": pid,
                    "policy": self.store.manifest(pid)["spec"]["research_policy"],
                },
            )
        return True

    def advance(self, owner: str) -> dict[str, Any]:
        with self.store.campaign_lock(owner, scope="reviewed-dream"):
            if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                return {"status": self.store.get_control(owner), "study_id": owner}
            if self.store.status(owner) in {
                "CLOSED",
                "NEEDS_RECONCILIATION",
                "PAUSED",
                "CANCELLED",
            }:
                return (
                    reviewed_dream_report(self.store, owner)
                    if self.store.status(owner) == "CLOSED"
                    else {"status": self.store.status(owner), "study_id": owner}
                )
            if self.store.manifest(owner)["source_identity"] != source_identity():
                raise IntegrityError("Dream runtime changed")
            try:
                return self._advance(owner)
            except (JarvisError, ValueError):
                self.store.set_status(owner, "NEEDS_RECONCILIATION")
                raise

    def run(
        self,
        owner: str,
        *,
        max_steps: int = 2000,
        wall_seconds: int = 14400,
        poll_seconds: int = 45,
    ) -> dict[str, Any]:
        import time

        if (
            not 1 <= max_steps <= 5000
            or not 1 <= wall_seconds <= 14400
            or not 1 <= poll_seconds <= 60
        ):
            raise AdmissionError("finite Dream runner bounds required")
        started = time.monotonic()
        progress: dict[str, Any] = {"status": "RUNNER_LIMIT"}
        with self.store.campaign_lock(owner, scope="reviewed-dream-run"):
            for _ in range(max_steps):
                if time.monotonic() - started >= wall_seconds:
                    break
                progress = self.advance(owner)
                if progress["status"] in {"CLOSED", "NEEDS_RECONCILIATION", "PAUSED", "CANCELLED"}:
                    return progress
                if any(
                    not j["finalized"]
                    for member in self.store.resource_members(owner)
                    for j in self.store.jobs(member)
                ):
                    time.sleep(
                        min(poll_seconds, max(0, wall_seconds - (time.monotonic() - started)))
                    )
        return {
            "study_id": owner,
            "status": "RUNNER_LIMIT",
            "last_progress": progress,
            "resumable": True,
        }

    def _advance(self, owner: str) -> dict[str, Any]:
        spec = ReviewedDreamSpec.model_validate(self.store.manifest(owner)["spec"])
        for phase, tasks in [("development", spec.development), ("heldout", spec.heldout)]:
            for i, task in enumerate(tasks):
                if self._collect(owner, task, f"{phase}:{i}") is None:
                    return {
                        "status": "COLLECTING_REVIEWED_HISTORY",
                        "study_id": owner,
                        "phase": phase,
                        "index": i,
                    }
        collected = _payloads(self.store, owner, "REVIEWED_HISTORY_COLLECTED")
        dev = tuple(p["world_ref"] for p in collected if p["key"].startswith("development:"))
        held = tuple(p["world_ref"] for p in collected if p["key"].startswith("heldout:"))
        screening = self._develop(owner, spec, dev, 1, spec.incumbent)
        if screening["finalist_ref"] is None:
            return self._finish(owner, "NO_DEVELOPMENT_GAIN")
        candidate = HermesPolicy.model_validate(self.store.artifacts.get(screening["finalist_ref"]))
        frozen = _payloads(self.store, owner, "REVIEWED_FINALIST_FROZEN")
        if not frozen:
            self.store.append(
                owner, "REVIEWED_FINALIST_FROZEN", {"policy_ref": screening["finalist_ref"]}
            )
        if not _payloads(self.store, owner, "REVIEWED_HELDOUT_CHECKED"):
            keys = [cohort_key(verify_world(self.store, r)) for r in held]
            if not self.store.claim_policy_families(owner, keys, "heldout"):
                return self._finish(owner, "HELDOUT_ALREADY_EXPOSED")
            verdict = compare_policies(
                self.store, held, candidate, spec.incumbent, spec.catalog_budget, strict=False
            )
            self.store.append(
                owner, "REVIEWED_HELDOUT_CHECKED", {"result_ref": self.store.artifacts.put(verdict)}
            )
        if not self.store.artifacts.get(
            _payloads(self.store, owner, "REVIEWED_HELDOUT_CHECKED")[0]["result_ref"]
        )["accepted"]:
            return self._finish(owner, "REJECTED_HELDOUT")
        fresh = self._pairs(owner, spec, "fresh", candidate)
        if fresh is None:
            return {"status": "WAITING_FULL_COST_FRESH_ARMS", "study_id": owner}
        if not fresh["accepted"]:
            return self._finish(owner, "REJECTED_FULL_COST_FRESH")
        registries = _payloads(self.store, owner, "REVIEWED_REGISTRY_CREATED")
        if registries:
            registry = registries[0]["registry_id"]
        else:
            registry = self.registry.create(spec.incumbent, spec.fresh[0].program)
            self.store.append(owner, "REVIEWED_REGISTRY_CREATED", {"registry_id": registry})
        proof = {
            "kind": "ReviewedPolicyFreshProof",
            "study_id": owner,
            "finalist_ref": screening["finalist_ref"],
            "incumbent": spec.incumbent.model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json"),
            "protocol_id": protocol_key(spec.fresh[0].program),
            "result_origin": spec.fresh[0].program.queue.environment.result_origin,
            "pairs": fresh["pairs"],
            "heldout": held,
            "catalog_budget": spec.catalog_budget,
        }
        if not _payloads(self.store, registry, "REVIEWED_POLICY_ACTIVATED"):
            self.registry.change(registry, spec.fresh[0].program, proof)
        if not self._rollout(owner, spec, registry, 0):
            return {"status": "CONTINUING_WITH_DREAM_POLICY", "study_id": owner}
        canary = self._pairs(owner, spec, "canary", candidate)
        if canary is None:
            return {"status": "WAITING_FULL_COST_CANARY_ARMS", "study_id": owner}
        if not canary["accepted"] and not _payloads(
            self.store, registry, "REVIEWED_POLICY_ROLLED_BACK"
        ):
            rollback = {**proof, "kind": "ReviewedPolicyRollbackProof", "pairs": canary["pairs"]}
            self.registry.change(registry, spec.fresh[0].program, rollback, rollback=True)
        if not self._rollout(owner, spec, registry, 1):
            return {"status": "CONTINUING_AFTER_CANARY", "study_id": owner}
        if not _payloads(self.store, owner, "REVIEWED_POOL_EXPANDED"):
            additions = [
                ref
                for p in _payloads(self.store, owner, "REVIEWED_CONTINUATION_OBSERVED")
                for ref in program_report(self.store, p["program_id"])["world_refs"]
            ]
            self.store.append(owner, "REVIEWED_POOL_EXPANDED", {"history_refs": additions})
        active, _ = self.registry.active(registry, spec.fresh[0].program)
        additions = _payloads(self.store, owner, "REVIEWED_POOL_EXPANDED")[0]["history_refs"]
        second = self._develop(owner, spec, tuple([*dev, *additions]), 2, active)
        self.store.append(
            owner,
            "REVIEWED_NEXT_CYCLE_STOPPED",
            {
                "reason": "NEW_INDEPENDENT_VALIDATION_REQUIRED"
                if second["finalist_ref"]
                else "NO_COVERED_DEVELOPMENT_GAIN",
                "old_validation_reused": False,
            },
        )
        return self._finish(owner, "APPROVED_FOR_RESEARCH" if canary["accepted"] else "ROLLED_BACK")

    def _finish(self, owner: str, outcome: str) -> dict[str, Any]:
        self.store.append(owner, "REVIEWED_DREAM_COMPLETED", {"outcome": outcome})
        self.store.set_status(owner, "CLOSED")
        return reviewed_dream_report(self.store, owner)


def reviewed_dream_report(store: Store, owner: str) -> dict[str, Any]:
    verification = verify_reviewed_dream(store, owner)
    programs = _payloads(store, owner, "REVIEWED_PROGRAM_REGISTERED")
    continuation = _payloads(store, owner, "REVIEWED_CONTINUATION_CREATED")
    done = _payloads(store, owner, "REVIEWED_DREAM_COMPLETED")
    return {
        "kind": "ReviewedDreamReport",
        "study_id": owner,
        "status": store.status(owner),
        "outcome": done[-1]["outcome"] if done else None,
        "verification": verification,
        "programs": [{**p, "report": program_report(store, p["program_id"])} for p in programs],
        "comparisons": [
            store.artifacts.get(p["result_ref"])
            for p in _payloads(store, owner, "REVIEWED_PAIRED_PHASE_COMPLETED")
        ],
        "continuation": continuous_report(store, continuation[0]["study_id"])
        if continuation
        else None,
        "dream_usage": store.resource_totals(owner),
        "full_usage": store.resource_totals_tree(owner),
        "pool_expansion": _payloads(store, owner, "REVIEWED_POOL_EXPANDED"),
        "next_cycle": _payloads(store, owner, "REVIEWED_NEXT_CYCLE_STOPPED"),
        "scope": "SYNTHETIC_ONLY"
        if not ReviewedDreamSpec.model_validate(store.manifest(owner)["spec"]).allow_observed
        else "DECLARED_RESEARCH_ONLY",
        "remote_compute_queue_and_money": "UNAVAILABLE; not inferred from local timers or tokens",
        "deployment": "NOT_RUN",
    }


def verify_reviewed_dream(store: Store, owner: str) -> dict[str, Any]:
    m = store.manifest(owner)
    spec = ReviewedDreamSpec.model_validate(m["spec"])
    if len(_payloads(store, owner, "REVIEWED_HISTORY_COLLECTED")) > len(spec.development) + len(
        spec.heldout
    ):
        raise IntegrityError("unexpected extra collection history")
    if m["world_id"] != digest({"spec": m["spec"], "provider": m["provider"]}):
        raise IntegrityError("Dream declaration changed")
    verify_roles(store, owner)
    events = store.events(owner)
    registered = _payloads(store, owner, "REVIEWED_PROGRAM_REGISTERED")
    by_key = {p["key"]: p for p in registered}
    if len(by_key) != len(registered):
        raise IntegrityError("duplicate registered research arm")
    collected = _payloads(store, owner, "REVIEWED_HISTORY_COLLECTED")
    if len({p["key"] for p in collected}) != len(collected):
        raise IntegrityError("duplicate collected history")
    for p in collected:
        phase, index = p["key"].split(":")
        tasks = spec.development if phase == "development" else spec.heldout
        expected = tasks[int(index)].program.model_copy(
            update={"research_policy": q0(), "experiments_per_epoch": 3}
        )
        world = verify_world(store, p["world_ref"])
        if (
            p["key"] not in by_key
            or p["program_id"] != by_key[p["key"]]["program_id"]
            or ResearchProgramSpec.model_validate(store.manifest(p["program_id"])["spec"])
            != expected
            or world["environment"] != expected.queue.environment.model_dump(mode="json")
            or p["world_ref"] not in program_report(store, p["program_id"])["world_refs"]
        ):
            raise IntegrityError("history was not produced by the declared reviewed task")
    screens = _payloads(store, owner, "REVIEWED_DREAM_SCREENED")
    if len({s["cycle"] for s in screens}) != len(screens):
        raise IntegrityError("duplicate Dream policy-development cycle")
    completed = {
        e["payload"]["receipt_ref"]: e for e in events if e["event_type"] == "ROLE_COMPLETED"
    }
    jobs = {j["id"]: j for j in store.jobs(owner)}
    dev_refs = tuple(p["world_ref"] for p in collected if p["key"].startswith("development:"))
    for screen in screens:
        receipt_event = completed.get(screen["receipt_ref"])
        if receipt_event is None:
            raise IntegrityError("policy development has no paid/declared receipt")
        completion = receipt_event["payload"]
        request = store.artifacts.get(jobs[completion["job_id"]]["request_ref"])
        batch = HermesPolicyBatch.model_validate(store.artifacts.get(completion["answer_ref"]))
        context = request["context"]
        refs = tuple(screen["history_refs"])
        if screen["cycle"] == 1 and refs != dev_refs:
            raise IntegrityError("policy developer saw heldout/future history")
        if screen["cycle"] == 2:
            expansions = _payloads(store, owner, "REVIEWED_POOL_EXPANDED")
            if not expansions or refs != (*dev_refs, *expansions[0]["history_refs"]):
                raise IntegrityError("next Dream cycle omitted actual new histories")
        incumbent = HermesPolicy.model_validate(context["incumbent"])
        expected_context = development_context(
            store,
            HermesDreamSpec(
                development=refs, incumbent=incumbent, experiments_per_world=spec.catalog_budget
            ),
        )
        expected_context["screening_boundary"] = (
            "Catalog ordering screen only. Tool path cost is not full Director/Critic counterfactual cost. Full new paired programs are mandatory before activation."
        )
        if (
            context != expected_context
            or screen["context_id"] != digest(context)
            or batch.context_id != digest(context)
            or request["provider"] != m["provider"]
            or request["config"] != spec.policy_agent.model_dump(mode="json")
        ):
            raise IntegrityError("Dream context/config differs from declared development input")
        comparisons = [
            {
                "policy": p.model_dump(mode="json"),
                "comparison": compare_policies(
                    store, refs, p, incumbent, spec.catalog_budget, strict=True
                ),
            }
            for p in batch.proposals
        ]
        eligible = [p for p in comparisons if p["comparison"]["accepted"]]
        expected_ref = digest(min(eligible, key=policy_rank)["policy"]) if eligible else None
        if (
            screen["finalist_ref"] != expected_ref
            or store.artifacts.get(screen["comparisons_ref"]) != comparisons
        ):
            raise IntegrityError("Dream selection differs from exact recorded-branch replay")
    frozen = _payloads(store, owner, "REVIEWED_FINALIST_FROZEN")
    if len(frozen) > 1:
        raise IntegrityError("more than one development finalist")
    held_events = [e for e in events if e["event_type"] == "REVIEWED_HELDOUT_CHECKED"]
    if held_events:
        freeze_events = [e for e in events if e["event_type"] == "REVIEWED_FINALIST_FROZEN"]
        if not freeze_events or freeze_events[0]["sequence"] >= held_events[0]["sequence"]:
            raise IntegrityError("heldout opened before finalist freeze")
        held_refs = tuple(p["world_ref"] for p in collected if p["key"].startswith("heldout:"))
        candidate = HermesPolicy.model_validate(store.artifacts.get(frozen[0]["policy_ref"]))
        if compare_policies(
            store, held_refs, candidate, spec.incumbent, spec.catalog_budget, strict=False
        ) != store.artifacts.get(held_events[0]["payload"]["result_ref"]):
            raise IntegrityError("heldout verdict changed")
    for row in _payloads(store, owner, "REVIEWED_PROGRAM_REGISTERED"):
        if ResearchProgramSpec.model_validate(
            store.artifacts.get(row["spec_ref"])
        ) != ResearchProgramSpec.model_validate(store.manifest(row["program_id"])["spec"]):
            raise IntegrityError("registered policy arm changed")
        program_report(store, row["program_id"])
        if store.manifest(row["program_id"]).get("budget_owner") != owner:
            raise IntegrityError("program escaped total Dream cost ledger")
    for row in _payloads(store, owner, "REVIEWED_PAIRED_PHASE_COMPLETED"):
        value = store.artifacts.get(row["result_ref"])
        tasks = spec.fresh if row["phase"] == "fresh" else spec.canary
        if len(tasks) != len(value["pairs"]):
            raise IntegrityError("paired comparison omitted a task")
        for index, (task, pair) in enumerate(zip(tasks, value["pairs"], strict=True)):
            for arm in ["incumbent", "candidate"]:
                declared = task.program.model_copy(
                    update={
                        "research_policy": spec.incumbent
                        if arm == "incumbent"
                        else HermesPolicy.model_validate(
                            store.artifacts.get(frozen[0]["policy_ref"])
                        )
                    }
                )
                if pair[arm] != by_key[f"{row['phase']}:{index}:{arm}"][
                    "program_id"
                ] or declared != ResearchProgramSpec.model_validate(
                    store.manifest(pair[arm])["spec"]
                ):
                    raise IntegrityError("paired arm changed its frozen task/policy")
        actual = [
            compare_full_programs(store, p["incumbent"], p["candidate"], strict=False)
            for p in value["pairs"]
        ]
        phase_accepted = all(p["nonregression"] for p in actual) and (
            row["phase"] == "canary" or any(p["strict_gain"] for p in actual)
        )
        if actual != value["comparisons"] or phase_accepted != value["accepted"]:
            raise IntegrityError("full-cost comparison differs from its receipts")
    for row in _payloads(store, owner, "REVIEWED_CONTINUATION_CREATED"):
        continuous_report(store, row["study_id"])
    completed_study = _payloads(store, owner, "REVIEWED_DREAM_COMPLETED")
    if len(completed_study) > 1 or (
        store.status(owner) == "CLOSED"
        and (not completed_study or any(store.resource_totals_tree(owner)["pending"].values()))
    ):
        raise IntegrityError("closed Dream has unknown costs or inconsistent completion")
    if completed_study and completed_study[0]["outcome"] in {
        "ROLLED_BACK",
        "APPROVED_FOR_RESEARCH",
    }:
        phases = {
            p["phase"]: store.artifacts.get(p["result_ref"])
            for p in _payloads(store, owner, "REVIEWED_PAIRED_PHASE_COMPLETED")
        }
        observations = _payloads(store, owner, "REVIEWED_CONTINUATION_OBSERVED")
        expansion = _payloads(store, owner, "REVIEWED_POOL_EXPANDED")
        if (
            set(phases) != {"fresh", "canary"}
            or not phases["fresh"]["accepted"]
            or len(observations) != 2
            or len(expansion) != 1
            or len(screens) != 2
        ):
            raise IntegrityError("complete feedback lacks actual continuation/new history")
        wanted_outcome = "APPROVED_FOR_RESEARCH" if phases["canary"]["accepted"] else "ROLLED_BACK"
        candidate = HermesPolicy.model_validate(store.artifacts.get(frozen[0]["policy_ref"]))
        expected_policies = [
            candidate,
            candidate if phases["canary"]["accepted"] else spec.incumbent,
        ]
        actual_refs = []
        for index, row in enumerate(observations):
            report = program_report(store, row["program_id"])
            if (
                row["index"] != index
                or row["policy"] != expected_policies[index].model_dump(mode="json")
                or report["research_policy"] != row["policy"]
            ):
                raise IntegrityError("continued research did not adopt the expected policy")
            actual_refs.extend(report["world_refs"])
        if (
            completed_study[0]["outcome"] != wanted_outcome
            or expansion[0]["history_refs"] != actual_refs
        ):
            raise IntegrityError("final policy outcome/history expansion changed")
    return {
        "status": "VERIFIED",
        "external_calls": 0,
        "programs": len(_payloads(store, owner, "REVIEWED_PROGRAM_REGISTERED")),
        "next_cycles": len(_payloads(store, owner, "REVIEWED_DREAM_SCREENED")),
    }
