import math

import pytest
import yaml
from pydantic import ValidationError

from jarvis.contracts import Budget, ResearchTask, Splits
from jarvis.identity import canonical, digest


def test_example_roundtrip():
    task = ResearchTask.model_validate(yaml.safe_load(open("examples/research_task.example.yaml")))
    assert ResearchTask.model_validate_json(task.model_dump_json()) == task
    assert task.identity == digest(task.model_dump(mode="json"))
    assert digest({"b": 2, "a": 1}) == digest({"a": 1, "b": 2})


@pytest.mark.parametrize(
    "data",
    [
        {"schema_version": 2},
        {"oops": 1},
        {"max_actions": -1},
        {"max_actions": True},
        {"max_wall_seconds": math.inf},
        {"max_compute_units": math.nan},
        {"max_parallel_jobs": 0},
    ],
)
def test_reject_invalid_budgets(data):
    with pytest.raises(ValidationError):
        Budget(**data)


def test_split_alias_refused():
    with pytest.raises(ValidationError):
        Splits(development_id="same", selection_id="same")


def test_nonfinite_canonical_json_refused():
    with pytest.raises(ValueError):
        canonical({"score": math.nan})
