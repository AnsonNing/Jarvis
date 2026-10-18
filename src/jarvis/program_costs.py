"""Full-program cost/quality comparisons, including separate researcher roles."""

from typing import Any

from jarvis.contracts import Budget, IntegrityError
from jarvis.identity import digest
from jarvis.storage import Store


def protocol_key(spec: Any) -> str:
    # Round counts may be shortened for integration. Role implementations,
    # call reservation semantics, tools and resource caps remain fixed.
    return digest(
        {
            "directions": spec.directions,
            "agent": spec.agent.model_dump(mode="json", exclude={"max_calls"}),
            "critic": spec.critic.model_dump(mode="json", exclude={"max_calls"})
            if spec.critic
            else None,
            "execution_budget": spec.execution_budget.model_dump(mode="json")
            if spec.execution_budget
            else None,
            "catalog_size": spec.experiments_per_batch,
            "experiments_per_epoch": spec.experiments_per_epoch,
        }
    )


def full_program_summary(store: Store, program: str) -> dict[str, Any]:
    from jarvis.research_program import ResearchProgramSpec, program_report

    spec = ResearchProgramSpec.model_validate(store.manifest(program)["spec"])
    report = program_report(store, program)
    queues = [
        e["payload"]["queue_id"]
        for e in store.events(program)
        if e["event_type"] == "RESEARCH_QUEUE_ATTACHED"
    ]
    if set(store.resource_members(program)) != {program, *queues}:
        raise IntegrityError("full program cost scope omitted or added a child")
    usage = store.resource_totals_tree(program)
    budget = spec.execution_budget or Budget(
        max_llm_tokens=spec.llm_token_budget, max_wall_seconds=3600
    )
    within = all(
        usage["actual"][k] + usage["pending"][k] <= v + 1e-9 for k, v in budget.limits().items()
    )
    events = store.events(program)
    from datetime import datetime

    elapsed = (
        datetime.fromisoformat(events[-1]["recorded_at_utc"])
        - datetime.fromisoformat(events[0]["recorded_at_utc"])
    ).total_seconds()
    within = within and elapsed <= budget.max_wall_seconds
    return {
        "program_id": program,
        "policy": report["research_policy"],
        "usage": usage,
        "budget": budget.model_dump(mode="json"),
        "within_budget": within,
        "complete": report["status"] == "CLOSED" and not any(usage["pending"].values()) and within,
        "best": report["best_development_candidate"],
        "role_calls": report["role_calls"],
        "protocol_id": protocol_key(spec),
        "local_elapsed_seconds": elapsed,
        "remote_compute_and_queue_seconds": None,
        "monetary_cost": None,
        "timing_scope": "observed local work sum and elapsed reported separately; no inferred remote compute or queue cost",
    }


def compare_full_programs(
    store: Store, incumbent: str, candidate: str, *, strict: bool
) -> dict[str, Any]:
    from jarvis.research_program import ResearchProgramSpec

    a, b = [
        ResearchProgramSpec.model_validate(store.manifest(p)["spec"])
        for p in (incumbent, candidate)
    ]
    if (
        a.execution_budget is None
        or a.execution_budget != b.execution_budget
        or protocol_key(a) != protocol_key(b)
        or a.queue != b.queue
        or a.max_direction_batches != b.max_direction_batches
        or a.agent != b.agent
        or a.critic != b.critic
        or store.manifest(incumbent)["provider"] != store.manifest(candidate)["provider"]
        or store.manifest(incumbent)["critic_provider"]
        != store.manifest(candidate)["critic_provider"]
    ):
        raise IntegrityError("policy arms differ in task, providers, roles or shared budget")
    arms = {
        "incumbent": full_program_summary(store, incumbent),
        "candidate": full_program_summary(store, candidate),
    }
    complete = all(v["complete"] and v["best"] is not None for v in arms.values())
    nonreg = (
        complete
        and arms["candidate"]["best"]["result"]["entry_only_pl"]
        >= arms["incumbent"]["best"]["result"]["entry_only_pl"]
    )
    better = (
        nonreg
        and arms["candidate"]["best"]["result"]["entry_only_pl"]
        > arms["incumbent"]["best"]["result"]["entry_only_pl"]
    )
    # A cost-only gain must Pareto-dominate the complete ledger; never add
    # incomparable tokens, seconds and backtests into a fabricated scalar.
    costs = [v["usage"]["actual"] for v in arms.values()]
    cheaper = (
        nonreg
        and all(costs[1][k] <= costs[0][k] for k in costs[0])
        and any(costs[1][k] < costs[0][k] for k in costs[0])
    )
    return {
        "arms": arms,
        "complete": complete,
        "nonregression": nonreg,
        "strict_gain": bool(better or cheaper),
        "accepted": bool((better or cheaper) if strict else nonreg),
        "objective": "entry_only_pl first under identical complete resource vectors; cost-only gains require vector dominance; risks retained",
    }
