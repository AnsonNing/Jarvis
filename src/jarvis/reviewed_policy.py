"""Causal policy decisions in the actual Director/Critic execution path."""

from typing import Any

from jarvis.contracts import IntegrityError
from jarvis.hermes_policy import choose_task
from jarvis.identity import digest
from jarvis.research_queue import queue_tasks
from jarvis.storage import Store


def _stagnant(observed: list[dict[str, Any]]) -> int:
    best = observed[0]["result"]["entry_only_pl"]
    stagnant = 0
    for observation in observed[1:]:
        value = observation["result"].get("entry_only_pl")
        if value is not None and value > best:
            best, stagnant = value, 0
        else:
            stagnant += 1
    return stagnant


def policy_state(
    store: Store, program: str, queue: str, *, before: str | None = None
) -> dict[str, Any]:
    """Reconstruct results in completion order; never expose queued outcomes."""
    links = [
        e["payload"]["queue_id"]
        for e in store.events(program)
        if e["event_type"] == "RESEARCH_QUEUE_ATTACHED"
        and (before is None or e["recorded_at_utc"] <= before)
    ]
    base = queue_tasks(store, queue)[0]
    observed = [
        {
            "task_id": base["task_id"],
            "result": store.artifacts.get(base["result_ref"]),
            "result_ref": base["result_ref"],
        }
    ]
    for qid in links:
        baseline = queue_tasks(store, qid)[0]["task_id"]
        observed.extend(
            {
                "task_id": e["payload"]["task_id"],
                "result": store.artifacts.get(e["payload"]["result_ref"]),
                "result_ref": e["payload"]["result_ref"],
            }
            for e in store.events(qid)
            if e["event_type"] == "IRIS_RESULT_RECORDED"
            and e["payload"]["task_id"] != baseline
            and (before is None or e["recorded_at_utc"] <= before)
        )
    rows = queue_tasks(store, queue)
    return {
        "trials": [{"task_id": t["task_id"], "experiment_ref": t["experiment_ref"]} for t in rows],
        "observed": observed,
        "previous_count": sum(o["task_id"] not in {t["task_id"] for t in rows} for o in observed),
    }


def decide(store: Store, program: str, queue: str) -> dict[str, Any]:
    from jarvis.research_program import ResearchProgramSpec

    spec = ResearchProgramSpec.model_validate(store.manifest(program)["spec"])
    assert spec.research_policy is not None
    state = policy_state(store, program, queue)
    selected = choose_task(
        store,
        state["trials"],
        state["observed"],
        spec.research_policy,
        state["previous_count"] + spec.experiments_per_epoch,
    )
    payload = {
        "queue_id": queue,
        "policy_id": digest(spec.research_policy),
        "state_ref": store.artifacts.put(state),
        "selected_task": selected,
        "stop_program": _stagnant(state["observed"]) >= spec.research_policy.stop_after_no_gain,
    }
    existing = [
        e["payload"]
        for e in store.events(program)
        if e["event_type"] == "RESEARCH_POLICY_DECISION"
        and e["payload"]["state_ref"] == payload["state_ref"]
    ]
    if existing:
        if existing[-1] != payload:
            raise IntegrityError("policy decision changed for its frozen observation")
        return existing[-1]
    store.append(program, "RESEARCH_POLICY_DECISION", payload)
    return payload


def verify_policy_decisions(store: Store, program: str) -> None:
    from jarvis.research_program import ResearchProgramSpec

    spec = ResearchProgramSpec.model_validate(store.manifest(program)["spec"])
    decisions = [e for e in store.events(program) if e["event_type"] == "RESEARCH_POLICY_DECISION"]
    if spec.research_policy is None:
        if decisions:
            raise IntegrityError("undeclared policy decisions")
        return
    for event in decisions:
        p = event["payload"]
        state = store.artifacts.get(p["state_ref"])
        expected = policy_state(store, program, p["queue_id"], before=event["recorded_at_utc"])
        if state != expected or p["policy_id"] != digest(spec.research_policy):
            raise IntegrityError("policy snapshot used changed/future evidence")
        chosen = choose_task(
            store,
            state["trials"],
            state["observed"],
            spec.research_policy,
            state["previous_count"] + spec.experiments_per_epoch,
        )
        if chosen != p["selected_task"] or p["stop_program"] != (
            _stagnant(state["observed"]) >= spec.research_policy.stop_after_no_gain
        ):
            raise IntegrityError("recorded action does not follow the pinned policy")
    for link in [
        e["payload"]["queue_id"]
        for e in store.events(program)
        if e["event_type"] == "RESEARCH_QUEUE_ATTACHED"
    ]:
        base = queue_tasks(store, link)[0]["task_id"]
        for dispatch in store.events(link):
            if (
                dispatch["event_type"] != "IRIS_DISPATCH_RESERVED"
                or dispatch["payload"]["task_id"] == base
            ):
                continue
            prior = [
                e
                for e in decisions
                if e["payload"]["queue_id"] == link
                and e["recorded_at_utc"] <= dispatch["recorded_at_utc"]
            ]
            if not prior or prior[-1]["payload"]["selected_task"] != dispatch["payload"]["task_id"]:
                raise IntegrityError("backend dispatch escaped policy selection")
