"""Pay then reveal exact realized edges; no execution/evaluator/provider imports."""

from typing import Any

from jarvis.contracts import Cost, ResearchState
from jarvis.discovery import fits, verify_tree
from jarvis.exploration import ExplorationPolicy, choose
from jarvis.policy import admit
from jarvis.storage import Store


def replay_tree(
    store: Store, ref: str, policy: ExplorationPolicy, target: float = 0.9
) -> dict[str, Any]:
    tree = verify_tree(store, ref)
    state_ref = tree.root
    cost = Cost().dimensions()
    trace: list[dict[str, Any]] = []
    decisions = 0
    while True:
        state = ResearchState.model_validate(store.artifacts.get(state_ref))
        decision = choose(policy, state, target)
        admit(decision, state)
        decisions += 1
        action = next(a for a in state.available_actions if a.action_id == decision.action_id)
        values = [
            o.metrics["objective_score"]
            for o in state.observations
            if "objective_score" in o.metrics and o.status == "SUCCEEDED"
        ]
        best = max(values) if values else None
        if action.action_type == "STOP" or not fits(state, action.estimated_cost):
            return {
                "status": "COMPLETE",
                "termination": "STOP" if action.action_type == "STOP" else "BUDGET_EXHAUSTED",
                "scope": "REPLAY_ONLY",
                "policy_id": policy.identity,
                "tree_ref": ref,
                "cost": cost,
                "best_revealed_value": best,
                "success": bool(best is not None and best >= target),
                "covered_decisions": len(trace) + 1,
                "decisions": decisions,
                "trace": trace,
                "external_calls": 0,
            }
        edge = next(
            (
                e
                for e in tree.edges
                if e.before == state_ref
                and store.artifacts.get(e.request_ref)["action"] == action.model_dump(mode="json")
            ),
            None,
        )
        if edge is None:
            return {
                "status": "INSUFFICIENT_COVERAGE",
                "reason": "UNKNOWN_ACTION",
                "scope": "REPLAY_ONLY",
                "state_ref": state_ref,
                "action_id": action.action_id,
                "score": None,
                "success": None,
                "cost": cost,
                "trace": trace,
                "covered_decisions": len(trace),
                "decisions": decisions,
                "external_calls": 0,
            }
        receipt = store.artifacts.get(edge.receipt_ref)
        paid = Cost.model_validate(receipt["cost"])
        if not fits(state, paid):
            return {
                "status": "INSUFFICIENT_COVERAGE",
                "reason": "RECORDED_COST_EXCEEDS_BUDGET",
                "score": None,
                "success": None,
                "cost": cost,
                "trace": trace,
                "external_calls": 0,
            }
        for key, value in paid.dimensions().items():
            cost[key] += value
        trace.append(
            {
                "state_ref": state_ref,
                "action_id": action.action_id,
                "receipt_ref": edge.receipt_ref,
                "evaluation_ref": edge.evaluation_ref,
                "origin": "REPLAYED",
            }
        )
        state_ref = edge.after


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    complete = all(r["status"] == "COMPLETE" for r in results)
    return {
        "complete": complete,
        "worlds": len(results),
        "successes": sum(bool(r.get("success")) for r in results) if complete else None,
        "tool_calls": sum(r["cost"]["tool_calls"] for r in results),
        "coverage": sum(r.get("covered_decisions", 0) for r in results)
        / max(1, sum(r.get("decisions", 1) for r in results)),
        "results": results,
        "scope": "REPLAY_ONLY",
        "external_calls": 0,
    }
