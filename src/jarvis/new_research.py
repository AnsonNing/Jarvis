"""Versioned research turns for new-baseline prefill experiments."""

from typing import Annotated, Literal

from pydantic import Field

from jarvis.contracts import AdmissionError, Hypothesis, Record
from jarvis.researcher import (
    INSTRUCTIONS,
    ExperimentDesign,
    ExperimentParameters,
    validate_parameters,
)
from jarvis.search_space import CancelRule


class ExperimentParametersV2(ExperimentParameters):
    cancel_risk_delta: Annotated[float, Field(ge=-0.2, le=0.2)] | None = None
    min_order_age_ms: Annotated[float, Field(ge=0, le=30000)] | None = None
    cancel_rule: CancelRule | None = None


class ExperimentDesignV2(ExperimentDesign):
    parameters: ExperimentParametersV2


class ResearchTurnV2(Record):
    kind: Literal["ResearchTurnV2"] = "ResearchTurnV2"
    request_id: str
    state_hash: str
    analysis_summary: str = Field(min_length=1, max_length=5000)
    evidence_refs: tuple[str, ...] = Field(max_length=64)
    hypothesis: Hypothesis
    experiment: ExperimentDesignV2
    next_direction: Literal["CONTINUE", "BRANCH", "STOP", "NEEDS_INPUT"]
    limitations: tuple[str, ...] = Field(max_length=12)


def validate_new_parameters(action_id: str, arguments: dict) -> None:
    args = ExperimentParametersV2.model_validate(arguments).arguments()
    if action_id not in {"cancel_experiment", "joint_experiment"}:
        validate_parameters(action_id, args)
        return
    allowed = {"cancel_risk_delta", "min_order_age_ms", "cancel_rule"}
    if action_id == "joint_experiment":
        allowed |= {"trading_cutoff_delta", "cancel_mode"}
    if not set(args) <= allowed:
        raise AdmissionError("unsupported controlled intervention")
    changes = (
        int(args.get("trading_cutoff_delta", 0) != 0)
        + int(args.get("cancel_risk_delta", 0) != 0)
        + int(args.get("min_order_age_ms", 0) != 0)
        + int(args.get("cancel_mode") == "disabled")
        + int("cancel_rule" in args)
    )
    if (action_id == "cancel_experiment" and changes != 1) or (
        action_id == "joint_experiment" and not 2 <= changes <= 3
    ):
        raise AdmissionError(
            "declare exactly one cancel change or two/three explicit joint changes"
        )
    if args.get("cancel_mode") == "disabled" and set(args) & {
        "cancel_rule",
        "cancel_risk_delta",
        "min_order_age_ms",
    }:
        raise AdmissionError("disabled cancellation cannot also specify a trigger")
    if "cancel_rule" in args:
        if set(args) & {"cancel_risk_delta", "min_order_age_ms"}:
            raise AdmissionError("custom rule must declare its complete trigger")
        rule = CancelRule.model_validate(args["cancel_rule"])
        if any(c.field not in {"cancel_score", "order_age_ms"} for c in rule.conditions):
            raise AdmissionError(
                "REQUIRES_CAPABILITY: current snapshots contain score and age only"
            )


INSTRUCTIONS_V2 = (
    INSTRUCTIONS.split("\nHermes tools (only if offered):", 1)[0]
    + """
The required answer kind is ResearchTurnV2. Use hermes_new tools only when admitted.
Inspection and the reproduced frozen baseline are mandatory. If prior_round_review is offered,
read it before new experiments. Its prior development findings are advisory, not independent tests.
threshold_experiment changes trading_cutoff_delta in [-0.002,0.002] OR cancel_mode='disabled'.
cancel_experiment changes exactly ONE of: cancel_risk_delta [-0.2,0.2] in model-risk units;
min_order_age_ms (0,30000], requiring baseline risk crossing AND that minimum prefill age;
or cancel_rule with bounded all/any score/age conditions and gt/ge/lt/le comparisons.
Use small evidence-grounded changes appropriate to the observed risk scale, not percentile refits.
joint_experiment permits two or three explicitly controlled changes after single-factor evidence.
Do not combine disabled cancellation with a trigger, or custom rules with implicit age/risk changes.
Rules can read only cancel_score and order_age_ms. No spread, adverse move or postfill price path is
available. A matching prefill snapshot produces at most one cancellation per episode. This is a
conditional filled-population replay assuming immediate successful cancellation, not order ack.
conditional_analysis groups the frozen baseline by day/product. cost_stress uses additional_cost
in source PL units. All remaining tools take no parameters; unused fields must be null.
Calibration dates are already exposed development. September is excluded from adaptive selection.
No refitting models/cutoffs, outside source access, external backtest submissions or deployment.
Source entry PL/PR and fee/exit semantics are not independently reconstructed. Do not clip saved
PL to invent stop/target outcomes. backend_requirements reports actual missing data/capabilities.
STOP selects template_id='stop' with evidence-grounded analysis, negative results and next steps.
"""
)
