"""Restricted, deterministic research policy; no expressions, imports or eval."""

from pathlib import Path
from typing import Literal

from jarvis.contracts import AdmissionError, PolicyDecision, Record, ResearchState
from jarvis.identity import digest, file_digest


class PolicySpec(Record):
    kind: Literal["ResearchExplorationPolicy"] = "ResearchExplorationPolicy"
    name: str = "Q0"
    # Preferences are action IDs already present in the admitted frontier.
    prefer: tuple[str, ...] = ()

    @property
    def identity(self) -> str:
        return digest({"spec": self.model_dump(), "compiler": file_digest(Path(__file__))})


class FixedPolicy:
    def __init__(self, spec: PolicySpec | None = None):
        self.spec = spec or PolicySpec()

    @property
    def identity(self) -> str:
        return self.spec.identity

    def choose(self, state: ResearchState) -> PolicyDecision:
        if not state.available_actions:
            raise AdmissionError("no admitted actions")
        available = {a.action_id: a for a in state.available_actions}
        choice = next((x for x in self.spec.prefer if x in available), None)
        if choice is None:
            choice = state.available_actions[0].action_id
        return PolicyDecision(
            state_hash=state.identity,
            action_id=choice,
            rationale="Q0 selects the first evidence-conditioned eligible action; explicit preferences precede that order",
            evidence_refs=state.visible_evidence_ids,
        )


def admit(decision: PolicyDecision, state: ResearchState) -> None:
    if decision.state_hash != state.identity:
        raise AdmissionError("STALE_DECISION")
    if not set(decision.evidence_refs) <= set(state.visible_evidence_ids):
        raise AdmissionError("EVIDENCE_NOT_VISIBLE")
    if decision.action_id not in {a.action_id for a in state.available_actions}:
        raise AdmissionError("UNKNOWN_ACTION")
    action = next(a for a in state.available_actions if a.action_id == decision.action_id)
    if not set(action.hypothesis.evidence_refs) <= set(state.visible_evidence_ids):
        raise AdmissionError("EVIDENCE_NOT_VISIBLE")
