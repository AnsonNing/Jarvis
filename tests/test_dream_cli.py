import json
import subprocess
import sys

from jarvis.dream_cli import markdown, verify_study
from jarvis.storage import Store


def test_cli_space_reports_missing_exit_backend(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "jarvis", "--store", str(tmp_path), "rsi", "space"],
        capture_output=True,
        text=True,
        check=True,
    )
    value = json.loads(result.stdout)
    assert len(value["candidates"]) == 54
    assert value["scope"] == "DESIGN_ONLY"


def test_cli_study_report_and_offline_verify(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "jarvis", "--store", str(tmp_path), "rsi", "study"],
        capture_output=True,
        text=True,
        check=True,
    )
    value = json.loads(result.stdout)
    sid = value["run_id"]
    store = Store(tmp_path)
    assert verify_study(store, sid)["rounds"] == 3
    rendered = markdown(store, value["report"])
    assert "rolled back" in rendered
    assert "NO_PROMOTION" in rendered
    resumed = subprocess.run(
        [sys.executable, "-m", "jarvis", "--store", str(tmp_path), "rsi", "resume", sid],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(resumed.stdout)["report_ref"] == value["report_ref"]
