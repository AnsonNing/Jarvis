"""Small decision-time planning reviews, distinct branch stops and owner handoffs.

Priorities and effort are agent estimates, not evaluator results. This module checks
consistency/visibility; it does not score scientific merit or demand more experiments.
"""

from typing import Any

from jarvis.autonomy_contracts import AutonomyContract, AutonomyDecision, StopAction
from jarvis.storage import Store


def review_due(store: Store, state: dict[str, Any]) -> list[str]:
    latest = state.get("planning_review")
    reasons = []
    if latest is None:
        reasons.append("initial research framing")
    else:
        for index, entry in state["reviews"].items():
            review = store.artifacts.get(entry["review_ref"])
            if index >= latest["index"] and review["recommendation"] in {
                "close_branch",
                "invalid_result",
            }:
                reasons.append("reconsider alternatives after branch criticism")
                break
    if any(state["guidance"][g]["mode"] == "redirect" for g in state["pending_guidance"]):
        reasons.append("owner redirect")
    if state.get("owner_checkpoint"):
        reasons.append("resume from owner checkpoint")
    return reasons


def planning_view(store: Store, state: dict[str, Any]) -> dict[str, Any]:
    latest = state.get("planning_review")
    return {
        "latest_review": latest,
        "review_due": review_due(store, state),
        "owner_checkpoint": state.get("owner_checkpoint"),
        "semantics": "Directions, priorities and effort are proposals/estimates, not measured outcomes. Untried directions remain untested.",
    }


def validate_planning(
    store: Store,
    contract: AutonomyContract,
    state: dict[str, Any],
    decision: AutonomyDecision,
    frozen: dict[str, Any],
) -> None:
    action, review = decision.action, decision.planning_review
    checkpoint = isinstance(action, StopAction) and action.scope == "checkpoint"
    if contract.planning is None:
        if review is not None or checkpoint:
            raise ValueError("collaborative planning requires an opted-in contract")
        return
    if frozen["research_plan"].get("planning", {}).get("latest_review") != state.get(
        "planning_review"
    ):
        raise ValueError("planning context differs from decision-time recorded review")
    handoff = state.get("owner_checkpoint")
    if handoff and not handoff["has_owner_reply"]:
        raise ValueError("owner checkpoint awaits a new guidance reply")
    required = (
        review_due(store, state) or decision.branch is not None or isinstance(action, StopAction)
    )
    if required and review is None:
        raise ValueError(
            "planning review required at initial framing, branch changes, redirects or stopping"
        )
    if review is None:
        return
    visible = set(frozen["visible_evidence_ids"])
    directions = {d.direction_id: d for d in review.directions}
    previous = state.get("planning_review")
    if previous:
        pending = {
            d["direction_id"]
            for d in previous["review"]["directions"]
            if d["disposition"] != "retired"
        }
        if not pending <= set(directions):
            raise ValueError(
                "account for pending directions explicitly; do not silently drop alternatives"
            )
    available = frozen.get("executors_available", {})
    for direction in directions.values():
        if not set(direction.hypothesis.evidence_refs) <= visible:
            raise ValueError("planning hypotheses may cite only visible evidence")
        if direction.disposition != "ready":
            continue
        kind = direction.next_action
        usable = kind in contract.grants.actions and kind not in {"WAIT", "STOP"}
        usable &= not (kind == "BACKTEST" and not available.get("backtest"))
        usable &= not (kind == "TRAIN_MODEL" and not available.get("train_model"))
        usable &= not (kind == "IMPLEMENT" and not available.get("implement_targets"))
        usable &= not (kind == "READ_SOURCE" and not contract.source_scopes)
        if not usable:
            raise ValueError(
                "ready direction needs an available granted action; defer/block unavailable tools"
            )
        branch = state["branches"].get(direction.direction_id, {})
        if branch.get("status") == "closed":
            raise ValueError("closed branch cannot remain a ready direction")
    if isinstance(action, StopAction) or action.kind == "WAIT":
        if review.selected_direction_id is not None:
            raise ValueError("a stop/wait does not execute a proposed direction")
    else:
        selected = directions.get(review.selected_direction_id or "")
        if selected is None or selected.disposition != "ready":
            raise ValueError("select one ready direction in a planning review")
        branch_kind = (
            decision.branch.kind
            if decision.branch
            else state["branches"].get(action.branch_id, {}).get("kind")
        )
        if (
            selected.direction_id != action.branch_id
            or selected.kind != branch_kind
            or selected.next_action != action.kind
            or selected.hypothesis.hypothesis_id not in action.hypothesis_ids
        ):
            raise ValueError(
                "selected direction must match the action, branch and addressed hypothesis"
            )
        questions = {q["question_id"]: q for q in frozen["research_plan"]["questions"]}
        if decision.question:
            questions[decision.question.question_id] = decision.question.model_dump(mode="json")
        declared: dict[str, Any] = next(
            (
                h
                for h in questions.get(action.question_id, {}).get("hypotheses", [])
                if h["hypothesis_id"] == selected.hypothesis.hypothesis_id
            ),
            {},
        )
        if any(
            declared.get(k) != getattr(selected.hypothesis, k)
            for k in ("claim", "prediction", "falsification")
        ):
            expected = {k: declared.get(k) for k in ("claim", "prediction", "falsification")}
            raise ValueError(
                "planning must preserve the addressed hypothesis meaning; copy the declared "
                f"claim/prediction/falsification for {selected.hypothesis.hypothesis_id!r} "
                f"in question {action.question_id!r} exactly: {expected!r}. "
                "Update the direction reason/next_action, or declare a new question/hypothesis "
                "for a changed scientific claim."
            )
    if checkpoint:
        if review.stop_basis != "owner_checkpoint" or not review.owner_question:
            raise ValueError(
                "owner checkpoint needs a concrete question and owner_checkpoint basis"
            )
    elif isinstance(action, StopAction) and action.scope == "session":
        if review.owner_question or not review.directions:
            raise ValueError(
                "mission stop needs considered directions; use a checkpoint for owner questions"
            )
        if action.conclusion == "research_complete":
            if review.stop_basis != "goal_met":
                raise ValueError(
                    "successful mission stop requires goal_met (candidate gates still apply)"
                )
        elif review.stop_basis != "no_justified_next_step":
            raise ValueError(
                "unsuccessful mission stop needs its own no_justified_next_step review"
            )
        if review.stop_basis == "no_justified_next_step" and any(
            d.disposition == "ready" for d in directions.values()
        ):
            raise ValueError("branch failure is not mission exhaustion: ready alternatives remain")
    elif review.stop_basis is not None or review.owner_question is not None:
        raise ValueError("branch changes do not establish mission stop or owner checkpoint")
