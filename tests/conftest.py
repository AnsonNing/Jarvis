from pathlib import Path

import pytest
import yaml

from jarvis.contracts import ResearchTask


@pytest.fixture
def task():
    return ResearchTask.model_validate(
        yaml.safe_load(Path("examples/research_task.example.yaml").read_text())
    )
