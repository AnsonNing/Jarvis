"""Strategy + direction -> asynchronous real research, immutable task snapshots."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from pydantic import Field

from jarvis.campaign import code_provenance, source_identity
from jarvis.contracts import AdmissionError, Budget, BudgetExceeded, IntegrityError, Record
from jarvis.hermes_policy import HermesPolicy, seal_world, verify_world
from jarvis.identity import digest
from jarvis.iris import IrisTransport, LocalIrisTransport, setting_capabilities
from jarvis.iris_contracts import IrisExperiment, IrisQueueSpec
from jarvis.iris_design import design_capabilities
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.research_acceptance import ResearchAcceptance, assess_candidate
from jarvis.research_queue import IrisResearchQueue, queue_tasks, verify_queue, visible_results
from jarvis.research_roles import (
    DirectionBatch,
    RoleCaller,
    role_resources,
    strict_schema,
    verify_roles,
)
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


class ResearchProgramSpec(Record):
    kind: Literal["ResearchProgramSpec"] = "ResearchProgramSpec"
    queue: IrisQueueSpec
    directions: tuple[str, ...] = Field(min_length=1, max_length=12)
    agent: AgentConfig = AgentConfig(reserve_tokens=65536, max_calls=4)
    critic: AgentConfig | None = None
    research_policy: HermesPolicy | None = None
    experiments_per_epoch: int = Field(default=3, ge=1, le=3, strict=True)
    execution_budget: Budget | None = None
    support_ref: str | None = None
    acceptance: ResearchAcceptance | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    max_direction_batches: int = Field(default=3, ge=1, le=8, strict=True)
    experiments_per_batch: int = Field(default=3, ge=1, le=3, strict=True)
    llm_token_budget: int = Field(default=300000, ge=65536, le=1000000, strict=True)


def direction_context(store: Store, program: str, queue: str) -> dict[str, Any]:
    from jarvis.execution_evidence import execution_evidence

    spec = ResearchProgramSpec.model_validate(store.manifest(program)["spec"])
    links = [
        e["payload"]["queue_id"]
        for e in store.events(program)
        if e["event_type"] == "RESEARCH_QUEUE_ATTACHED"
    ]
    links = list(dict.fromkeys([*spec.queue.prior_queues, *links]))
    results = [r for q in links for r in visible_results(store, q)]
    support = store.artifacts.get(spec.support_ref) if spec.support_ref else None
    return {
        "role": "direction",
        "goal": spec.queue.goal,
        "primary_metric": "entry_only_pl",
        "risk": "Report all; no hidden penalty or metric change",
        **(
            {"selection_constraints": spec.acceptance.model_dump(mode="json")}
            if spec.acceptance
            else {}
        ),
        "directions": spec.directions,
        "batch_constraints": {
            "experiments_maximum": spec.experiments_per_batch,
            "direction_batches_maximum": spec.max_direction_batches,
        },
        "scope": spec.queue.environment.period.exposure,
        "side": spec.queue.environment.side,
        "available_settings": [b.setting for b in spec.queue.environment.bindings]
        + [b["setting"] for b in design_capabilities(spec.queue.environment)],
        "setting_contracts": setting_capabilities(spec.queue.environment),
        "rule_contracts": design_capabilities(spec.queue.environment),
        "existing_settings": [
            store.artifacts.get(t["experiment_ref"])["settings"]
            for q in links
            for t in queue_tasks(store, q)
        ],
        "admission_feedback": [
            {
                "batch_ref": e["payload"]["batch_ref"],
                "rejected": e["payload"]["rejected"],
                "capability_requests": e["payload"]["capability_requests"],
            }
            for e in store.events(program)
            if e["event_type"] == "DIRECTION_BATCH_ADMITTED"
        ],
        "visible_evidence_ids": [r["result_ref"] for r in results],
        "trusted_execution_evidence": execution_evidence(store, links),
        **(
            {"research_support": support, "research_support_ref": spec.support_ref}
            if support
            else {}
        ),
        "observations": [
            {
                "experiment": r["experiment"],
                "result_ref": r["result_ref"],
                "result": {
                    k: v
                    for k, v in r["result"].items()
                    if k
                    in {
                        "status",
                        "entry_only_pl",
                        "count",
                        "mdd",
                        "worst_day",
                        "risk",
                        "daily_pl",
                        "result_origin",
                    }
                },
            }
            for r in results
        ],
        "objective_boundary": "No independent strategy verdict on exposed periods; existing models/strategy are owner supplied, not agent discoveries",
        **(
            {
                "fixed_controls": {
                    "source_sha256": spec.queue.environment.template_sha256,
                    "evaluator_sha256": spec.queue.environment.evaluator_sha256,
                    "model_lineage": spec.queue.environment.model_lineage,
                    "period": spec.queue.environment.period.model_dump(mode="json"),
                    "result_origin": spec.queue.environment.result_origin,
                    "baseline_origin": "owner_provided",
                    "comparator": "frozen_baseline",
                },
                "critic_feedback": [
                    {
                        "review_ref": e["payload"]["review_ref"],
                        "review": store.artifacts.get(e["payload"]["review_ref"]),
                    }
                    for e in store.events(program)
                    if e["event_type"] == "CRITIC_REVIEW_RECORDED"
                ],
            }
            if spec.critic
            else {}
        ),
    }


class ResearchProgram:
    def __init__(
        self,
        store: Store,
        *,
        provider_factory: Callable[[AgentConfig], ResearchProvider] = CodexProvider,
        transport_factory: Callable[..., IrisTransport] = LocalIrisTransport,
        critic_provider_factory: Callable[[AgentConfig], ResearchProvider] | None = None,
    ):
        self.store = store
        self.provider_factory = provider_factory
        self.critic_provider_factory = critic_provider_factory or provider_factory
        self.queue = IrisResearchQueue(store, transport_factory)

    def create(self, spec: ResearchProgramSpec, *, budget_owner: str | None = None) -> str:
        if spec.support_ref:
            from jarvis.lab_contracts import ResearchSupport

            ResearchSupport.model_validate(self.store.artifacts.get(spec.support_ref))
        if spec.research_policy and (spec.critic is None or spec.execution_budget is None):
            raise AdmissionError(
                "policy-controlled research requires Critic and shared execution budget"
            )
        # No provider call. Queue validation/admission occurs before attaching a child.
        self.queue.transport_factory(spec.queue.environment)
        provider = self.provider_factory(spec.agent).identity()
        critic_identity = (
            {"critic_provider": self.critic_provider_factory(spec.critic).identity()}
            if spec.critic
            else {}
        )
        return self.store.create(
            {
                "kind": "ResearchProgram",
                **({"budget_owner": budget_owner} if budget_owner else {}),
                "spec": spec.model_dump(mode="json"),
                "world_id": digest(
                    {"spec": spec.model_dump(mode="json"), "provider": provider, **critic_identity}
                ),
                "provider": provider,
                **critic_identity,
                "task": {
                    "budget": (
                        spec.execution_budget
                        or Budget(
                            max_llm_tokens=spec.llm_token_budget,
                            max_wall_seconds=3600,
                            max_parallel_jobs=1,
                        )
                    ).model_dump()
                },
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
            }
        )

    def _baseline(self) -> IrisExperiment:
        return IrisExperiment(
            label="baseline",
            family="baseline",
            hypothesis="Pinned strategy baseline is reproducible",
            falsification="Source, calendar or entry accounting fails",
            origin="human_provided",
        )

    def _admit(
        self, program: str, qid: str, prepared: dict[str, Any], *, review_ref: str | None = None
    ) -> None:
        raw = DirectionBatch.model_validate(self.store.artifacts.get(prepared["batch_ref"]))
        review = self.store.artifacts.get(review_ref) if review_ref else None
        accepted = []
        rejected = []
        for proposal in raw.experiments:
            try:
                if review and (
                    review["stop_recommended"]
                    or next(
                        a["verdict"]
                        for a in review["assessments"]
                        if a["experiment_ref"] == digest(proposal)
                    )
                    != "approve"
                ):
                    raise AdmissionError("independent Critic requires revision or rejection")
                if proposal.origin != "agent_proposed" or proposal.family == "baseline":
                    raise AdmissionError("direction role must propose a new candidate")
                task_id = self.queue.enqueue(
                    qid, proposal, allowed_evidence=set(prepared["visible_refs"])
                )
                if task_id in accepted or task_id not in {
                    t["task_id"] for t in queue_tasks(self.store, qid)
                }:
                    raise AdmissionError(
                        "candidate duplicates an existing batch or prior comparison"
                    )
                accepted.append(task_id)
            except AdmissionError as exc:
                rejected.append(
                    {"experiment": proposal.model_dump(mode="json"), "reason": str(exc)}
                )
        self.store.append(
            program,
            "DIRECTION_BATCH_ADMITTED",
            {
                **prepared,
                "queue_id": qid,
                "accepted": accepted,
                "rejected": rejected,
                "stop_reason": raw.stop_reason,
                "capability_requests": raw.capability_requests,
                **({"critic_review_ref": review_ref} if review_ref else {}),
            },
        )

    def advance(self, program: str) -> dict[str, Any]:
        manifest = self.store.manifest(program)
        spec = ResearchProgramSpec.model_validate(manifest["spec"])
        if self.store.status(program) == "CLOSED":
            return {"program_id": program, "status": "CLOSED"}
        if manifest["source_identity"] != source_identity():
            raise IntegrityError("research program runtime changed")
        if spec.critic:
            from jarvis.research_critic import advance_reviewed_program

            return advance_reviewed_program(self, program)
        with self.store.campaign_lock(program):
            links = [
                e["payload"]["queue_id"]
                for e in self.store.events(program)
                if e["event_type"] == "RESEARCH_QUEUE_ATTACHED"
            ]
            if not links:
                qid = self.queue.create(spec.queue)
                self.store.append(program, "RESEARCH_QUEUE_ATTACHED", {"queue_id": qid})
                links = [qid]
            qid = links[-1]
        rows = queue_tasks(self.store, qid)
        if not rows:
            self.queue.enqueue(qid, self._baseline())
            rows = queue_tasks(self.store, qid)
        if rows[0]["status"] == "QUEUED" and len(links) > 1:
            self.queue.adopt_baseline(qid, links[-2])
            rows = queue_tasks(self.store, qid)
        elif rows[0]["status"] == "QUEUED":
            for prior in reversed(spec.queue.prior_queues):
                prior_spec = IrisQueueSpec.model_validate(self.store.manifest(prior)["spec"])
                known = queue_tasks(self.store, prior)
                if (
                    prior_spec.environment == spec.queue.environment
                    and known
                    and known[0]["status"] == "SUCCEEDED"
                ):
                    self.queue.adopt_baseline(qid, prior)
                    rows = queue_tasks(self.store, qid)
                    break
        if rows[0]["status"] != "SUCCEEDED":
            return {"program_id": program, "queue_id": qid, **self.queue.tick(qid)}
        events = self.store.events(program)
        batches = [e["payload"] for e in events if e["event_type"] == "DIRECTION_BATCH_ADMITTED"]
        used = {p["batch_ref"] for p in batches}
        prepared = next(
            (
                e["payload"]
                for e in events
                if e["event_type"] == "DIRECTION_BATCH_PREPARED"
                and e["payload"]["batch_ref"] not in used
            ),
            None,
        )
        current = next((p for p in batches if p["queue_id"] == qid), None)
        stopped = any(p["stop_reason"] for p in batches)
        result: dict[str, Any] = {"status": "IDLE"}
        # A next-epoch director can reason while the current epoch's remaining
        # Iris jobs execute. Its catalog is never inserted into the old tree.
        can_direct = (
            prepared is None
            and not stopped
            and len(batches) < spec.max_direction_batches
            and (
                current is None
                or not current["accepted"]
                or any(t["status"] == "SUCCEEDED" for t in rows[1:])
            )
        )
        if can_direct:
            context = direction_context(self.store, program, qid)
            schema = strict_schema(DirectionBatch)
            schema["properties"]["context_id"]["enum"] = [digest(context)]
            schema["properties"]["experiments"]["maxItems"] = spec.experiments_per_batch
            schema["$defs"]["IrisExperiment"]["properties"]["origin"]["enum"] = ["agent_proposed"]
            schema["$defs"]["IrisExperiment"]["properties"]["evidence_refs"]["items"]["enum"] = (
                context["visible_evidence_ids"]
            )
            provider = self.provider_factory(spec.agent)
            if provider.identity() != manifest["provider"]:
                raise IntegrityError("research provider changed")
            role = RoleCaller(self.store, provider, spec.agent)
            with ThreadPoolExecutor(max_workers=2) as executor:
                progress = executor.submit(self.queue.tick, qid)
                try:
                    response = role.call(program, "direction", context, schema)
                except BudgetExceeded:
                    response = None
                    stopped = True
                result = progress.result()
            if response:
                raw = DirectionBatch.model_validate(response["answer"])
                if raw.context_id != digest(context):
                    raise AdmissionError("direction role used a stale context")
                if len(raw.experiments) > spec.experiments_per_batch:
                    raise AdmissionError("direction role exceeded the declared batch size")
                prepared = {
                    "context_id": raw.context_id,
                    "visible_refs": context["visible_evidence_ids"],
                    "receipt_ref": response["receipt_ref"],
                    "batch_ref": self.store.artifacts.put(raw),
                }
                self.store.append(program, "DIRECTION_BATCH_PREPARED", prepared)
        else:
            result = self.queue.tick(qid)
        if current is None and prepared:
            self._admit(program, qid, prepared)
            current = {
                "queue_id": qid,
                "stop_reason": self.store.artifacts.get(prepared["batch_ref"])["stop_reason"],
            }
            prepared = None
            batches = [
                e["payload"]
                for e in self.store.events(program)
                if e["event_type"] == "DIRECTION_BATCH_ADMITTED"
            ]
        rows = queue_tasks(self.store, qid)
        if any(t["status"] in {"QUEUED", "RUNNING"} for t in rows):
            return {"program_id": program, "queue_id": qid, **result}
        if current is not None:
            world_ref = seal_world(self.store, qid, spec.queue.environment.period.group_id)
            if not any(
                e["event_type"] == "PROGRAM_WORLD_COLLECTED" and e["payload"]["queue_id"] == qid
                for e in self.store.events(program)
            ):
                self.store.append(
                    program, "PROGRAM_WORLD_COLLECTED", {"queue_id": qid, "world_ref": world_ref}
                )
        if (
            stopped
            or (current and current["stop_reason"])
            or len(batches) >= spec.max_direction_batches
        ):
            self.store.set_status(program, "CLOSED")
            return {"program_id": program, "queue_id": qid, "status": "CLOSED"}
        if prepared:
            raw = DirectionBatch.model_validate(self.store.artifacts.get(prepared["batch_ref"]))
            if not raw.experiments:
                self._admit(program, qid, prepared)
                self.store.set_status(program, "CLOSED")
                return {"program_id": program, "queue_id": qid, "status": "CLOSED"}
            child_spec = spec.queue.model_copy(
                update={"prior_queues": tuple(dict.fromkeys([*spec.queue.prior_queues, *links]))}
            )
            child = self.queue.create(child_spec)
            self.store.append(program, "RESEARCH_QUEUE_ATTACHED", {"queue_id": child})
            self.queue.enqueue(child, self._baseline())
            self.queue.adopt_baseline(child, qid)
            self._admit(program, child, prepared)
            return {"program_id": program, "queue_id": child, "status": "NEXT_EPOCH_READY"}
        return {"program_id": program, "queue_id": qid, "status": "NEEDS_DIRECTION"}


def verify_program(store: Store, program: str) -> dict[str, Any]:
    manifest = store.manifest(program)
    spec = ResearchProgramSpec.model_validate(manifest["spec"])
    identity = {"spec": manifest["spec"], "provider": manifest["provider"]}
    if spec.critic:
        identity["critic_provider"] = manifest["critic_provider"]
    if manifest["world_id"] != digest(identity):
        raise IntegrityError("program specification changed")
    verify_roles(store, program)
    if spec.critic:
        from jarvis.research_critic import verify_reviewed_program

        return verify_reviewed_program(store, program)
    events = store.events(program)
    links: list[str] = []
    completed_refs: dict[str, DirectionBatch] = {}
    for event in events:
        p = event["payload"]
        if event["event_type"] == "RESEARCH_QUEUE_ATTACHED":
            qid = p["queue_id"]
            expected = (
                spec.queue.model_copy(
                    update={
                        "prior_queues": tuple(dict.fromkeys([*spec.queue.prior_queues, *links]))
                    }
                )
                if links
                else spec.queue
            )
            if (
                IrisQueueSpec.model_validate(store.manifest(qid)["spec"]) != expected
                or qid in links
            ):
                raise IntegrityError("program queue escaped its fixed controls")
            verify_queue(store, qid)
            links.append(qid)
        if event["event_type"] == "ROLE_COMPLETED":
            job = next(j for j in store.jobs(program) if j["id"] == p["job_id"])
            request = store.artifacts.get(job["request_ref"])
            context = request["context"]
            start = next(
                e
                for e in events
                if e["event_type"] == "JOB_RESERVED" and e["payload"]["job_id"] == job["id"]
            )
            available = {
                e["payload"]["result_ref"]
                for q in [*spec.queue.prior_queues, *links]
                for e in store.events(q)
                if e["event_type"] == "IRIS_RESULT_RECORDED"
                and e["recorded_at_utc"] <= start["recorded_at_utc"]
            }
            if (
                request["provider"] != manifest["provider"]
                or context["goal"] != spec.queue.goal
                or not set(context["visible_evidence_ids"]) <= available
            ):
                raise IntegrityError("direction context used unavailable evidence or changed goal")
            for observation in context["observations"]:
                if observation["result_ref"] not in available or any(
                    store.artifacts.get(observation["result_ref"])[k] != v
                    for k, v in observation["result"].items()
                ):
                    raise IntegrityError("direction observation differs from its evidence")
            # Raw structured JSON may represent a declared float as an integer.
            # Preserve that original receipt, compare batch identity through the
            # same declared model used when admitting the proposal.
            if p.get("error") is None:
                completed_refs[p["receipt_ref"]] = DirectionBatch.model_validate(
                    store.artifacts.get(p["answer_ref"])
                )
        if event["event_type"] in {"DIRECTION_BATCH_PREPARED", "DIRECTION_BATCH_ADMITTED"}:
            if completed_refs.get(p["receipt_ref"]) != DirectionBatch.model_validate(
                store.artifacts.get(p["batch_ref"])
            ):
                raise IntegrityError("direction batch lacks its observed role receipt")
            batch = DirectionBatch.model_validate(store.artifacts.get(p["batch_ref"]))
            if batch.context_id != p["context_id"] or any(
                not set(t.evidence_refs) <= set(p["visible_refs"]) for t in batch.experiments
            ):
                raise IntegrityError("direction batch context changed")
        if event["event_type"] == "PROGRAM_WORLD_COLLECTED":
            if (
                verify_world(store, p["world_ref"])["queue_id"] != p["queue_id"]
                or p["queue_id"] not in links
            ):
                raise IntegrityError("program history belongs to another queue")
    return {"status": "VERIFIED", "program_id": program, "queues": len(links), "external_calls": 0}


def program_report(store: Store, program: str) -> dict[str, Any]:
    manifest = store.manifest(program)
    spec = ResearchProgramSpec.model_validate(manifest["spec"])
    program_verification = verify_program(store, program)
    links = [
        e["payload"]["queue_id"]
        for e in store.events(program)
        if e["event_type"] == "RESEARCH_QUEUE_ATTACHED"
    ]
    qid = links[-1] if links else None
    verification = [verify_queue(store, q) for q in links]
    results = [r for q in links for r in visible_results(store, q)]
    valid = [r for r in results if r["result"]["status"] == "SUCCEEDED"]
    best = max(valid, key=lambda r: r["result"]["entry_only_pl"]) if valid else None
    baseline = next((r["result"] for r in valid if r["experiment"]["family"] == "baseline"), None)
    comparisons = [
        {
            "result_ref": r["result_ref"],
            "experiment": r["experiment"]["label"],
            "delta_from_baseline": {
                k: r["result"][k] - baseline[k]
                for k in ["entry_only_pl", "mdd", "worst_day", "count"]
            },
            "risk": r["result"]["risk"],
        }
        for r in valid
        if baseline and r["experiment"]["family"] != "baseline"
    ]
    assessments = [
        {"result_ref": r["result_ref"], **assess_candidate(r["result"], baseline, spec.acceptance)}
        for r in valid
        if baseline and spec.acceptance and r["experiment"]["family"] != "baseline"
    ]
    qualified_refs = {a["result_ref"] for a in assessments if a["qualified"]}
    qualified = max(
        (r for r in valid if r["result_ref"] in qualified_refs),
        key=lambda r: r["result"]["entry_only_pl"],
        default=None,
    )
    usage_rows = [store.resource_totals(p) for p in [program, *links]]
    return {
        "kind": "ResearchProgramReport",
        "program_id": program,
        "status": store.status(program),
        "goal": spec.queue.goal,
        "primary_metric": "entry_only_pl",
        "risk_policy": spec.queue.risk_policy,
        "scope": spec.queue.environment.period.exposure,
        "best_development_candidate": best,
        **(
            {
                "selection_constraints": spec.acceptance.model_dump(mode="json"),
                "candidate_assessments": assessments,
                "best_qualified_candidate": qualified,
            }
            if spec.acceptance
            else {}
        ),
        "experiments": results,
        "baseline_comparisons": comparisons,
        "queue_id": qid,
        "verification": verification,
        "program_verification": program_verification,
        "direction_batches": [
            {**e["payload"], "batch": store.artifacts.get(e["payload"]["batch_ref"])}
            for e in store.events(program)
            if e["event_type"] == "DIRECTION_BATCH_ADMITTED"
        ],
        "world_refs": [
            e["payload"]["world_ref"]
            for e in store.events(program)
            if e["event_type"] == "PROGRAM_WORLD_COLLECTED"
        ],
        "research_usage": [store.resource_totals(q) for q in links],
        "direction_usage": role_resources(store, program, "direction"),
        "critic_usage": role_resources(store, program, "critic"),
        "llm_usage": store.resource_totals(program),
        "full_usage": {
            section: {
                key: sum(row[section][key] for row in usage_rows) for key in usage_rows[0][section]
            }
            for section in ["actual", "pending"]
        },
        "shared_budget_enforced": spec.execution_budget is not None,
        "research_policy": spec.research_policy.model_dump(mode="json")
        if spec.research_policy
        else None,
        "cost_scope": "all role, compile, import and backend jobs; local work seconds; remote compute/queue and monetary cost unavailable",
        "role_calls": {
            role: sum(
                e["event_type"] == "ROLE_COMPLETED" and e["payload"]["role"] == role
                for e in store.events(program)
            )
            for role in ["direction", "critic"]
        },
        "review_policy": "independent pre-admission veto and post-result advice; fixed numeric evaluator"
        if spec.critic
        else "legacy combined direction role",
        "critic_reviews": [
            {**e["payload"], "review": store.artifacts.get(e["payload"]["review_ref"])}
            for e in store.events(program)
            if e["event_type"] == "CRITIC_REVIEW_RECORDED"
        ],
        "formal_strategy_validation": "NOT_RUN",
        "deployment": "NOT_RUN",
    }
