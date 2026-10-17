"""Deterministic non-financial research fixture, kept outside role contexts.

The outcomes are deliberately simple test data. Q0 sees observations only;
the fixture source is never serialized into a ResearchState or a role prompt.
"""

from pathlib import Path
from typing import Any

from jarvis.contracts import (
    ActionSpec,
    ActionType,
    AdmissionError,
    Cost,
    Hypothesis,
    Observation,
    ResearchState,
    ResearchTask,
)
from jarvis.identity import digest, file_digest

CATALOG: dict[str, tuple[ActionType, str, str]] = {
    "inspect": (
        "INSPECT",
        "Measure the declared baseline",
        "Baseline measurement fixes the comparator",
    ),
    "capacity": (
        "RUN_EXPERIMENT",
        "More capacity improves the objective",
        "Increase capacity alone",
    ),
    "diagnose": (
        "DIAGNOSE",
        "Conditioning limits the candidate",
        "A cheap diagnostic separates bottlenecks",
    ),
    "regularize": (
        "RUN_EXPERIMENT",
        "Conditioning correction improves quality",
        "Change conditioning alone",
    ),
    "refine": (
        "REFINE",
        "A bounded refinement preserves the gain",
        "Refine the observed promising candidate",
    ),
    "unknown": (
        "RUN_EXPERIMENT",
        "An untested method might help",
        "Coverage fixture; no result is assumed",
    ),
    "stop": (
        "STOP",
        "Further work lacks justified value",
        "Freeze the best observed development result",
    ),
}


def action(name: str, task: ResearchTask) -> ActionSpec:
    kind, claim, mechanism = CATALOG[name]
    return ActionSpec(
        action_id=name,
        action_type=kind,
        tool_id="synthetic." + name,
        split_id=task.split_policy.development_id,
        hypothesis=Hypothesis(
            hypothesis_id="fixture-" + name,
            claim=claim,
            mechanism=mechanism,
            falsification="The fixed evaluator records no declared improvement",
            origin="declared_fixture",
        ),
        estimated_cost=Cost()
        if name == "stop"
        else Cost(
            actions=1,
            tool_calls=1,
            compute_units=1,
            wall_seconds=10,
        ),
    )


class SyntheticAdapter:
    adapter_id = "synthetic:1"

    def __init__(self, world_ref: str = "synthetic-world-v1"):
        self.world_ref = world_ref

    def identity(self) -> dict[str, str]:
        return {
            "adapter_source": file_digest(Path(__file__)),
            "dataset_identity": digest({"fixture": self.world_ref, "version": 1}),
        }

    def validate(self, task: ResearchTask) -> None:
        if task.domain != "synthetic" or task.world_ref not in {
            "synthetic-world-v1",
            "synthetic-capacity-v1",
            "synthetic-negative-v1",
            "synthetic-sufficient-v1",
        }:
            raise AdmissionError("unknown synthetic fixture world")
        if task.primary_metric != "objective_score" or task.metric_direction != "maximize":
            raise AdmissionError("synthetic fixture supports maximizing objective_score only")
        if not task.split_policy.locked_test_id:
            raise AdmissionError("synthetic fixture requires an independent locked split identity")

    def frontier(self, task: ResearchTask, state: ResearchState) -> tuple[ActionSpec, ...]:
        seen = {o.action_id for o in state.observations}
        if not seen:
            names = ["inspect"]
        elif any(o.status != "SUCCEEDED" for o in state.observations):
            names = ["stop"]
        elif (
            task.acceptance.target_primary is not None
            and state.observations[0].metrics["objective_score"] >= task.acceptance.target_primary
        ):
            names = ["stop"]
        elif "capacity" not in seen:
            names = ["capacity", "unknown", "stop"]
        elif "refine" in seen or "regularize" in seen:
            names = ["refine", "unknown", "stop"] if "refine" not in seen else ["stop"]
        elif "diagnose" in seen:
            names = ["regularize", "unknown", "stop"]
        else:
            capacity = next(o for o in state.observations if o.action_id == "capacity")
            base = state.observations[0].metrics["objective_score"]
            names = (
                ["refine", "unknown", "stop"]
                if capacity.metrics["objective_score"] > base
                else ["diagnose", "unknown", "stop"]
            )
        return tuple(action(n, task) for n in names)

    def execute(self, request: ActionSpec) -> Observation:
        name = request.action_id
        if request.tool_id != "synthetic." + name or name not in CATALOG or name == "stop":
            raise AdmissionError("unknown synthetic tool")
        if request.arguments:
            raise AdmissionError("synthetic fixture accepts no tool arguments")
        if name == "diagnose":
            return Observation(
                action_id=name, result_origin="SIMULATED", findings=("conditioning_bottleneck",)
            )
        scores = {
            "inspect": 0.4,
            "capacity": 0.3,
            "regularize": 0.72,
            "refine": 0.80,
            "unknown": 0.45,
        }
        if self.world_ref == "synthetic-capacity-v1":
            scores["capacity"] = 0.74
        if self.world_ref == "synthetic-negative-v1":
            scores.update(capacity=0.25, regularize=0.3, refine=0.35)
        if self.world_ref == "synthetic-sufficient-v1":
            scores["inspect"] = 0.98
        return Observation(
            action_id=name,
            result_origin="SIMULATED",
            metrics={"objective_score": scores[name]},
            limitations=("Deterministic synthetic fixture; no financial interpretation",),
        )

    def formal(self, candidate: str) -> Observation:
        # Held-out fixture outcomes are opened only after a durable freeze event.
        scores = {"inspect": 0.39, "capacity": 0.70, "regularize": 0.68, "refine": 0.76}
        if self.world_ref == "synthetic-negative-v1":
            scores = {key: 0.30 for key in scores}
            scores["inspect"] = 0.39
        if self.world_ref == "synthetic-sufficient-v1":
            scores["inspect"] = 0.97
        return Observation(
            action_id=candidate,
            result_origin="SIMULATED",
            metrics={
                "objective_score": scores[candidate],
                "locked_baseline": 0.97 if self.world_ref == "synthetic-sufficient-v1" else 0.39,
            },
            limitations=("Synthetic locked cohort only; no transfer claim",),
        )

    def local_manifest(self) -> dict[str, Any]:
        return {"world_ref": self.world_ref}
