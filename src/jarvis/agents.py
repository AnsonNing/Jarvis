"""Provider-neutral structured role boundary. No implicit API or credential use."""

from typing import Literal, Protocol

from jarvis.contracts import Cost, Evaluation, Observation, PolicyDecision, Record, ResearchState
from jarvis.policy import FixedPolicy

Role = Literal["director", "scientist", "designer", "critic", "curator"]


class RoleRequest(Record):
    request_id: str
    role: Role
    state: ResearchState


class Completion(Record):
    request_id: str
    decision: PolicyDecision
    usage: Cost
    provider: str


class StructuredProvider(Protocol):
    def complete(self, request: RoleRequest) -> Completion: ...


class MockProvider:
    """CI/test provider. Reports zero LLM tokens because no LLM call occurs."""

    def complete(self, request: RoleRequest) -> Completion:
        decision = (
            FixedPolicy().choose(request.state).model_copy(update={"origin": "mock_provider"})
        )
        return Completion(
            request_id=request.request_id, decision=decision, usage=Cost(), provider="mock:1"
        )


def role_records(state: ResearchState, decision: PolicyDecision) -> list[dict]:
    """Explicit deterministic role receipts, not fabricated LLM conversations."""
    action = next(a for a in state.available_actions if a.action_id == decision.action_id)
    return [
        {
            "role": "director",
            "origin": "fixed_policy",
            "action_id": decision.action_id,
            "evidence_refs": decision.evidence_refs,
        },
        {
            "role": "scientist",
            "origin": action.hypothesis.origin,
            "hypothesis": action.hypothesis.model_dump(),
        },
        {"role": "designer", "origin": action.origin, "action_ref": action.identity},
    ]


class Critique(Record):
    role: Literal["critic"] = "critic"
    origin: Literal["deterministic_rule"] = "deterministic_rule"
    classification: Literal[
        "EXECUTION_ERROR", "RESEARCH_NEGATIVE", "DEVELOPMENT_GAIN", "DIAGNOSTIC_ONLY"
    ]
    next_direction: Literal[
        "RECONCILE", "BRANCH_OR_DIAGNOSE", "BOUNDED_REFINEMENT", "CONTINUE_DIAGNOSIS"
    ]
    evidence_refs: tuple[str, ...]
    uncertainties: tuple[str, ...]


class Finding(Record):
    role: Literal["curator"] = "curator"
    origin: Literal["deterministic_rule"] = "deterministic_rule"
    action_id: str
    scope: str
    status: Literal["TESTED_NEGATIVE", "SUPPORTED_IN_SCOPE", "INCONCLUSIVE"]
    supporting_evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    transferable: Literal[False] = False


def critique(evaluation: Evaluation, observation: Observation, evaluation_ref: str) -> Critique:
    classification: Literal[
        "EXECUTION_ERROR", "RESEARCH_NEGATIVE", "DEVELOPMENT_GAIN", "DIAGNOSTIC_ONLY"
    ] = "DIAGNOSTIC_ONLY"
    direction: Literal[
        "RECONCILE", "BRANCH_OR_DIAGNOSE", "BOUNDED_REFINEMENT", "CONTINUE_DIAGNOSIS"
    ] = "CONTINUE_DIAGNOSIS"
    if observation.status != "SUCCEEDED":
        classification, direction = "EXECUTION_ERROR", "RECONCILE"
    elif evaluation.acceptance == "FAIL":
        classification, direction = "RESEARCH_NEGATIVE", "BRANCH_OR_DIAGNOSE"
    elif evaluation.acceptance == "PASS":
        classification, direction = "DEVELOPMENT_GAIN", "BOUNDED_REFINEMENT"
    return Critique(
        classification=classification,
        next_direction=direction,
        evidence_refs=(evaluation_ref,),
        uncertainties=evaluation.limitations,
    )


def curate(evaluation: Evaluation, evaluation_ref: str) -> Finding:
    status: Literal["TESTED_NEGATIVE", "SUPPORTED_IN_SCOPE", "INCONCLUSIVE"] = "INCONCLUSIVE"
    if evaluation.acceptance == "FAIL":
        status = "TESTED_NEGATIVE"
    elif evaluation.acceptance == "PASS":
        status = "SUPPORTED_IN_SCOPE"
    return Finding(
        action_id=evaluation.action_id,
        scope=evaluation.scope,
        status=status,
        supporting_evidence=(evaluation_ref,),
        limitations=evaluation.limitations,
    )
