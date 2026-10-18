import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_iris_admission import package, write

from jarvis.contracts import AdmissionError, IntegrityError
from jarvis.iris import verify_environment
from jarvis.iris_admission import (
    development_use,
    model_status,
    prepare_environment,
    require_admission,
)
from jarvis.iris_continuous import prepare_continuous
from jarvis.iris_readiness import research_readiness


def inputs(tmp_path):
    folder = tmp_path / "iris_experiments" / "bundle"
    m = package(folder, findings=True)
    original = copy.deepcopy(m["cases"][0])
    for side, window, rid in (
        ("long", "smoke", "44444444"),
        ("short", "full", "55555555"),
        ("short", "smoke", "66666666"),
    ):
        case = copy.deepcopy(original)
        case.update(
            label=f"multimonth_{side}_smoke"
            if window == "smoke"
            else f"multimonth_{side}_trading_cancel_08",
            side=side.upper(),
            request_id=rid,
        )
        if window == "smoke":
            case.update(period="2026/08/03-2026/08/04")
            case["job"]["period"] = case["period"].split("-")
        m["cases"].append(case)
    write(folder / "manifest.json", m)
    for name in ("iris_agent.py", "iris_entry_evaluation.py"):
        (tmp_path / name).write_text("raise AssertionError('readiness imported private runtime')\n")
    certificate = tmp_path / "development-use.json"
    write(
        certificate,
        development_use(folder, "Owner selects the supplied model for preparation only").model_dump(
            mode="json"
        ),
    )
    return folder, certificate


def test_owner_development_use_keeps_findings_and_grants_no_execution(tmp_path):
    folder, certificate = inputs(tmp_path)
    before = model_status(folder)
    result = require_admission(folder, certificate)
    assert result and model_status(folder) == before
    assert before["status"] == "MODEL_ACCEPTANCE_NEEDS_ATTENTION"
    assert json.loads(certificate.read_text())["execution_authorized"] is False
    with pytest.raises(AdmissionError):
        require_admission(folder, None)


@pytest.mark.parametrize("changed", ["analysis", "hpp", "engineering", "source"])
def test_model_or_evidence_change_invalidates_prepared_development_use(tmp_path, changed):
    folder, certificate = inputs(tmp_path)
    if changed == "analysis":
        write(folder / "analysis.json", {"status": "PASS"})
    elif changed == "engineering":
        write(folder / "local_acceptance.json", {"status": "FAIL"})
    else:
        path = folder / "long/artificial.hpp" if changed == "hpp" else folder / "artificial.txt"
        path.write_text("changed")
    with pytest.raises((IntegrityError, AdmissionError)):
        require_admission(folder, certificate)


def test_readiness_prepares_four_tracks_without_executing_or_creating_store(tmp_path, monkeypatch):
    folder, certificate = inputs(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: pytest.fail("readiness ran a process"))
    result = research_readiness(folder, Path(sys.executable), development_use_path=certificate)
    assert result["status"] == "PREPARED_NOT_STARTED"
    assert len(result["tracks"]) == 4 and not result["blockers"]
    assert result["new_llm_calls"] == result["new_remote_submissions"] == result["new_studies"] == 0
    assert result["execution_authorized"] is False
    assert all(t["period"]["exposure"] == "development_exposed" for t in result["tracks"])
    assert all(t["baseline_accounting"] == "NOT_RECOMPUTED_BY_READINESS" for t in result["tracks"])


def test_readiness_cli_never_constructs_an_evidence_store(tmp_path):
    folder, certificate = inputs(tmp_path)
    store = tmp_path / "must-not-exist"
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "jarvis",
            "--store",
            str(store),
            "iris",
            "readiness",
            "--upstream",
            str(folder),
            "--python",
            sys.executable,
            "--development-use",
            str(certificate),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(ran.stdout)["status"] == "PREPARED_NOT_STARTED"
    assert not store.exists()


def test_readiness_without_owner_use_reports_blockers_and_does_not_execute(tmp_path):
    folder, certificate = inputs(tmp_path)
    result = research_readiness(folder, Path(sys.executable))
    assert result["status"] == "NEEDS_INPUT" and len(result["blockers"]) == 4
    assert result["execution_authorized"] is False


def test_continuous_spec_preparation_uses_owner_selected_model_without_start(tmp_path, monkeypatch):
    folder, certificate = inputs(tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda *_, **__: pytest.fail("preparation executed backend")
    )
    spec = prepare_continuous(
        folder,
        Path(sys.executable),
        "Preparation only; do not start",
        development_use_path=certificate,
    )
    assert len(spec.tracks) == 2
    assert all(q.authorization is None for t in spec.tracks for q in (t.smoke, t.development))
    assert all(
        str(certificate.resolve()) in t.development.environment.accepted_inputs for t in spec.tracks
    )


def test_development_use_cannot_be_relabelled_as_independent_heldout(tmp_path):
    folder, certificate = inputs(tmp_path)
    env = prepare_environment(
        folder, "long", "08", Path(sys.executable), development_use_path=certificate
    )
    independent = env.model_copy(
        update={"period": env.period.model_copy(update={"exposure": "sealed_unseen"})}
    )
    with pytest.raises(IntegrityError, match="independent test"):
        verify_environment(independent)
