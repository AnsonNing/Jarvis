"""Independent, receipt-backed proposal/result critique; no evaluator authority."""

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from jarvis.contracts import AdmissionError, BudgetExceeded, IntegrityError, Record
from jarvis.hermes_policy import seal_world, verify_world
from jarvis.identity import digest
from jarvis.iris_contracts import IrisExperiment, IrisQueueSpec
from jarvis.research_queue import TERMINAL, queue_tasks, verify_queue
from jarvis.research_roles import DirectionBatch, RoleCaller, strict_schema
from jarvis.storage import Store

if TYPE_CHECKING:
    from jarvis.research_program import ResearchProgram


class CriticAssessment(Record):
    experiment_ref: str
    verdict: Literal[
        "approve",
        "revise",
        "reject",
        "supported",
        "negative",
        "inconclusive",
        "invalid",
        "not_executed",
    ]
    rationale: str = Field(min_length=1, max_length=2000)
    concerns: tuple[str, ...] = Field(max_length=8)
    evidence_refs: tuple[str, ...] = Field(max_length=64)
    next_step: str = Field(min_length=1, max_length=2000)


class CriticReview(Record):
    kind: Literal["ResearchCritique"] = "ResearchCritique"
    context_id: str
    phase: Literal["proposal", "results"]
    batch_ref: str
    summary: str = Field(min_length=1, max_length=5000)
    assessments: tuple[CriticAssessment, ...] = Field(max_length=3)
    limitations: tuple[str, ...] = Field(min_length=1, max_length=8)
    capability_requests: tuple[str, ...] = Field(max_length=8)
    stop_recommended: bool


def validate_review(review: CriticReview, context: dict[str, Any]) -> None:
    refs = {e["experiment_ref"] for e in context["proposals"]}
    actual = [a.experiment_ref for a in review.assessments]
    if (
        review.context_id != digest(context)
        or review.batch_ref != context["batch_ref"]
        or review.phase != context["phase"]
        or len(actual) != len(set(actual))
        or set(actual) != refs
    ):
        raise AdmissionError("Critic context, batch or proposal coverage mismatch")
    allowed = (
        {"approve", "revise", "reject"}
        if review.phase == "proposal"
        else {
            "supported",
            "negative",
            "inconclusive",
            "invalid",
            "not_executed",
        }
    )
    outcomes = {o["experiment_ref"]: o for o in context["outcomes"]}
    for a in review.assessments:
        if a.verdict not in allowed or not set(a.evidence_refs) <= set(
            context["visible_evidence_ids"]
        ):
            raise AdmissionError("Critic verdict or evidence escaped declared phase")
        if review.phase == "results":
            outcome = outcomes[a.experiment_ref]
            if (a.verdict == "not_executed") != (outcome["status"] == "NOT_EXECUTED"):
                raise AdmissionError("Critic confused observed and unexecuted outcomes")
            if outcome["result_ref"] and outcome["result_ref"] not in a.evidence_refs:
                raise AdmissionError("Critic must cite the experiment's actual result")
            if a.verdict in {"supported", "negative"} and outcome["status"] != "SUCCEEDED":
                raise AdmissionError("Critic inferred a strategy result from execution failure")


def _events(store: Store, program: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in store.events(program) if e["event_type"] == kind]


def _call(
    engine: "ResearchProgram",
    program: str,
    key: str,
    role: str,
    context: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Freeze before charging; restart reuses the exact context and paid receipt."""
    from jarvis.research_program import ResearchProgramSpec

    store = engine.store
    manifest = store.manifest(program)
    spec = ResearchProgramSpec.model_validate(manifest["spec"])
    prior = next(
        (p for p in _events(store, program, "PROGRAM_ROLE_REQUESTED") if p["key"] == key), None
    )
    if prior is None:
        prior = {
            "key": key,
            "role": role,
            "context_ref": store.artifacts.put(context),
            "schema_ref": store.artifacts.put(schema),
        }
        store.append(program, "PROGRAM_ROLE_REQUESTED", prior)
    if prior["role"] != role:
        raise IntegrityError("frozen research role changed")
    config = spec.critic if role == "critic" else spec.agent
    assert config is not None
    factory = engine.critic_provider_factory if role == "critic" else engine.provider_factory
    provider = factory(config)
    identity = manifest["critic_provider"] if role == "critic" else manifest["provider"]
    if provider.identity() != identity:
        raise IntegrityError("declared research role provider changed")
    guidance = None
    if spec.support_ref:
        from jarvis.lab_contracts import ResearchSupport

        method = ResearchSupport.model_validate(store.artifacts.get(spec.support_ref)).method
        guidance = method.critic_guidance if role == "critic" else method.director_guidance
    return RoleCaller(store, provider, config).call(
        program,
        role,
        store.artifacts.get(prior["context_ref"]),
        store.artifacts.get(prior["schema_ref"]),
        role_budget=True,
        guidance=guidance,
    )


def _context(store: Store, program: str, qid: str, batch_ref: str, phase: str) -> dict[str, Any]:
    from jarvis.research_program import direction_context

    batch = DirectionBatch.model_validate(store.artifacts.get(batch_ref))
    proposals: list[dict[str, Any]] = [
        {"experiment_ref": digest(e), "experiment": e.model_dump(mode="json")}
        for e in batch.experiments
    ]
    rows = {
        digest(IrisExperiment.model_validate(store.artifacts.get(t["experiment_ref"]))): t
        for t in queue_tasks(store, qid)[1:]
    }
    return {
        **direction_context(store, program, qid),
        "role": "critic",
        "phase": phase,
        "queue_id": qid,
        "batch_ref": batch_ref,
        "proposed_analysis": batch.analysis_summary,
        "proposals": proposals,
        "outcomes": [
            {
                "experiment_ref": p["experiment_ref"],
                "status": (
                    rows.get(p["experiment_ref"], {}).get("status", "NOT_EXECUTED")
                    if rows.get(p["experiment_ref"], {}).get("status") in TERMINAL
                    else "NOT_EXECUTED"
                ),
                "result_ref": rows.get(p["experiment_ref"], {}).get("result_ref"),
            }
            for p in proposals
        ]
        if phase == "results"
        else [],
    }


def _review(
    engine: "ResearchProgram", program: str, qid: str, batch_ref: str, phase: str
) -> dict[str, Any]:
    store = engine.store
    existing = next(
        (
            p
            for p in _events(store, program, "CRITIC_REVIEW_RECORDED")
            if p["batch_ref"] == batch_ref and p["phase"] == phase
        ),
        None,
    )
    if existing:
        return existing
    key = f"critic:{qid}:{phase}"
    context = _context(store, program, qid, batch_ref, phase)
    schema = strict_schema(CriticReview)
    schema["properties"]["context_id"]["enum"] = [digest(context)]
    schema["properties"]["phase"]["enum"] = [phase]
    schema["properties"]["batch_ref"]["enum"] = [batch_ref]
    schema["properties"]["assessments"].update(
        minItems=len(context["proposals"]), maxItems=len(context["proposals"])
    )
    if context["proposals"]:
        schema["$defs"]["CriticAssessment"]["properties"]["experiment_ref"]["enum"] = [
            p["experiment_ref"] for p in context["proposals"]
        ]
    schema["$defs"]["CriticAssessment"]["properties"]["evidence_refs"]["items"]["enum"] = context[
        "visible_evidence_ids"
    ]
    schema["$defs"]["CriticAssessment"]["properties"]["verdict"]["enum"] = (
        ["approve", "revise", "reject"]
        if phase == "proposal"
        else ["supported", "negative", "inconclusive", "invalid", "not_executed"]
    )
    response = _call(engine, program, key, "critic", context, schema)
    # On recovery the frozen request, not a newly assembled context, owns identity.
    frozen = next(p for p in _events(store, program, "PROGRAM_ROLE_REQUESTED") if p["key"] == key)
    context = store.artifacts.get(frozen["context_ref"])
    review = CriticReview.model_validate(response["answer"])
    validate_review(review, context)
    payload = {
        "key": key,
        "queue_id": qid,
        "phase": phase,
        "batch_ref": batch_ref,
        "context_id": review.context_id,
        "receipt_ref": response["receipt_ref"],
        "review_ref": store.artifacts.put(review),
    }
    store.append(program, "CRITIC_REVIEW_RECORDED", payload)
    return payload


def advance_reviewed_program(engine: "ResearchProgram", program: str) -> dict[str, Any]:
    store = engine.store
    # Separate orchestration lock allows RoleCaller to retain its own reservation lock.
    with store.campaign_lock(program, scope="reviewed-program"):
        if store.status(program) in {
            "PAUSED",
            "CANCELLED",
            "NEEDS_RECONCILIATION",
            "CRITIC_NEEDS_ATTENTION",
            "BUDGET_EXHAUSTED",
        }:
            return {"program_id": program, "status": store.status(program)}
        try:
            return _advance(engine, program)
        except BudgetExceeded as exc:
            store.append(
                program, "RESEARCH_STOPPED", {"reason": str(exc), "kind": "BUDGET_EXHAUSTED"}
            )
            store.set_status(program, "BUDGET_EXHAUSTED")
            return {"program_id": program, "status": "BUDGET_EXHAUSTED"}
        except (AdmissionError, ValueError) as exc:
            store.append(
                program, "RESEARCH_STOPPED", {"reason": str(exc), "kind": "INVALID_ROLE_RESPONSE"}
            )
            store.set_status(program, "CRITIC_NEEDS_ATTENTION")
            raise


def _advance(engine: "ResearchProgram", program: str) -> dict[str, Any]:
    from jarvis.research_program import ResearchProgramSpec, direction_context

    store = engine.store
    spec = ResearchProgramSpec.model_validate(store.manifest(program)["spec"])
    links = [p["queue_id"] for p in _events(store, program, "RESEARCH_QUEUE_ATTACHED")]
    if not links:
        qid = engine.queue.create(
            spec.queue, budget_owner=program if spec.execution_budget else None
        )
        store.append(program, "RESEARCH_QUEUE_ATTACHED", {"queue_id": qid})
        links = [qid]
    qid = links[-1]
    answer = {"program_id": program, "queue_id": qid}
    rows = queue_tasks(store, qid)
    if not rows:
        engine.queue.enqueue(qid, engine._baseline())
        rows = queue_tasks(store, qid)
    if rows[0]["status"] == "QUEUED":
        for prior in reversed([*spec.queue.prior_queues, *links[:-1]]):
            if (
                IrisQueueSpec.model_validate(store.manifest(prior)["spec"]).environment
                == spec.queue.environment
                and queue_tasks(store, prior)[0]["status"] == "SUCCEEDED"
            ):
                engine.queue.adopt_baseline(qid, prior)
                rows = queue_tasks(store, qid)
                break
    if rows[0]["status"] != "SUCCEEDED":
        return {**answer, **engine.queue.tick(qid)}
    prepared = next(
        (p for p in _events(store, program, "DIRECTION_BATCH_PREPARED") if p["queue_id"] == qid),
        None,
    )
    if prepared is None:
        context = direction_context(store, program, qid)
        schema = strict_schema(DirectionBatch)
        schema["properties"]["context_id"]["enum"] = [digest(context)]
        schema["properties"]["experiments"]["maxItems"] = spec.experiments_per_batch
        schema["$defs"]["IrisExperiment"]["properties"]["origin"]["enum"] = ["agent_proposed"]
        schema["$defs"]["IrisExperiment"]["properties"]["evidence_refs"]["items"]["enum"] = context[
            "visible_evidence_ids"
        ]
        response = _call(engine, program, f"direction:{qid}", "direction", context, schema)
        batch = DirectionBatch.model_validate(response["answer"])
        frozen = next(
            p
            for p in _events(store, program, "PROGRAM_ROLE_REQUESTED")
            if p["key"] == f"direction:{qid}"
        )
        context = store.artifacts.get(frozen["context_ref"])
        if (
            batch.context_id != digest(context)
            or len(batch.experiments) > spec.experiments_per_batch
            or any(
                e.origin != "agent_proposed"
                or e.family == "baseline"
                or not set(e.evidence_refs) <= set(context["visible_evidence_ids"])
                for e in batch.experiments
            )
        ):
            raise AdmissionError("direction batch escaped its declared context")
        prepared = {
            "queue_id": qid,
            "context_id": batch.context_id,
            "visible_refs": context["visible_evidence_ids"],
            "receipt_ref": response["receipt_ref"],
            "batch_ref": store.artifacts.put(batch),
        }
        store.append(program, "DIRECTION_BATCH_PREPARED", prepared)
        return {**answer, "status": "AWAITING_PROPOSAL_CRITIQUE"}
    batch_ref = prepared["batch_ref"]
    pre = _review(engine, program, qid, batch_ref, "proposal")
    admitted = next(
        (p for p in _events(store, program, "DIRECTION_BATCH_ADMITTED") if p["queue_id"] == qid),
        None,
    )
    if admitted is None:
        engine._admit(program, qid, prepared, review_ref=pre["review_ref"])
        return {**answer, "status": "CRITIC_REVIEWED_ADMISSION"}
    rows = queue_tasks(store, qid)
    policy_stop = False
    if spec.research_policy:
        from jarvis.reviewed_policy import decide

        if any(t["status"] == "RUNNING" for t in rows):
            return {**answer, **engine.queue.tick(qid)}
        decision = decide(store, program, qid)
        if decision["selected_task"] is not None:
            return {**answer, **engine.queue.tick(qid, selected_task=decision["selected_task"])}
        policy_stop = decision["stop_program"]
    elif any(t["status"] not in TERMINAL for t in rows):
        return {**answer, **engine.queue.tick(qid)}
    post = _review(engine, program, qid, batch_ref, "results")
    if not any(p["queue_id"] == qid for p in _events(store, program, "PROGRAM_WORLD_COLLECTED")):
        ref = seal_world(store, qid, spec.queue.environment.period.group_id)
        store.append(program, "PROGRAM_WORLD_COLLECTED", {"queue_id": qid, "world_ref": ref})
    stop = (
        admitted["stop_reason"]
        or not store.artifacts.get(batch_ref)["experiments"]
        or store.artifacts.get(pre["review_ref"])["stop_recommended"]
        or store.artifacts.get(post["review_ref"])["stop_recommended"]
        or len(_events(store, program, "DIRECTION_BATCH_ADMITTED")) >= spec.max_direction_batches
        or policy_stop
    )
    if stop:
        store.append(program, "RESEARCH_STOPPED", {"reason": str(stop), "kind": "REVIEWED_CLOSE"})
        store.set_status(program, "CLOSED")
        return {**answer, "status": "CLOSED"}
    child = engine.queue.create(
        spec.queue.model_copy(
            update={"prior_queues": tuple(dict.fromkeys([*spec.queue.prior_queues, *links]))}
        ),
        budget_owner=program if spec.execution_budget else None,
    )
    store.append(program, "RESEARCH_QUEUE_ATTACHED", {"queue_id": child})
    return {**answer, "queue_id": child, "status": "NEXT_REVIEWED_EPOCH_READY"}


def verify_reviewed_program(store: Store, program: str) -> dict[str, Any]:
    """Causal receipt audit using recorded artifacts only; zero external operations."""
    from jarvis.research_program import ResearchProgramSpec

    manifest = store.manifest(program)
    spec = ResearchProgramSpec.model_validate(manifest["spec"])
    from jarvis.reviewed_policy import verify_policy_decisions

    verify_policy_decisions(store, program)
    links: list[str] = []
    frozen: dict[str, dict[str, Any]] = {}
    completed: dict[str, dict[str, Any]] = {}
    prepared: dict[str, dict[str, Any]] = {}
    reviews: dict[tuple[str, str], dict[str, Any]] = {}
    admitted: set[str] = set()
    worlds: set[str] = set()
    jobs = {j["id"]: j for j in store.jobs(program)}
    for event in store.events(program):
        p, kind = event["payload"], event["event_type"]
        if kind == "RESEARCH_QUEUE_ATTACHED":
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
                p["queue_id"] in links
                or IrisQueueSpec.model_validate(store.manifest(p["queue_id"])["spec"]) != expected
                or (links and links[-1] not in worlds)
            ):
                raise IntegrityError("reviewed queue escaped fixed controls or result review")
            verify_queue(store, p["queue_id"])
            if (
                spec.execution_budget
                and store.manifest(p["queue_id"]).get("budget_owner") != program
            ):
                raise IntegrityError("queue escaped shared program budget")
            links.append(p["queue_id"])
        elif kind == "PROGRAM_ROLE_REQUESTED":
            context = store.artifacts.get(p["context_ref"])
            available = {
                e["payload"]["result_ref"]
                for q in [*spec.queue.prior_queues, *links]
                for e in store.events(q)
                if e["event_type"] == "IRIS_RESULT_RECORDED"
                and e["recorded_at_utc"] <= event["recorded_at_utc"]
            }
            if spec.support_ref:
                available.add(spec.support_ref)
                if context.get("research_support") != store.artifacts.get(spec.support_ref):
                    raise IntegrityError("research support changed")
            prior_reviews = {v["review_ref"] for v in reviews.values()}
            if (
                p["key"] in frozen
                or context["goal"] != spec.queue.goal
                or context["primary_metric"] != "entry_only_pl"
                or context["role"] != p["role"]
                or context["fixed_controls"]
                != {
                    "source_sha256": spec.queue.environment.template_sha256,
                    "evaluator_sha256": spec.queue.environment.evaluator_sha256,
                    "model_lineage": spec.queue.environment.model_lineage,
                    "period": spec.queue.environment.period.model_dump(mode="json"),
                    "result_origin": spec.queue.environment.result_origin,
                    "baseline_origin": "owner_provided",
                    "comparator": "frozen_baseline",
                }
                or not set(context["visible_evidence_ids"]) <= available
                or {v["review_ref"] for v in context["critic_feedback"]} != prior_reviews
                or any(
                    v["review"] != store.artifacts.get(v["review_ref"])
                    for v in context["critic_feedback"]
                )
            ):
                raise IntegrityError("role snapshot used unavailable evidence or feedback")
            for o in context["observations"]:
                if o["result_ref"] not in context["visible_evidence_ids"] or any(
                    store.artifacts.get(o["result_ref"])[k] != v for k, v in o["result"].items()
                ):
                    raise IntegrityError("role observation differs from recorded result")
            if p["role"] == "critic":
                qid = context["queue_id"]
                if qid not in prepared or context["batch_ref"] != prepared[qid]["batch_ref"]:
                    raise IntegrityError("Critic snapshot references an unprepared batch")
                phase = context["phase"]
                if p["key"] != f"critic:{qid}:{phase}" or phase not in {"proposal", "results"}:
                    raise IntegrityError("Critic snapshot role or phase changed")
                known_rows = queue_tasks(store, qid)[1:]
                if phase == "proposal":
                    if context["outcomes"] or qid in admitted:
                        raise IntegrityError("proposal review disclosed future outcomes")
                else:
                    if qid not in admitted or any(
                        t["status"] not in TERMINAL
                        and not (spec.research_policy and t["status"] == "QUEUED")
                        for t in known_rows
                    ):
                        raise IntegrityError("result review preceded completed execution")
                    by_experiment = {
                        digest(
                            IrisExperiment.model_validate(store.artifacts.get(t["experiment_ref"]))
                        ): t
                        for t in known_rows
                    }
                    expected_outcomes = []
                    for proposal in context["proposals"]:
                        row = by_experiment.get(proposal["experiment_ref"])
                        if (
                            row
                            and row.get("result_ref")
                            and (row.get("result_ref") not in available)
                        ):
                            raise IntegrityError("result review used future execution evidence")
                        expected_outcomes.append(
                            {
                                "experiment_ref": proposal["experiment_ref"],
                                "status": row["status"]
                                if row and row["status"] in TERMINAL
                                else "NOT_EXECUTED",
                                "result_ref": row.get("result_ref") if row else None,
                            }
                        )
                    if context["outcomes"] != expected_outcomes:
                        raise IntegrityError("Critic outcome snapshot differs from execution")
            frozen[p["key"]] = {**p, "context": context}
        elif kind == "ROLE_COMPLETED":
            request = store.artifacts.get(jobs[p["job_id"]]["request_ref"])
            role = request["role"]
            config = spec.critic if role == "critic" else spec.agent
            identity = manifest["critic_provider"] if role == "critic" else manifest["provider"]
            if (
                role not in {"direction", "critic"}
                or config is None
                or request["provider"] != identity
                or request["config"] != config.model_dump(mode="json")
                or request.get("call_limit_scope") != role
                or request["world_id"] != manifest["world_id"]
                or not any(
                    f["role"] == role
                    and f["context_ref"] == digest(request["context"])
                    and f["schema_ref"] == digest(request["schema"])
                    for f in frozen.values()
                )
            ):
                raise IntegrityError("independent role lacks its frozen provider/context")
            completed[p["receipt_ref"]] = p
        elif kind == "DIRECTION_BATCH_PREPARED":
            context = frozen[f"direction:{p['queue_id']}"]["context"]
            batch = DirectionBatch.model_validate(store.artifacts.get(p["batch_ref"]))
            receipt = completed.get(p["receipt_ref"])
            if (
                receipt is None
                or receipt["role"] != "direction"
                or receipt["error"]
                or DirectionBatch.model_validate(store.artifacts.get(receipt["answer_ref"]))
                != batch
                or batch.context_id != digest(context)
                or p["context_id"] != batch.context_id
                or p["visible_refs"] != context["visible_evidence_ids"]
                or p["queue_id"] in prepared
                or p["queue_id"] != links[-1]
                or len(batch.experiments) > spec.experiments_per_batch
                or any(
                    e.origin != "agent_proposed"
                    or e.family == "baseline"
                    or not set(e.evidence_refs) <= set(p["visible_refs"])
                    for e in batch.experiments
                )
            ):
                raise IntegrityError("direction batch lacks its causal role receipt")
            prepared[p["queue_id"]] = p
        elif kind == "CRITIC_REVIEW_RECORDED":
            review = CriticReview.model_validate(store.artifacts.get(p["review_ref"]))
            f = frozen[p["key"]]
            receipt = completed.get(p["receipt_ref"])
            batch = DirectionBatch.model_validate(store.artifacts.get(p["batch_ref"]))
            if (
                receipt is None
                or receipt["role"] != "critic"
                or receipt["error"]
                or CriticReview.model_validate(store.artifacts.get(receipt["answer_ref"])) != review
                or (p["queue_id"], p["phase"]) in reviews
                or prepared[p["queue_id"]]["batch_ref"] != p["batch_ref"]
                or f["context"]["phase"] != p["phase"]
                or f["context"]["queue_id"] != p["queue_id"]
                or p["context_id"] != review.context_id
                or f["context"]["proposals"]
                != [
                    {"experiment_ref": digest(e), "experiment": e.model_dump(mode="json")}
                    for e in batch.experiments
                ]
            ):
                raise IntegrityError("Critic review lacks its independent receipt or proposal")
            try:
                validate_review(review, f["context"])
            except AdmissionError as exc:
                raise IntegrityError(str(exc)) from exc
            if p["phase"] == "results" and p["queue_id"] not in admitted:
                raise IntegrityError("result review preceded admission")
            reviews[p["queue_id"], p["phase"]] = p
        elif kind == "DIRECTION_BATCH_ADMITTED":
            pre = reviews.get((p["queue_id"], "proposal"))
            if (
                pre is None
                or p.get("critic_review_ref") != pre["review_ref"]
                or p["queue_id"] in admitted
                or any(p[k] != v for k, v in prepared[p["queue_id"]].items())
                or p["stop_reason"] != store.artifacts.get(p["batch_ref"])["stop_reason"]
                or p["capability_requests"]
                != store.artifacts.get(p["batch_ref"])["capability_requests"]
            ):
                raise IntegrityError("admission lacks independent proposal review")
            review = CriticReview.model_validate(store.artifacts.get(pre["review_ref"]))
            approvals = {
                a.experiment_ref
                for a in review.assessments
                if a.verdict == "approve" and not review.stop_recommended
            }
            for row in queue_tasks(store, p["queue_id"])[1:]:
                if (
                    digest(
                        IrisExperiment.model_validate(store.artifacts.get(row["experiment_ref"]))
                    )
                    not in approvals
                    or row["task_id"] not in p["accepted"]
                ):
                    raise IntegrityError("unapproved candidate entered execution queue")
            if any(
                e["event_type"] == "RESEARCH_ENQUEUED"
                and e["payload"]["experiment_ref"]
                != queue_tasks(store, p["queue_id"])[0]["experiment_ref"]
                and e["recorded_at_utc"]
                < next(
                    e["recorded_at_utc"]
                    for e in store.events(program)
                    if e["event_type"] == "CRITIC_REVIEW_RECORDED"
                    and e["payload"]["review_ref"] == pre["review_ref"]
                )
                for e in store.events(p["queue_id"])
            ):
                raise IntegrityError("candidate enqueued before Critic approval")
            admitted.add(p["queue_id"])
        elif kind == "PROGRAM_WORLD_COLLECTED":
            if (
                (p["queue_id"], "results") not in reviews
                or p["queue_id"] in worlds
                or verify_world(store, p["world_ref"])["queue_id"] != p["queue_id"]
            ):
                raise IntegrityError("world sealed without independent result review")
            worlds.add(p["queue_id"])
    if len(prepared) > spec.max_direction_batches or (
        store.status(program) == "CLOSED" and set(links) != worlds
    ):
        raise IntegrityError("closed reviewed program has missing review/epoch evidence")
    return {
        "status": "VERIFIED",
        "program_id": program,
        "queues": len(links),
        "critic_reviews": len(reviews),
        "external_calls": 0,
    }
