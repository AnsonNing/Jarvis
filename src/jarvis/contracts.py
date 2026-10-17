"""Versioned, closed contracts shared by the CLI, kernel and adapters."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jarvis.identity import digest

Count = Annotated[int, Field(ge=0, strict=True)]
Amount = Annotated[float, Field(ge=0)]
ActionType = Literal["INSPECT", "DIAGNOSE", "RUN_EXPERIMENT", "REFINE", "STOP"]
EvaluationScope = Literal["development", "locked_synthetic", "dataset_replay"]
Verdict = Literal["PASS", "FAIL", "INCONCLUSIVE", "NOT_RUN", "INVALID_EVALUATION"]


class Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, validate_default=True
    )
    schema_version: Literal[1] = 1

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported integer schema version")
        return value


class Budget(Record):
    max_actions: Count = 20
    max_tool_calls: Count = 30
    max_llm_tokens: Count = 0
    max_wall_seconds: Amount = 300
    max_compute_units: Amount = 40
    max_parallel_jobs: Annotated[int, Field(ge=1, le=16, strict=True)] = 1

    def limits(self) -> dict[str, float]:
        return {
            "actions": self.max_actions,
            "tool_calls": self.max_tool_calls,
            "llm_tokens": self.max_llm_tokens,
            "wall_seconds": self.max_wall_seconds,
            "compute_units": self.max_compute_units,
        }


class Cost(Record):
    actions: Count = 0
    tool_calls: Count = 0
    llm_tokens: Count = 0
    wall_seconds: Amount = 0
    compute_units: Amount = 0

    def dimensions(self) -> dict[str, float]:
        return self.model_dump(exclude={"schema_version"})


class Acceptance(Record):
    minimum_primary_improvement: Amount = 0.01
    allow_no_improvement: bool = True
    target_primary: float | None = None


class Splits(Record):
    development_id: str = Field(min_length=1)
    selection_id: str = Field(min_length=1)
    locked_test_id: str | None = None

    @model_validator(mode="after")
    def distinct(self) -> "Splits":
        values = [v for v in (self.development_id, self.selection_id, self.locked_test_id) if v]
        if len(values) != len(set(values)):
            raise ValueError("split identities must be distinct")
        return self


class Permissions(Record):
    allow_generated_code: Literal[False] = False
    allow_external_search: Literal[False] = False
    allow_live_execution: Literal[False] = False


class ResearchTask(Record):
    kind: Literal["ResearchTask"] = "ResearchTask"
    task_id: str = Field(min_length=1)
    domain: Literal["synthetic", "hermes"]
    goal: str = Field(min_length=1)
    hypotheses_allowed: tuple[str, ...]
    primary_metric: str
    metric_direction: Literal["maximize", "minimize"] = "maximize"
    secondary_metrics: tuple[str, ...] = ()
    acceptance: Acceptance = Acceptance()
    split_policy: Splits
    budget: Budget = Budget()
    permissions: Permissions = Permissions()
    world_ref: str
    parent_task_id: str | None = None
    origin: Literal["human_provided", "declared_fixture"] = "human_provided"

    @property
    def identity(self) -> str:
        return digest(self)


class WorldSpec(Record):
    kind: Literal["WorldSpec"] = "WorldSpec"
    task_identity: str
    domain: str
    adapter_id: str
    adapter_source: str
    evaluator_id: str
    engine_source: str
    environment_identity: str
    dataset_identity: str
    split_identity: str
    budget: Budget
    scope: str
    origin: str

    @property
    def identity(self) -> str:
        return digest(self)


class Hypothesis(Record):
    hypothesis_id: str
    claim: str
    mechanism: str
    falsification: str
    evidence_refs: tuple[str, ...] = ()
    origin: Literal["declared_fixture", "human_provided", "agent_proposed"]


class ActionSpec(Record):
    kind: Literal["ActionSpec"] = "ActionSpec"
    action_id: str
    action_type: ActionType
    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    hypothesis: Hypothesis
    split_id: str
    estimated_cost: Cost = Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=10)
    origin: Literal["declared_fixture", "human_provided", "agent_proposed"] = "declared_fixture"

    @property
    def identity(self) -> str:
        return digest(self)


class Observation(Record):
    kind: Literal["ToolResult"] = "ToolResult"
    action_id: str
    status: Literal["SUCCEEDED", "FAILED", "NEEDS_INPUT"] = "SUCCEEDED"
    result_origin: Literal["SIMULATED", "OBSERVED", "REPLAYED"]
    metrics: dict[str, float] = Field(default_factory=dict)
    findings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    artifacts: dict[str, str] = Field(default_factory=dict)


class Evaluation(Record):
    kind: Literal["EvaluationResult"] = "EvaluationResult"
    evaluator_id: str
    action_id: str
    scope: EvaluationScope
    acceptance: Verdict
    primary_metric: str
    value: float | None = None
    baseline: float | None = None
    improvement: float | None = None
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...] = ()


class ResearchState(Record):
    kind: Literal["ResearchState"] = "ResearchState"
    campaign_id: str
    world_id: str
    sequence: int
    parent_node_id: str | None
    visible_evidence_ids: tuple[str, ...]
    observations: tuple[Observation, ...]
    evaluations: tuple[Evaluation, ...]
    remaining_budget: dict[str, float]
    available_actions: tuple[ActionSpec, ...]
    selected_policy_id: str

    @property
    def identity(self) -> str:
        return digest(self)


class PolicyDecision(Record):
    kind: Literal["PolicyDecision"] = "PolicyDecision"
    state_hash: str
    action_id: str
    rationale: str
    evidence_refs: tuple[str, ...] = ()
    origin: Literal["fixed_policy", "mock_provider", "provider"] = "fixed_policy"


class CampaignStatus(StrEnum):
    READY = "READY"
    SEARCHING = "SEARCHING"
    PAUSED = "PAUSED"
    INTERRUPTED = "INTERRUPTED"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
    EXHAUSTED = "EXHAUSTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class JarvisError(Exception):
    """Expected, user-readable kernel rejection."""


class IntegrityError(JarvisError):
    pass


class AdmissionError(JarvisError):
    pass


class BudgetExceeded(JarvisError):
    pass
