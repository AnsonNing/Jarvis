"""Closed exploration DSL: observation-only predicates, never code execution."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from jarvis.contracts import PolicyDecision, Record, ResearchState
from jarvis.identity import digest, file_digest

ActionName = Literal["inspect", "expand", "diagnose", "correct", "stop"]
Predicate = Literal[
    "always", "baseline_sufficient", "diagnosed", "correction_supported", "failed", "improved"
]


class Rule(Record):
    when: Predicate
    prefer: tuple[ActionName, ...] = Field(min_length=1, max_length=5)


class ExplorationPolicy(Record):
    kind: Literal["BranchExplorationPolicy"] = "BranchExplorationPolicy"
    name: str = Field(min_length=1, max_length=80)
    rules: tuple[Rule, ...] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1, max_length=1600)
    origin: Literal["declared_fixture", "agent_proposed", "human_provided"] = "declared_fixture"

    @model_validator(mode="after")
    def fallback(self) -> "ExplorationPolicy":
        if self.rules[-1].when != "always":
            raise ValueError("last rule must be an unconditional fallback")
        if any(len(set(r.prefer)) != len(r.prefer) for r in self.rules):
            raise ValueError("duplicate action preference")
        return self

    @property
    def identity(self) -> str:
        return digest(
            {
                "policy": self.model_dump(),
                "compiler_features": file_digest(Path(__file__)),
                "frontier": file_digest(Path(__file__).with_name("suite.py")),
                "budget_semantics": "paid-edge-vector:1",
                "prompt_version": "policy-developer:1",
            }
        )


def features(state: ResearchState, target: float) -> dict[str, bool]:
    values = [
        o.metrics["objective_score"] for o in state.observations if "objective_score" in o.metrics
    ]
    return {
        "always": True,
        "baseline_sufficient": bool(values and values[0] >= target),
        "diagnosed": any(o.action_id == "diagnose" for o in state.observations),
        "correction_supported": any(
            "correction_supported" in o.findings for o in state.observations
        ),
        "failed": any(o.status != "SUCCEEDED" for o in state.observations),
        "improved": bool(values and max(values) > values[0]),
    }


def choose(policy: ExplorationPolicy, state: ResearchState, target: float) -> PolicyDecision:
    observed = features(state, target)
    eligible = {a.action_id for a in state.available_actions}
    for rule in policy.rules:
        if observed[rule.when]:
            for name in rule.prefer:
                if name in eligible:
                    return PolicyDecision(
                        state_hash=state.identity,
                        action_id=name,
                        rationale=policy.rationale,
                        evidence_refs=state.visible_evidence_ids,
                    )
    return PolicyDecision(
        state_hash=state.identity,
        action_id=state.available_actions[0].action_id,
        rationale="Trusted frontier fallback",
        evidence_refs=state.visible_evidence_ids,
    )


def incumbent() -> ExplorationPolicy:
    return ExplorationPolicy(
        name="Q0-branch-v1",
        rules=(Rule(when="always", prefer=("inspect", "expand", "diagnose", "correct", "stop")),),
        rationale="Measure baseline, try broad expansion, diagnose, then correct.",
    )


def fixture_proposals() -> tuple[ExplorationPolicy, ...]:
    return (
        ExplorationPolicy(
            name="diagnose-first",
            rules=(
                Rule(when="always", prefer=("inspect", "diagnose", "correct", "expand", "stop")),
            ),
            rationale="A cheap diagnostic can avoid a wasted broad experiment.",
        ),
        ExplorationPolicy(
            name="stop-after-measurement",
            rules=(
                Rule(when="always", prefer=("inspect", "stop", "diagnose", "correct", "expand")),
            ),
            rationale="Negative control: low cost alone is insufficient without target quality.",
        ),
        ExplorationPolicy(
            name="diagnose-expand",
            rules=(
                Rule(when="always", prefer=("inspect", "diagnose", "expand", "correct", "stop")),
            ),
            rationale="Competing order: diagnose before a broad experiment.",
        ),
    )
