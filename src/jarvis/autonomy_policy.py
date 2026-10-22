"""Research-exploration policy Q for autonomous sessions (the object Dream-RSI improves).

A policy never touches strategy parameters, evaluators or data. It shapes how research
actions are chosen: method guidance for the Director plus closed, harness-enforced rules.
"""

from typing import Any, Literal

from pydantic import Field

from jarvis.autonomy_contracts import AutonomyDecision, BranchKind
from jarvis.contracts import Record


class BranchShare(Record):
    kind: BranchKind
    max_share: float = Field(gt=0, le=1)


class ResearchPolicy(Record):
    kind: Literal["AutonomyResearchPolicy"] = "AutonomyResearchPolicy"
    name: str = Field(min_length=1, max_length=80)
    guidance: str = Field(default="", max_length=2500)
    max_consecutive_backtests: int | None = Field(default=None, ge=1, le=20)
    explain_gains_before_more_backtests: bool = False
    require_model_branch_before_stop: bool = False
    min_analyses_before_stop: int = Field(default=0, ge=0, le=10)
    max_values_per_setting: int | None = Field(default=None, ge=2, le=50)
    branch_shares: tuple[BranchShare, ...] = Field(default=(), max_length=6)
    rationale: str = Field(min_length=1, max_length=1600)
    origin: Literal["human_provided", "agent_proposed", "declared_fixture"]


def q0() -> ResearchPolicy:
    return ResearchPolicy(
        name="Autonomy-Q0",
        rationale="Fixed comparator: contract and stop guard only, no extra method rules",
        origin="human_provided",
    )


def describe(policy: ResearchPolicy) -> dict[str, Any]:
    rules = []
    if policy.max_consecutive_backtests:
        rules.append(
            f"At most {policy.max_consecutive_backtests} consecutive BACKTEST actions before a non-backtest action."
        )
    if policy.explain_gains_before_more_backtests:
        rules.append(
            "After an experiment passes an acceptance profile, ANALYZE its trades before another BACKTEST."
        )
    if policy.require_model_branch_before_stop:
        rules.append(
            "If a passing candidate changed a setting that affects model inputs, a model branch must exist before session STOP."
        )
    if policy.min_analyses_before_stop:
        rules.append(
            f"At least {policy.min_analyses_before_stop} ANALYZE actions before session STOP."
        )
    if policy.max_values_per_setting:
        rules.append(
            f"At most {policy.max_values_per_setting} distinct values per setting across backtests."
        )
    for share in policy.branch_shares:
        rules.append(f"{share.kind} branches use at most {share.max_share:.0%} of decisions.")
    return {"name": policy.name, "guidance": policy.guidance, "enforced_rules": rules}


def enforce(policy: ResearchPolicy, state: dict[str, Any], decision: AutonomyDecision) -> None:
    """Raise ValueError with a correction when a decision violates the active policy."""
    action = decision.action
    history: list[dict[str, Any]] = state["history"]
    if action.kind == "BACKTEST":
        if policy.max_consecutive_backtests:
            run = 0
            for item in reversed(history):
                if item["kind"] != "BACKTEST":
                    break
                run += 1
            if run >= policy.max_consecutive_backtests:
                raise ValueError(
                    "policy: too many consecutive backtests; analyze, read source, train or implement first"
                )
        if policy.explain_gains_before_more_backtests and state["unexplained_gains"]:
            raise ValueError(
                "policy: explain passing results first; ANALYZE at least one table of each: "
                + "; ".join(state["unexplained_gains"])
            )
        if policy.max_values_per_setting:
            tried: dict[str, set[str]] = {k: set(v) for k, v in state["setting_values"].items()}
            for experiment in action.experiments:
                current = state["candidate_settings"].get(experiment.candidate_ref, {})
                for setting in experiment.settings:
                    if current.get(setting.name) != setting.value:
                        tried.setdefault(setting.name, set()).add(repr(setting.value))
            crowded = [k for k, v in tried.items() if len(v) > policy.max_values_per_setting]
            if crowded:
                raise ValueError(
                    "policy: parameter enumeration limit reached for " + ", ".join(crowded)
                )
    kind = state["branch_kinds"].get(action.branch_id) or (
        decision.branch.kind if decision.branch else None
    )
    total = len(history) + 1
    for share in policy.branch_shares:
        used = sum(1 for h in history if h["branch_kind"] == share.kind) + (kind == share.kind)
        if kind == share.kind and total >= 6 and used / total > share.max_share:
            raise ValueError(f"policy: {share.kind} branches exceed their decision share")
    if action.kind == "STOP" and action.scope == "session":
        analyses = sum(1 for h in history if h["kind"] == "ANALYZE")
        if analyses < policy.min_analyses_before_stop:
            raise ValueError("policy: more mechanism analysis required before stopping")
        if (
            policy.require_model_branch_before_stop
            and state["trainer_available"]
            and state["input_shifting_gain"]
            and "model" not in state["branch_kinds"].values()
            and not state.get("model_research_in_memory")
        ):
            raise ValueError(
                "policy: a passing candidate changed model inputs; open a model branch (retraining check) before stopping"
            )
