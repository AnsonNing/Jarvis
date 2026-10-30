"""Contracts for autonomous research sessions: owner intent, typed actions and artifacts.

The owner declares the baseline candidate, what may change (settings, training space,
implementation targets), readable sources, metrics and acceptance profiles. The Director
chooses actions; trusted executors run them. Domain specifics live in adapters.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from jarvis.contracts import Budget, Record
from jarvis.research_contract import HypothesisSpec, Name, QuestionSpec, Ref
from jarvis.runtime_controls import RuntimeControls
from jarvis.workbench import AnalysisEnvironment

ActionKind = Literal[
    "READ_SOURCE",
    "ANALYZE",
    "BACKTEST",
    "TRAIN_MODEL",
    "IMPLEMENT",
    "COMPARE",
    "CONSULT",
    "WAIT",
    "STOP",
]
BranchKind = Literal["diagnosis", "parameter", "model", "pipeline", "rule", "other"]
Scalar = float | int | bool | str


class SourceScope(Record):
    name: Name
    path: str = Field(min_length=1)
    sha256: Ref
    kind: Literal["strategy", "pipeline", "document", "data_dictionary", "other"]
    description: str = Field(min_length=1, max_length=1000)


class SettingSpec(Record):
    name: Name
    kind: Literal["float", "int", "bool", "choice"]
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    baseline: Scalar
    description: str = Field(min_length=1, max_length=600)
    affects_model_inputs: bool = False

    def check(self, value: Any) -> None:
        if self.kind == "bool":
            ok = isinstance(value, bool)
        elif self.kind == "choice":
            ok = isinstance(value, str) and value in self.choices
        else:
            ok = (
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and (self.kind == "float" or float(value).is_integer())
                and (self.minimum is None or value >= self.minimum)
                and (self.maximum is None or value <= self.maximum)
            )
        if not ok:
            raise ValueError(f"setting {self.name} outside declared space")


class ImplementTarget(Record):
    name: Name
    kind: Literal["strategy_patch", "pipeline_patch"]
    files: tuple[str, ...] = Field(min_length=1, max_length=16)
    description: str = Field(min_length=1, max_length=2000)
    interface: str = Field(min_length=1, max_length=4000)


class MetricSpec(Record):
    name: Name
    direction: Literal["maximize", "minimize"]
    description: str = Field(min_length=1, max_length=400)


class Objective(Record):
    metric: Name
    min_gain: float = 0
    relative: bool = False


class RiskLimit(Record):
    """Bounds a metric against the matched baseline (max_worsening) and/or absolutely (floor).

    A floor is the worst admissible candidate value: a minimum for metrics to maximize, a
    maximum for metrics to minimize.
    """

    metric: Name
    max_worsening: float | None = Field(default=None, ge=0)
    relative: bool = True
    floor: float | None = None

    @model_validator(mode="after")
    def bounded(self) -> "RiskLimit":
        if self.max_worsening is None and self.floor is None:
            raise ValueError("a risk limit needs max_worsening or floor")
        return self


class AcceptanceProfile(Record):
    """A candidate passes if any objective improves enough and no risk limit is breached."""

    name: Name
    objectives: tuple[Objective, ...] = Field(min_length=1, max_length=4)
    risk_limits: tuple[RiskLimit, ...] = Field(default=(), max_length=8)
    min_count_fraction: float = Field(default=0.5, ge=0, le=1)
    description: str = Field(min_length=1, max_length=600)


class Grants(Record):
    actions: tuple[ActionKind, ...] = (
        "READ_SOURCE",
        "ANALYZE",
        "BACKTEST",
        "TRAIN_MODEL",
        "IMPLEMENT",
        "COMPARE",
        "CONSULT",
        "WAIT",
        "STOP",
    )
    max_setting_changes: int = Field(default=3, ge=1, le=12)
    max_experiments_per_backtest: int = Field(default=6, ge=1, le=16)
    max_training_runs: int = Field(default=12, ge=0, le=200)
    max_implementations: int = Field(default=8, ge=0, le=100)
    max_analysis_steps: int = Field(default=12, ge=1, le=40)

    @model_validator(mode="after")
    def essentials(self) -> "Grants":
        if "STOP" not in self.actions or "WAIT" not in self.actions:
            raise ValueError("STOP and WAIT are always available")
        return self


class AnalysisDelivery(Record):
    """Opt-in v1 delivery gate; absent fields preserve historical session semantics."""

    kind: Literal["VerifiedAnalysisDelivery"] = "VerifiedAnalysisDelivery"
    require_tables: bool = True
    require_reproduction: bool = True
    review_evidence: bool = True
    capture_submitted_source: bool = Field(default=False, exclude_if=lambda value: value is False)


class ResearchStages(Record):
    """Explicit exposed-data stages. Confirmation is not an independent locked test."""

    kind: Literal["ResearchStages"] = "ResearchStages"
    screening: tuple[Name, ...] = Field(default=(), max_length=8)
    selection: Name
    confirmation: tuple[Name, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def distinct(self) -> "ResearchStages":
        periods = [*self.screening, self.selection, *self.confirmation]
        if len(periods) != len(set(periods)):
            raise ValueError("research stages must use distinct periods")
        return self


class ResearchPlanning(Record):
    """Opt-in collaborative planning; no branch quotas or automatic search expansion."""

    kind: Literal["CollaborativeResearchPlanning"] = "CollaborativeResearchPlanning"


class ResearchDirection(Record):
    """A proposed direction and qualitative priority, never an observed result."""

    direction_id: Name
    kind: BranchKind
    hypothesis: HypothesisSpec
    next_action: ActionKind
    effort: Literal["low", "medium", "high"]
    disposition: Literal["ready", "deferred", "blocked", "retired"]
    reason: str = Field(min_length=1, max_length=1200)
    origin: Literal["agent_proposed", "human_provided", "declared_fixture"]


class PlanningReview(Record):
    summary: str = Field(min_length=1, max_length=1600)
    directions: tuple[ResearchDirection, ...] = Field(max_length=8)
    selected_direction_id: Name | None = None
    selection_reason: str = Field(min_length=1, max_length=1600)
    stop_basis: Literal["goal_met", "no_justified_next_step", "owner_checkpoint"] | None = None
    owner_question: str | None = Field(default=None, min_length=1, max_length=1600)

    @model_validator(mode="after")
    def distinct(self) -> "PlanningReview":
        ids = [d.direction_id for d in self.directions]
        if len(ids) != len(set(ids)):
            raise ValueError("planning direction IDs must be distinct")
        return self


class AutonomyContract(Record):
    kind: Literal["AutonomousResearchContract"] = "AutonomousResearchContract"
    task_id: Name
    instruction: str = Field(min_length=1, max_length=12000)
    objective: str = Field(min_length=1, max_length=2000)
    focus: str = Field(min_length=1, max_length=2000)
    domain: str = Field(min_length=1)
    evaluator_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    periods: tuple[Name, ...] = Field(min_length=1, max_length=12)
    exposure: Literal["development_exposed", "synthetic"]
    baseline_candidate_ref: Ref
    settings: tuple[SettingSpec, ...] = Field(default=(), max_length=64)
    training_space: dict[str, Any] | None = None
    implement_targets: tuple[ImplementTarget, ...] = Field(default=(), max_length=8)
    source_scopes: tuple[SourceScope, ...] = Field(default=(), max_length=32)
    metrics: tuple[MetricSpec, ...] = Field(min_length=1, max_length=24)
    acceptance_profiles: tuple[AcceptanceProfile, ...] = Field(min_length=1, max_length=6)
    analysis_environment: AnalysisEnvironment = AnalysisEnvironment()
    grants: Grants = Grants()
    constraints: tuple[str, ...] = Field(min_length=1, max_length=24)
    origin: Literal["human_provided", "declared_fixture"]
    analysis_delivery: AnalysisDelivery | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    stages: ResearchStages | None = Field(default=None, exclude_if=lambda value: value is None)
    planning: ResearchPlanning | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def references(self) -> "AutonomyContract":
        metrics = {m.name for m in self.metrics}
        for profile in self.acceptance_profiles:
            used = {o.metric for o in profile.objectives} | {r.metric for r in profile.risk_limits}
            if not used <= metrics:
                raise ValueError("acceptance profile uses an undeclared metric")
        for group in (self.settings, self.implement_targets, self.source_scopes):
            names = [x.name for x in group]
            if len(names) != len(set(names)):
                raise ValueError("duplicate declared name")
        if len({p.name for p in self.acceptance_profiles}) != len(self.acceptance_profiles):
            raise ValueError("duplicate acceptance profile")
        if self.stages and set(self.periods) != {
            *self.stages.screening,
            self.stages.selection,
            *self.stages.confirmation,
        }:
            raise ValueError("every declared period must have exactly one research stage")
        return self


class BranchSpec(Record):
    branch_id: Name
    kind: BranchKind
    goal: str = Field(min_length=1, max_length=1200)


class ActionBase(Record):
    branch_id: Name
    question_id: Name
    hypothesis_ids: tuple[Name, ...] = Field(min_length=1, max_length=6)
    rationale: str = Field(min_length=1, max_length=2000)
    expected_observation: str = Field(min_length=1, max_length=2000)
    evidence_refs: tuple[Ref, ...] = Field(max_length=24)


class ReadSourceAction(ActionBase):
    kind: Literal["READ_SOURCE"] = "READ_SOURCE"
    scopes: tuple[Name, ...] = Field(min_length=1, max_length=6)


class AnalyzeAction(ActionBase):
    kind: Literal["ANALYZE"] = "ANALYZE"
    question: str = Field(min_length=1, max_length=2000)
    input_refs: tuple[Ref, ...] = Field(min_length=1, max_length=12)


class SettingValue(Record):
    name: Name
    value: float | bool | str


def setting_dict(values: tuple[SettingValue, ...]) -> dict[str, Scalar]:
    names = [v.name for v in values]
    if len(names) != len(set(names)):
        raise ValueError("duplicate setting")
    return {v.name: v.value for v in values}


class ExperimentSpec(Record):
    label: Name
    candidate_ref: Ref
    settings: tuple[SettingValue, ...] = Field(default=(), max_length=12)
    period: Name
    hypothesis: str = Field(min_length=1, max_length=1200)
    falsification: str = Field(min_length=1, max_length=1200)


class BacktestAction(ActionBase):
    kind: Literal["BACKTEST"] = "BACKTEST"
    experiments: tuple[ExperimentSpec, ...] = Field(min_length=1, max_length=16)
    design: str = Field(min_length=1, max_length=1600)


class TrainModelAction(ActionBase):
    kind: Literal["TRAIN_MODEL"] = "TRAIN_MODEL"
    label: Name
    base_candidate_ref: Ref
    training_data_ref: Ref | None = None
    pipeline_patch_ref: Ref | None = None
    config: tuple[SettingValue, ...] = Field(default=(), max_length=24)
    # Declared strategy settings the trained candidate runs with (e.g. the collection's rule
    # switches); the trainer sees the base under them and the new candidate keeps them.
    settings: tuple[SettingValue, ...] = Field(default=(), max_length=12)


class ImplementAction(ActionBase):
    kind: Literal["IMPLEMENT"] = "IMPLEMENT"
    label: Name
    target: Name
    base_candidate_ref: Ref
    requirement: str = Field(min_length=1, max_length=4000)


class CompareAction(ActionBase):
    kind: Literal["COMPARE"] = "COMPARE"
    result_refs: tuple[Ref, ...] = Field(min_length=1, max_length=32)
    baseline_result_ref: Ref


class ConsultAction(ActionBase):
    kind: Literal["CONSULT"] = "CONSULT"
    specialist: Literal["quant", "ml", "execution", "statistics"]
    question: str = Field(min_length=1, max_length=2000)


class WaitAction(ActionBase):
    kind: Literal["WAIT"] = "WAIT"


class BranchClosure(Record):
    branch_id: Name
    conclusion: Literal["supported", "negative", "inconclusive", "blocked", "superseded"]
    reason: str = Field(min_length=1, max_length=1600)
    evidence_refs: tuple[Ref, ...] = Field(min_length=1, max_length=12)


class StopAction(ActionBase):
    kind: Literal["STOP"] = "STOP"
    scope: Literal["branch", "session", "checkpoint"]
    conclusion: Literal["negative", "inconclusive", "research_complete", "blocked"]
    closures: tuple[BranchClosure, ...] = Field(max_length=24)
    finalist_result_ref: Ref | None = None

    @model_validator(mode="after")
    def closure_scope(self) -> "StopAction":
        if self.scope != "checkpoint" and not self.closures:
            raise ValueError("branch/session stops require branch closures")
        if self.scope == "checkpoint" and (
            self.closures
            or self.finalist_result_ref is not None
            or self.conclusion not in {"inconclusive", "blocked"}
        ):
            raise ValueError("owner checkpoints leave branches open and select no finalist")
        return self


AutonomyAction = Annotated[
    ReadSourceAction
    | AnalyzeAction
    | BacktestAction
    | TrainModelAction
    | ImplementAction
    | CompareAction
    | ConsultAction
    | WaitAction
    | StopAction,
    Field(discriminator="kind"),
]


class GuidanceResponse(Record):
    guidance_id: Name
    adopted: bool
    response: str = Field(min_length=1, max_length=1600)


class AutonomyDecision(Record):
    context_id: Ref
    situation: str = Field(min_length=1, max_length=3000)
    branch: BranchSpec | None
    question: QuestionSpec | None
    guidance_responses: tuple[GuidanceResponse, ...] = Field(max_length=12)
    action: AutonomyAction
    planning_review: PlanningReview | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class HypothesisVerdict(Record):
    hypothesis_id: Name
    verdict: Literal["supported", "refuted", "inconclusive"]
    evidence_refs: tuple[Ref, ...] = Field(min_length=1, max_length=12)
    explanation: str = Field(min_length=1, max_length=2000)


class AutonomyReview(Record):
    context_id: Ref
    summary: str = Field(min_length=1, max_length=3000)
    evidence_refs: tuple[Ref, ...] = Field(min_length=1, max_length=24)
    assessments: tuple[HypothesisVerdict, ...] = Field(max_length=8)
    concerns: tuple[str, ...] = Field(min_length=1, max_length=12)
    recommendation: Literal["continue", "close_branch", "investigate_further", "invalid_result"]
    next_questions: tuple[str, ...] = Field(max_length=6)


class Candidate(Record):
    """A complete runnable strategy configuration; baseline is a candidate too."""

    kind: Literal["ResearchCandidate"] = "ResearchCandidate"
    label: Name
    parent_ref: Ref | None
    model_ref: Ref | None
    patch_refs: tuple[Ref, ...] = Field(default=(), max_length=8)
    settings: dict[Name, Scalar] = Field(default_factory=dict)
    origin: Literal["owner_baseline", "agent_trained", "agent_implemented"]
    data_policy: str = Field(min_length=1, max_length=600)
    produced_by: dict[str, Any] = Field(default_factory=dict)


class ModelArtifact(Record):
    kind: Literal["ModelArtifact"] = "ModelArtifact"
    model_id: str = Field(min_length=1)
    files: dict[str, Ref]
    training_config: dict[str, Scalar]
    training_data_ref: Ref | None
    data_policy: str = Field(min_length=1, max_length=600)
    pipeline_patch_ref: Ref | None = None
    validation: dict[str, Any]
    origin: Literal["owner_provided", "agent_trained"]
    trainer: dict[str, Any]


class CodePatch(Record):
    kind: Literal["CodePatch"] = "CodePatch"
    target: Name
    target_kind: Literal["strategy_patch", "pipeline_patch"]
    files: dict[str, Ref]
    diff_ref: Ref
    validation: dict[str, Any]
    origin: Literal["agent_implemented"] = "agent_implemented"


class ObservedTable(Record):
    kind: Literal["ObservedTable"] = "ObservedTable"
    name: Name
    blob_ref: Ref
    format: Literal["csv", "json"]
    columns: tuple[str, ...] = Field(max_length=512)
    rows: int = Field(ge=0)
    description: str = Field(min_length=1, max_length=1000)
    lineage: dict[str, Any]
    exposure: Literal["development_exposed", "synthetic"]


class GuidanceEntry(Record):
    kind: Literal["OwnerGuidance"] = "OwnerGuidance"
    guidance_id: Name
    mode: Literal["advise", "redirect"]
    text: str = Field(min_length=1, max_length=4000)
    origin: Literal["human_provided", "declared_fixture"] = "human_provided"


class AutonomySessionSpec(Record):
    kind: Literal["AutonomousResearchSpec"] = "AutonomousResearchSpec"
    contract_ref: Ref
    input_refs: tuple[Ref, ...] = Field(default=(), max_length=32)
    prior_evidence_refs: tuple[Ref, ...] = Field(default=(), max_length=64)
    policy_ref: Ref | None = None
    budget: Budget = Budget(
        max_actions=400,
        max_tool_calls=400,
        max_llm_tokens=8000000,
        max_wall_seconds=172800,
        max_compute_units=2000,
        max_parallel_jobs=12,
    )
    max_decisions: int = Field(default=200, ge=1, le=2000)
    max_concurrent_actions: int = Field(default=2, ge=1, le=8)
    role_config: dict[str, Any] = Field(default_factory=dict)
    # Pinned cross-session memory (lessons, prior experiments/candidates/tables, skills).
    memory_ref: Ref | None = Field(default=None, exclude_if=lambda value: value is None)
    runtime: RuntimeControls | None = Field(default=None, exclude_if=lambda value: value is None)
