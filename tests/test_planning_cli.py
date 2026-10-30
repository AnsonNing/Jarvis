"""The shipped planning example is runnable with normal CLI reservation defaults."""

import json
import subprocess
import sys
from pathlib import Path


def test_shipped_planning_declaration_runs_without_external_providers(tmp_path):
    executable = [sys.executable, "-m", "jarvis", "--store", str(tmp_path / "store"), "autonomy"]
    declaration = (
        Path(__file__).resolve().parents[1] / "examples/autonomy/planning.declaration.yaml"
    )

    def call(*arguments):
        result = subprocess.run(
            [*executable, *arguments], capture_output=True, text=True, timeout=60, check=True
        )
        return json.loads(result.stdout)

    owner = call("start", str(declaration))["owner"]
    completed = call("run", owner, "--wall-seconds", "30")
    assert completed["status"] == "CLOSED"
    assert (
        completed["status_detail"]["plan"]["planning"]["latest_review"]["review"]["stop_basis"]
        == "goal_met"
    )
    replay = call("replay", owner)
    assert replay["status"] == "COMPLETE" and replay["external_calls"] == 0
