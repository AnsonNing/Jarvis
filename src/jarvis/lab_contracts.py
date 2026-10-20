"""Versioned research-method records; no executable agent-defined code."""

import math
from typing import Literal

from pydantic import Field, model_validator

from jarvis.contracts import Budget, Record
from jarvis.research_program import ResearchProgramSpec
from jarvis.researcher import AgentConfig

Expert = Literal["mechanism", "analyst", "literature"]
LabAction = Literal["retrieve", "search", "consult", "discuss", "diagnose"]


class ResearchMethod(Record):
    kind: Literal["ResearchMethod"] = "ResearchMethod"
    name: str = Field(min_length=1, max_length=80)
    director_guidance: str = Field(
        default="Use falsifiable single-factor controls.", max_length=1800
    )
    critic_guidance: str = Field(default="Check controls and counter-evidence.", max_length=1800)
    specialist_guidance: str = Field(
        default="Propose distinct mechanisms and a discriminating test.", max_length=1800
    )
    specialist_roles: tuple[Expert, ...] = Field(default=("mechanism", "analyst"), max_length=3)
    evidence_first: bool = False
    origin: Literal["human_provided", "agent_proposed", "declared_fixture"] = "human_provided"

    @model_validator(mode="after")
    def unique(self) -> "ResearchMethod":
        if len(set(self.specialist_roles)) != len(self.specialist_roles):
            raise ValueError("duplicate specialist")
        return self


class LabPolicy(Record):
    kind: Literal["LabPolicy"] = "LabPolicy"
    name: str = Field(default="Lab-Q0", min_length=1, max_length=80)
    actions: tuple[LabAction, ...] = ("retrieve", "search", "consult", "discuss", "diagnose")
    origin: Literal["human_provided", "agent_proposed", "declared_fixture"] = "human_provided"

    @model_validator(mode="after")
    def closed_order(self) -> "LabPolicy":
        order = ["retrieve", "search", "consult", "discuss", "diagnose"]
        if len(set(self.actions)) != len(self.actions) or list(self.actions) != sorted(
            self.actions, key=order.index
        ):
            raise ValueError("action dependencies/order must remain fixed")
        if "discuss" in self.actions and "consult" not in self.actions:
            raise ValueError("discussion requires initial independent consultation")
        return self


class SourceDocument(Record):
    kind: Literal["SourceDocument"] = "SourceDocument"
    uri: str = Field(min_length=1, max_length=1500)
    title: str = Field(min_length=1, max_length=1000)
    text: str = Field(min_length=1, max_length=16000)
    version: str = Field(min_length=1, max_length=120)
    origin: Literal["external", "human_provided", "declared_fixture"]
    read_scope: Literal["metadata", "abstract", "full_text"]
    license_note: str = Field(max_length=500)


class PublicSourceQuery(Record):
    backend: Literal["crossref", "github"]
    question: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1000)


class PublicSourcePlan(Record):
    context_id: str
    queries: tuple[PublicSourceQuery, ...] = Field(min_length=1, max_length=3)
    limitations: str = Field(min_length=1, max_length=1500)


class HumanInput(Record):
    kind: Literal["HumanResearchInput"] = "HumanResearchInput"
    category: Literal["idea", "background", "priority"]
    text: str = Field(min_length=1, max_length=3000)
    origin: Literal["human_provided"] = "human_provided"


class ExpertOpinion(Record):
    kind: Literal["ExpertOpinion"] = "ExpertOpinion"
    context_id: str
    hypothesis: str = Field(min_length=1, max_length=2500)
    counterargument: str = Field(min_length=1, max_length=2500)
    test: str = Field(min_length=1, max_length=2500)
    evidence_refs: tuple[str, ...] = Field(max_length=32)
    question_for: Expert | None
    question: str | None


class TeamPlan(Record):
    kind: Literal["ResearchTeamPlan"] = "ResearchTeamPlan"
    context_id: str
    roles: tuple[Expert, ...] = Field(max_length=3)
    reason: str = Field(min_length=1, max_length=1500)


class ExperienceProposal(Record):
    kind: Literal["ExperienceProposal"] = "ExperienceProposal"
    context_id: str
    claim: str = Field(min_length=1, max_length=2500)
    limitations: str = Field(min_length=1, max_length=2500)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    counter_evidence_refs: tuple[str, ...] = Field(max_length=32)


class CapabilityDefinition(Record):
    kind: Literal["ResearchCapability"] = "ResearchCapability"
    name: str = Field(min_length=1, max_length=80)
    operation: Literal["group_mean_argmax"]
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1, max_length=1500)
    origin: Literal["agent_proposed", "human_provided", "declared_fixture"]


class CapabilityProposal(Record):
    context_id: str
    capability: CapabilityDefinition


class CapabilityChoice(Record):
    context_id: str
    capability_ref: str | None
    reason: str = Field(min_length=1, max_length=1500)


class LabSpec(Record):
    kind: Literal["ResearchLabSpec"] = "ResearchLabSpec"
    task_id: str = Field(min_length=1, max_length=100)
    family: str = Field(min_length=1, max_length=100)
    program: ResearchProgramSpec
    method: ResearchMethod = ResearchMethod(name="Research-M0")
    policy: LabPolicy = LabPolicy()
    role_config: AgentConfig = AgentConfig(max_calls=32)
    budget: Budget
    source_refs: tuple[str, ...] = Field(default=(), max_length=16)
    human_refs: tuple[str, ...] = Field(default=(), max_length=16)
    experience_refs: tuple[str, ...] = Field(default=(), max_length=32)
    board_id: str | None = None
    board_snapshot_ref: str | None = None
    adoption_study: str | None = None
    capability_ref: str | None = None
    diagnostic_groups: dict[str, tuple[float, ...]] = Field(default_factory=dict)
    public_search_question: str | None = Field(default=None, max_length=500)
    source_backend: Literal["crossref", "github"] = "crossref"
    allow_external_search: bool = False
    allow_observed_backend: bool = False

    @model_validator(mode="after")
    def controls(self) -> "LabSpec":
        if not self.program.critic or not self.program.execution_budget:
            raise ValueError("lab requires independent Critic and complete program budget")
        if self.family != self.program.queue.environment.period.group_id:
            raise ValueError("family must match the pinned task group")
        if (
            not self.allow_observed_backend
            and self.program.queue.environment.result_origin != "SIMULATED_FIXTURE"
        ):
            raise ValueError("observed research requires separate explicit declaration")
        if self.allow_external_search and not self.public_search_question:
            raise ValueError("external search needs an owner-declared public question")
        if self.program.support_ref is not None:
            raise ValueError("lab creates its own causal support package")
        if any(
            not values or any(not math.isfinite(v) for v in values)
            for values in self.diagnostic_groups.values()
        ):
            raise ValueError("diagnostic groups require finite nonempty values")
        return self


class ResearchSupport(Record):
    kind: Literal["ResearchSupport"] = "ResearchSupport"
    owner: str
    method: ResearchMethod
    evidence_refs: tuple[str, ...]
    observations: tuple[dict, ...]


class MethodCandidates(Record):
    context_id: str
    methods: tuple[ResearchMethod, ...] = Field(min_length=1, max_length=3)


class PolicyCandidates(Record):
    context_id: str
    policies: tuple[LabPolicy, ...] = Field(min_length=1, max_length=3)
