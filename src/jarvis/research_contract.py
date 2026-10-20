"""Mechanism research intent and actions; independent of strategy parameters."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from jarvis.contracts import Budget, Record

Ref = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Name = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,79}$")]


class ResearchContract(Record):
    kind: Literal["MechanismResearchContract"] = "MechanismResearchContract"
    task_id: Name
    instruction: str = Field(min_length=1, max_length=12000)
    objective: str = Field(min_length=1, max_length=2000)
    focus: str = Field(min_length=1, max_length=2000)
    baseline_refs: tuple[Ref, ...] = Field(min_length=1)
    evaluator_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    exposure: Literal["development_exposed", "synthetic"]
    candidate_freedom: tuple[str, ...] = Field(min_length=1, max_length=16)
    acceptance: dict[str, float | bool | str]
    constraints: tuple[str, ...] = Field(min_length=1, max_length=16)
    origin: Literal["human_provided", "declared_fixture"]
    allow_observed_experiments: bool = False
    allow_skill_building: bool = False
    experiment_environment_id: Ref | None = None


class HypothesisSpec(Record):
    hypothesis_id: Name
    claim: str = Field(min_length=1, max_length=2000)
    prediction: str = Field(min_length=1, max_length=2000)
    falsification: str = Field(min_length=1, max_length=2000)
    evidence_refs: tuple[Ref, ...]


class QuestionSpec(Record):
    question_id: Name
    parent_id: Name | None = None
    question: str = Field(min_length=1, max_length=2000)
    hypotheses: tuple[HypothesisSpec, ...] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def distinct(self) -> "QuestionSpec":
        ids = [h.hypothesis_id for h in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("competing hypotheses need distinct identities")
        return self


class ActionBase(Record):
    question_id: Name
    hypothesis_ids: tuple[Name, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=2000)
    expected_observation: str = Field(min_length=1, max_length=2000)
    evidence_refs: tuple[Ref, ...]


class InspectAction(ActionBase):
    kind: Literal["INSPECT"] = "INSPECT"
    input_refs: tuple[Ref, ...] = Field(min_length=1, max_length=8)


class DiagnoseAction(ActionBase):
    kind: Literal["DIAGNOSE"] = "DIAGNOSE"
    skill_name: Name
    baseline_ref: Ref
    candidate_ref: Ref


class ExperimentAction(ActionBase):
    kind: Literal["EXPERIMENT"] = "EXPERIMENT"
    direction: str = Field(min_length=1, max_length=2000)


class ConsultAction(ActionBase):
    kind: Literal["CONSULT"] = "CONSULT"
    specialist: Literal["mechanism", "analyst", "literature"]
    question: str = Field(min_length=1, max_length=2000)


class BuildSkillAction(ActionBase):
    kind: Literal["BUILD_SKILL"] = "BUILD_SKILL"
    skill_name: Name
    version: str = Field(default="0.1.0", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    protocol: Literal["paired-attribution-v1"]
    requirement: str = Field(min_length=1, max_length=3000)


class StopAction(ActionBase):
    kind: Literal["STOP"] = "STOP"
    scope: Literal["question", "session"]
    conclusion: Literal["negative", "inconclusive", "research_complete", "needs_successor"]


Action = Annotated[
    InspectAction
    | DiagnoseAction
    | ExperimentAction
    | ConsultAction
    | BuildSkillAction
    | StopAction,
    Field(discriminator="kind"),
]


class ResearchDecision(Record):
    context_id: Ref
    question: QuestionSpec | None
    action: Action


class HypothesisAssessment(Record):
    hypothesis_id: Name
    verdict: Literal["supported", "refuted", "inconclusive"]
    evidence_refs: tuple[Ref, ...] = Field(min_length=1)
    explanation: str = Field(min_length=1, max_length=2000)


class ActionReview(Record):
    context_id: Ref
    summary: str = Field(min_length=1, max_length=3000)
    evidence_refs: tuple[Ref, ...] = Field(min_length=1)
    assessments: tuple[HypothesisAssessment, ...] = Field(max_length=6)
    limitations: tuple[str, ...] = Field(min_length=1, max_length=12)


class SkillManifest(Record):
    kind: Literal["ResearchSkillManifest"] = "ResearchSkillManifest"
    name: Name
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    protocol: Literal["inventory-v1", "paired-attribution-v1", "score-distribution-v1"]
    implementation: Literal["trusted_builtin", "sandboxed_python"]
    source_ref: Ref
    input_schema_ref: Ref
    output_schema_ref: Ref
    applicability: str = Field(min_length=1, max_length=2000)
    domain: str = Field(min_length=1)
    allowed_roles: tuple[Literal["director", "analyst"], ...] = ("director", "analyst")
    permissions: tuple[Literal["read_input_snapshot", "write_private_output"], ...] = (
        "read_input_snapshot",
        "write_private_output",
    )
    dependencies: tuple[Literal["python-stdlib", "jarvis-research"], ...] = ("python-stdlib",)
    budget: Budget = Budget(max_actions=1, max_tool_calls=1, max_wall_seconds=15)
    source_refs: tuple[Ref, ...] = Field(min_length=1)
    origin: Literal["human_provided", "agent_proposed", "declared_fixture"]


class SkillAgentVersion(Record):
    kind: Literal["SkillAgentVersion"] = "SkillAgentVersion"
    contract_ref: Ref
    registry_snapshot_ref: Ref
    skill_refs: tuple[Ref, ...]
    method_id: str = "mechanism-actions-v1"
    policy_id: str = "director-adaptive-v1"
    prompt_version: str = "mechanism-research-v1"
    provider_identity: dict[str, str]
    predecessor_ref: Ref | None = None
