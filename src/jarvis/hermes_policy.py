"""Hermes exploration DSL and exact replay of predeclared independent branches."""

from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.contracts import Cost, IntegrityError, Record
from jarvis.identity import digest
from jarvis.iris_contracts import Family, IrisExperiment, IrisQueueSpec
from jarvis.research_queue import queue_tasks, verify_queue
from jarvis.storage import Store

PRIORITY: tuple[Family, ...] = ("entry", "cancel", "timing", "heuristic", "exit", "joint")


class HermesPolicy(Record):
    kind: Literal["HermesExplorationPolicy"] = "HermesExplorationPolicy"
    name: str = Field(min_length=1, max_length=80)
    priority: tuple[Family, ...] = PRIORITY
    after_loss_priority: tuple[Family, ...] = PRIORITY
    stop_after_no_gain: int = Field(default=3, ge=1, le=8, strict=True)
    rationale: str = Field(min_length=1, max_length=1600)
    origin: Literal["human_provided", "agent_proposed", "declared_fixture"]

    @model_validator(mode="after")
    def families(self) -> "HermesPolicy":
        if any(
            len(set(p)) != len(p) or set(p) != set(PRIORITY)
            for p in (self.priority, self.after_loss_priority)
        ):
            raise ValueError("policy must declare each supported research family exactly once")
        return self


def q0() -> HermesPolicy:
    return HermesPolicy(
        name="Hermes-Q0",
        rationale="Fixed entry/cancel-first research comparator",
        origin="human_provided",
    )


class HermesWorldPlan(Record):
    kind: Literal["HermesWorldPlan"] = "HermesWorldPlan"
    queue: IrisQueueSpec
    trials: tuple[IrisExperiment, ...] = Field(min_length=1, max_length=16)
    family_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def candidates(self) -> "HermesWorldPlan":
        if self.trials[0].family != "baseline" or any(
            t.family == "baseline" for t in self.trials[1:]
        ):
            raise ValueError("one initial baseline required")
        if len({digest(t.settings) for t in self.trials}) != len(self.trials):
            raise ValueError("duplicate parameter design")
        return self


def seal_world(store: Store, queue_id: str, family_id: str) -> str:
    sealed = [e for e in store.events(queue_id) if e["event_type"] == "HERMES_WORLD_SEALED"]
    if sealed:
        ref = str(sealed[0]["payload"]["world_ref"])
        verify_world(store, ref)
        return ref
    verify_queue(store, queue_id)
    rows = queue_tasks(store, queue_id)
    events = store.events(queue_id)
    if not rows or rows[0]["status"] != "SUCCEEDED" or any(t["status"] == "RUNNING" for t in rows):
        raise IntegrityError("baseline or remote operation incomplete")
    first_candidate_result = next(
        (
            e["sequence"]
            for e in events
            if e["event_type"] == "IRIS_RESULT_RECORDED"
            and e["payload"]["task_id"] != rows[0]["task_id"]
        ),
        len(events) + 1,
    )
    if any(
        e["sequence"] >= first_candidate_result
        for e in events
        if e["event_type"] == "RESEARCH_ENQUEUED"
    ):
        raise IntegrityError(
            "adaptive designs require a new epoch; cannot reorder future-informed branches"
        )
    trials = []
    for row in rows:
        job = next((j for j in store.jobs(queue_id) if j["id"] == row.get("job_id")), None)
        trials.append(
            {
                "task_id": row["task_id"],
                "experiment_ref": row["experiment_ref"],
                "cpp_ref": row["cpp_ref"],
                "result_ref": row.get("result_ref"),
                "cost": Cost.model_validate_json(job["actual"]).model_dump()
                if job and job["actual"]
                else None,
            }
        )
    value = {
        "kind": "HermesWorldHistory",
        "queue_id": queue_id,
        "family_id": family_id,
        "environment": store.manifest(queue_id)["spec"]["environment"],
        "scope": "predeclared independent candidate reruns; no unobserved counterfactual predictions",
        "trials": trials,
        "event_prefix_hash": events[-1]["event_hash"],
        "event_count": len(events),
    }
    ref = store.artifacts.put(value)
    if not any(
        e["event_type"] == "HERMES_WORLD_SEALED" and e["payload"]["world_ref"] == ref
        for e in events
    ):
        store.append(queue_id, "HERMES_WORLD_SEALED", {"world_ref": ref})
    return ref


def verify_world(store: Store, ref: str) -> dict[str, Any]:
    value = store.artifacts.get(ref)
    if value["kind"] != "HermesWorldHistory":
        raise IntegrityError("not a Hermes world")
    verify_queue(store, value["queue_id"])
    events = store.events(value["queue_id"])
    if (
        not any(
            e["event_type"] == "HERMES_WORLD_SEALED" and e["payload"]["world_ref"] == ref
            for e in events
        )
        or events[value["event_count"] - 1]["event_hash"] != value["event_prefix_hash"]
    ):
        raise IntegrityError("world seal/prefix mismatch")
    rows = queue_tasks(store, value["queue_id"])
    if value["environment"] != store.manifest(value["queue_id"])["spec"]["environment"]:
        raise IntegrityError("world environment differs from its queue")
    if len(rows) != len(value["trials"]):
        raise IntegrityError("world catalog changed after sealing")
    for trial, row in zip(value["trials"], rows, strict=True):
        if any(
            trial[k] != row.get(k) for k in ["task_id", "experiment_ref", "cpp_ref", "result_ref"]
        ):
            raise IntegrityError("world branch identity changed")
        if trial["result_ref"]:
            job = next(j for j in store.jobs(value["queue_id"]) if j["id"] == row["job_id"])
            if Cost.model_validate_json(job["actual"]) != Cost.model_validate(trial["cost"]):
                raise IntegrityError("historical branch cost changed")
    return value


def choose_task(
    store: Store,
    trials: list[dict[str, Any]],
    observed: list[dict[str, Any]],
    policy: HermesPolicy,
    budget: int,
) -> str | None:
    used = {t["task_id"] for t in observed}
    if not observed:
        return str(trials[0]["task_id"])
    if len(observed) - 1 >= budget:
        return None
    base = observed[0]["result"].get("entry_only_pl")
    if base is None:
        return None
    best = float(base)
    stagnant = 0
    for t in observed[1:]:
        value = t["result"].get("entry_only_pl")
        if value is not None and value > best:
            best = float(value)
            stagnant = 0
        else:
            stagnant += 1
    if stagnant >= policy.stop_after_no_gain:
        return None
    last = observed[-1]["result"].get("entry_only_pl")
    priority = policy.after_loss_priority if last is None or last < base else policy.priority
    available = [t for t in trials if t["task_id"] not in used]
    if not available:
        return None

    def key(t: dict[str, Any]) -> tuple[int, str]:
        experiment = IrisExperiment.model_validate(store.artifacts.get(t["experiment_ref"]))
        return (priority.index(experiment.family), str(t["task_id"]))

    return str(min(available, key=key)["task_id"])


def replay_hermes_world(
    store: Store, world_ref: str, policy: HermesPolicy, budget: int
) -> dict[str, Any]:
    world = verify_world(store, world_ref)
    observed: list[dict[str, Any]] = []
    cost = Cost().dimensions()
    manifest = store.manifest(world["queue_id"])
    refs = {
        e["payload"]["result_ref"]
        for q in manifest["spec"]["prior_queues"]
        for e in store.events(q)
        if e["event_type"] == "IRIS_RESULT_RECORDED"
        and e["recorded_at_utc"] <= manifest["created_at"]
    }
    status = "COMPLETE"
    while True:
        task_id = choose_task(store, world["trials"], observed, policy, budget)
        if task_id is None:
            break
        trial = next(t for t in world["trials"] if t["task_id"] == task_id)
        experiment = IrisExperiment.model_validate(store.artifacts.get(trial["experiment_ref"]))
        if not trial["result_ref"] or not set(experiment.evidence_refs) <= refs:
            status = "INSUFFICIENT_COVERAGE"
            break
        for k, v in Cost.model_validate(trial["cost"]).dimensions().items():
            cost[k] += v
        result = store.artifacts.get(trial["result_ref"])
        observed.append({"task_id": task_id, "result": result, "result_ref": trial["result_ref"]})
        refs.add(trial["result_ref"])
    valid = [o for o in observed if o["result"]["status"] == "SUCCEEDED"]
    best = max(valid, key=lambda o: o["result"]["entry_only_pl"]) if valid else None
    return {
        "status": status,
        "world_ref": world_ref,
        "policy_id": digest(policy),
        "family_id": world["family_id"],
        "best_entry_only_pl": best["result"]["entry_only_pl"] if best else None,
        "best_result_ref": best["result_ref"] if best else None,
        "trace": [o["task_id"] for o in observed],
        "historical_cost": cost,
        "external_calls": 0,
        "scope": "EXACT_RECORDED_BRANCHES_ONLY",
    }


def compare_policies(
    store: Store,
    refs: tuple[str, ...],
    candidate: HermesPolicy,
    incumbent: HermesPolicy,
    budget: int,
    *,
    strict: bool,
) -> dict[str, Any]:
    pairs = [
        {
            "candidate": replay_hermes_world(store, ref, candidate, budget),
            "incumbent": replay_hermes_world(store, ref, incumbent, budget),
        }
        for ref in refs
    ]
    covered = bool(pairs) and all(
        p[arm]["status"] == "COMPLETE" for p in pairs for arm in ["candidate", "incumbent"]
    )
    nonreg = covered and all(
        p["candidate"]["best_entry_only_pl"] >= p["incumbent"]["best_entry_only_pl"] for p in pairs
    )
    gain = nonreg and any(
        p["candidate"]["best_entry_only_pl"] > p["incumbent"]["best_entry_only_pl"]
        or p["candidate"]["historical_cost"]["tool_calls"]
        < p["incumbent"]["historical_cost"]["tool_calls"]
        for p in pairs
    )
    return {
        "complete": covered,
        "nonregression": nonreg,
        "strict_gain": gain,
        "accepted": gain if strict else nonreg,
        "pairs": pairs,
        "metric": "per-world entry-only PL first; cost only breaks quality ties; risk retained in result receipts",
    }
