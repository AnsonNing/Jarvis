"""Exact recorded-path replay. No adapter, evaluator, provider or network calls."""

from typing import Any

from jarvis.contracts import Cost, ResearchState
from jarvis.history import nodes, verify
from jarvis.policy import FixedPolicy, admit
from jarvis.storage import Store


def replay(store: Store, campaign_id: str, policy: FixedPolicy | None = None) -> dict[str, Any]:
    verify(store, campaign_id)
    if policy is None and store.manifest(campaign_id).get("researcher"):
        return replay_recorded_agent(store, campaign_id)
    policy = policy or FixedPolicy()
    used = Cost().dimensions()
    trace: list[dict[str, Any]] = []
    current_best: float | None = None
    sign = 1 if store.manifest(campaign_id)["task"]["metric_direction"] == "maximize" else -1
    for node in nodes(store, campaign_id):
        request = store.artifacts.get(node["request_ref"])
        state = ResearchState.model_validate(request["state"])
        decision = policy.choose(state)
        admit(decision, state)
        if decision.action_id != request["action"]["action_id"]:
            if decision.action_id == "stop":
                return {
                    "status": "COMPLETE",
                    "scope": "REPLAY_ONLY",
                    "trace": trace,
                    "cost": used,
                    "best_revealed_value": current_best,
                    "external_calls": 0,
                }
            return {
                "status": "INSUFFICIENT_COVERAGE",
                "reason": "UNKNOWN_ACTION",
                "state_hash": state.identity,
                "action_id": decision.action_id,
                "scope": "REPLAY_ONLY",
                "trace": trace,
                "cost": used,
                "score": None,
                "external_calls": 0,
            }
        template = next(a for a in state.available_actions if a.action_id == decision.action_id)
        if template.arguments != request["action"]["arguments"]:
            return {
                "status": "INSUFFICIENT_COVERAGE",
                "reason": "UNRECORDED_EXPERIMENT_DESIGN",
                "scope": "REPLAY_ONLY",
                "trace": trace,
                "cost": used,
                "score": None,
                "external_calls": 0,
            }
        receipt = store.artifacts.get(node["receipt_ref"])
        for key, value in Cost.model_validate(receipt["cost"]).dimensions().items():
            used[key] += value
        evaluation = store.artifacts.get(node["evaluation_ref"])
        value = evaluation["value"]
        if value is not None and (current_best is None or sign * value > sign * current_best):
            current_best = value
        trace.append(
            {
                "action_id": decision.action_id,
                "receipt_ref": node["receipt_ref"],
                "evaluation_ref": node["evaluation_ref"],
                "result_origin": "REPLAYED",
            }
        )
    status = "COMPLETE" if store.status(campaign_id) == "CLOSED" else "RECORDED_PREFIX_ONLY"
    return {
        "status": status,
        "scope": "REPLAY_ONLY",
        "trace": trace,
        "cost": used,
        "best_revealed_value": current_best,
        "external_calls": 0,
        "limitations": [
            "Exact recorded action path only; no policy promotion or transfer claim",
            "Costs exclude terminal formal evaluation; stored elapsed time is sequential observed execution",
        ],
    }


def replay_recorded_agent(store: Store, campaign_id: str) -> dict[str, Any]:
    """Reveal exactly paid, recorded agent decisions; never call the LLM again."""
    cost = Cost().dimensions()
    trace = []
    for event in store.events(campaign_id):
        payload = event["payload"]
        if event["event_type"] not in {"AGENT_COMPLETED", "NODE_COMPLETED"}:
            continue
        receipt = store.artifacts.get(payload["receipt_ref"])
        for key, value in Cost.model_validate(receipt["cost"]).dimensions().items():
            cost[key] += value
        if event["event_type"] == "AGENT_COMPLETED":
            trace.append(
                {
                    "type": "agent_decision",
                    "turn_ref": payload["turn_ref"],
                    "rejection": payload["rejection"],
                    "result_origin": "REPLAYED",
                }
            )
        else:
            request = store.artifacts.get(payload["request_ref"])
            trace.append(
                {
                    "type": "experiment",
                    "action_id": request["action"]["action_id"],
                    "arguments": request["action"]["arguments"],
                    "evaluation_ref": payload["evaluation_ref"],
                    "result_origin": "REPLAYED",
                }
            )
    return {
        "status": "COMPLETE" if store.status(campaign_id) == "CLOSED" else "RECORDED_PREFIX_ONLY",
        "scope": "REPLAY_ONLY",
        "policy_mode": "recorded_llm_decisions",
        "trace": trace,
        "cost": cost,
        "external_calls": 0,
        "limitations": [
            "Exact original decisions/designs only; no fresh LLM call or off-policy efficacy claim",
            "Formal evaluation costs excluded; uncertain pending calls have no invented actual usage",
        ],
    }
