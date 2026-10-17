"""Explicit trusted, inference-only bridge to the user-provided local runtime."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from jarvis.contracts import AdmissionError, IntegrityError
from jarvis.hermes import HermesAdapter


def materialize(root: Path, side: str, python: Path, output: Path) -> dict[str, Any]:
    root, output = root.resolve(), output.resolve()
    # Keep the venv executable symlink: resolving it would discard its environment.
    python = python.absolute()
    if output.is_relative_to(root) or output.exists():
        raise AdmissionError("choose a new output folder outside the upstream repository")
    adapter = HermesAdapter(root, side)
    adapter._verify_sources()
    before = adapter.hashes()
    command = [
        str(python),
        str(Path(__file__).with_name("hermes_worker.py")),
        "--root",
        str(root),
        "--side",
        side,
        "--output",
        str(output),
    ]
    clean_env = {
        k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"}
    }
    clean_env.update({"PYTHONNOUSERSITE": "1", "OMP_NUM_THREADS": "2"})
    try:
        result = subprocess.run(command, env=clean_env, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise AdmissionError(
            "inference worker timed out; inspect its private output before retrying"
        ) from exc
    if adapter.hashes() != before:
        raise IntegrityError("upstream protected inputs changed during materialization")
    if result.returncode:
        raise AdmissionError("inference-only materialization failed: " + result.stderr[-4000:])
    derived = HermesAdapter(root, side, output)
    derived.validate(derived.task())
    metrics = derived.baseline()
    return {
        "status": "MATERIALIZED_AND_VERIFIED",
        "output": str(output),
        "metrics": metrics,
        "upstream_inputs_unchanged": True,
        "training_runs": 0,
        "event_backtests": 0,
        "scope": "existing_model_inference_and_dataset_replay",
        "provenance": json.loads((output / "provenance.json").read_text()),
    }
