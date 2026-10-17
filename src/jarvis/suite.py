"""Independent deterministic mathematical fixtures; truth stays behind execution.

Families have different generators and objective calculations, not renamed seeds.
All outcomes are explicitly SIMULATED. No market or trading model is represented.
"""

import math
from pathlib import Path
from typing import Literal

from pydantic import Field

from jarvis.contracts import (
    Acceptance,
    ActionSpec,
    Budget,
    Cost,
    Hypothesis,
    Observation,
    Record,
    ResearchState,
    ResearchTask,
    Splits,
)
from jarvis.identity import digest, file_digest

Family = Literal[
    "linear_bias",
    "frequency_alias",
    "shortest_path",
    "allocation",
    "robust_location",
    "sufficient",
    "root_finding",
    "matrix_projection",
    "negative",
    "spurious_proxy",
    "missing",
    "crash",
]
DEV: tuple[Family, ...] = ("linear_bias", "frequency_alias", "shortest_path")
HELDOUT: tuple[Family, ...] = ("allocation", "robust_location", "sufficient")
FRESH: tuple[Family, ...] = ("root_finding", "matrix_projection", "negative")


class Fixture(Record):
    family: Family
    variant: int = Field(ge=0, le=9, strict=True)

    @property
    def identity(self) -> str:
        return digest(self)


def task_for(fixture: Fixture, *, collection: bool = False) -> ResearchTask:
    return ResearchTask(
        task_id="suite-" + fixture.identity[7:23],
        domain="synthetic",
        goal="Reach fixed normalized objective quality under a matched research budget",
        hypotheses_allowed=("diagnosis", "parameter_change"),
        primary_metric="objective_score",
        acceptance=Acceptance(target_primary=0.9),
        split_policy=Splits(
            development_id="dev:" + fixture.identity,
            selection_id="select:" + fixture.identity,
            locked_test_id="locked:" + fixture.identity,
        ),
        budget=Budget(
            max_actions=1000 if collection else 3,
            max_tool_calls=1000 if collection else 3,
            max_compute_units=1000 if collection else 3,
            max_wall_seconds=300,
        ),
        world_ref=fixture.identity,
        origin="declared_fixture",
    )


def frontier(task: ResearchTask, state: ResearchState) -> tuple[ActionSpec, ...]:
    seen = {o.action_id for o in state.observations}
    values = [
        o.metrics["objective_score"] for o in state.observations if "objective_score" in o.metrics
    ]
    if not seen:
        names = ["inspect"]
    elif any(o.status != "SUCCEEDED" for o in state.observations) or (
        values and max(values) >= 0.9
    ):
        names = ["stop"]
    else:
        names = [n for n in ("expand", "diagnose") if n not in seen]
        if (
            "diagnose" in seen
            and "correct" not in seen
            and any("correction_supported" in o.findings for o in state.observations)
        ):
            names.append("correct")
        names.append("stop")
    return tuple(
        ActionSpec(
            action_id=name,
            action_type="STOP"
            if name == "stop"
            else "DIAGNOSE"
            if name == "diagnose"
            else "INSPECT"
            if name == "inspect"
            else "RUN_EXPERIMENT",
            tool_id="suite." + name,
            split_id=task.split_policy.development_id,
            hypothesis=Hypothesis(
                hypothesis_id="suite-" + name,
                claim="Measure " + name,
                mechanism="Apply one registered immutable-baseline method",
                falsification="Measured objective fails the fixed target",
                origin="declared_fixture",
            ),
            estimated_cost=Cost()
            if name == "stop"
            else Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=1),
        )
        for name in names
    )


def _quality(loss: float, scale: float = 10) -> float:
    return 1 / (1 + scale * loss)


class SuiteAdapter:
    def __init__(self, fixture: Fixture):
        self.fixture = fixture

    def identity(self) -> dict[str, str]:
        return {"source": file_digest(Path(__file__)), "fixture": self.fixture.identity}

    def execute(self, name: str, *, formal: bool = False) -> Observation:
        f, v = self.fixture.family, self.fixture.variant
        if name not in {"inspect", "expand", "diagnose", "correct"}:
            raise ValueError("unknown registered suite tool")
        if f == "missing":
            return Observation(
                action_id=name,
                status="NEEDS_INPUT",
                result_origin="SIMULATED",
                findings=("missing_measurements",),
            )
        if f == "crash" and name == "expand":
            return Observation(
                action_id=name,
                status="FAILED",
                result_origin="SIMULATED",
                findings=("injected_tool_failure",),
            )
        if name == "diagnose":
            return Observation(
                action_id=name,
                result_origin="SIMULATED",
                findings=(
                    "no_correction" if f in {"negative", "sufficient"} else "correction_supported",
                ),
            )
        method = {"inspect": 0, "expand": 1, "correct": 2}[name]
        if f == "linear_bias":
            xs = [-2.0, -1.0, 0.0, 1.0, 2.0]
            bias = 1.5 + v / 10
            loss = sum(
                (2 * x + bias - (0 if method == 0 else 2 * x if method == 1 else 2 * x + bias)) ** 2
                for x in xs
            ) / len(xs)
            score = _quality(loss)
        elif f == "frequency_alias":
            xs = [2 * math.pi * i / 24 + (0.03 if formal else 0) for i in range(24)]
            frequency = 3 + v % 2
            loss = sum(
                (
                    math.sin(frequency * x)
                    - (
                        0
                        if method == 0
                        else math.sin(x)
                        if method == 1
                        else math.sin(frequency * x)
                    )
                )
                ** 2
                for x in xs
            ) / len(xs)
            score = _quality(loss)
        elif f == "shortest_path":
            # Three candidate paths in a weighted directed graph.
            edges = {(0, 3): 12 + v, (0, 1): 2, (1, 3): 6, (0, 2): 3, (2, 3): 1}
            cost = [edges[0, 3], edges[0, 1] + edges[1, 3], edges[0, 2] + edges[2, 3]][method]
            score = 4 / cost
        elif f == "allocation":
            # 0/1 capacity-six allocation: greedy value/weight misses the optimum.
            items = [(4, 8 + v), (3, 5 + v), (3, 5 + v)]
            optimum = 10 + 2 * v
            value = [0, items[0][1], optimum][method]
            score = value / optimum
        elif f == "robust_location":
            data = [-0.01, 0.0, 0.01, 0.0, 12.0 + v]
            estimate = [data[-1], sum(data) / len(data), sorted(data)[len(data) // 2]][method]
            score = _quality(estimate**2)
        elif f == "sufficient":
            score = _quality(0.001 + v * 0.0001)
        elif f == "root_finding":
            x = [0.0, 1.0, 1.0][method]
            for _ in range(0 if method == 0 else 1 if method == 1 else 5):
                x = (x + (2 + v * 0.1) / x) / 2
            score = _quality((x * x - (2 + v * 0.1)) ** 2)
        elif f == "matrix_projection":
            # Symmetric 2x2 matrix; diagonal-only vs exact top eigendirection.
            a, b, d = 2.0 + v * 0.1, 1.0, 2.0
            top_eigenvalue = (a + d + math.sqrt((a - d) ** 2 + 4 * b * b)) / 2
            achieved = [0.0, max(a, d), top_eigenvalue][method]
            score = achieved / top_eigenvalue
        elif f == "spurious_proxy":
            # Targeted correction fits a spurious development proxy; formal shifts it.
            score = [0.4, 0.94, 0.30 if formal else 0.96][method]
        elif f == "negative":
            score = _quality(1 + 0.05 * method)
        else:
            score = [0.3, 0.4, 0.95][method]
        return Observation(
            action_id=name,
            result_origin="SIMULATED",
            metrics={"objective_score": score},
            limitations=(
                "Declared deterministic mathematical fixture; no financial or general efficacy claim",
            ),
        )
