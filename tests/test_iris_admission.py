import copy
import json
from datetime import UTC, datetime

import pytest

from jarvis.contracts import IntegrityError
from jarvis.identity import file_digest
from jarvis.iris_admission import model_status, prepare_baseline
from jarvis.iris_cases import select_cases


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def package(folder, *, retry=False, findings=False):
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "artificial.txt"
    source.write_text(
        "// artificial owner source, no market logic\n// @start:config\n"
        + "\n".join(
            f"constexpr double {symbol} = 0.004;"
            for symbol in (
                "RATIO_LONG_PT",
                "RATIO_LONG_SL",
                "RATIO_SHORT_PT",
                "RATIO_SHORT_SL",
                "LONG_CANCEL_THRESHOLD",
                "SHORT_CANCEL_THRESHOLD",
                "THRESHOLD_HANG_LONG",
                "THRESHOLD_HANG_SHORT",
            )
        )
        + "\nconstexpr int TIME_LOG_CHECK_CANCEL_MS = 100;\n"
        "#define BYPASS_HEURISTIC_CANCEL false\n#define BYPASS_CANCEL_MODELS false\n"
        "// @end:config\n// @start:cancelmodellong\n"
        "constexpr double kInitialCutoff = 0.5; // score: p_bad\n// @end:cancelmodellong\n"
        "// @start:cancelmodelshort\nconstexpr double kInitialCutoff = 0.5; // score: p_bad\n"
        "// @end:cancelmodelshort\n"
    )
    sha = file_digest(source)[7:]
    raw = folder / "result" / "sim.hermes_trades_artificial.csv"
    raw.parent.mkdir()
    raw.write_text("artificial ledger\n")
    case = {
        "label": "multimonth_long_trading_cancel_08",
        "request_id": "11111111",
        "status": "completed",
        "sha256": sha,
        "file": str(source),
        "side": "LONG",
        "generation": "new",
        "mode": "trading_cancel",
        "comparison_month": "08",
        "phase": "comparison",
        "period": "2026/08/01-2026/08/31",
        "submitted_at": 100,
        "result_exit_code": 0,
        "result_dir": str(raw.parent),
        "job": {"branch": "fixture", "period": ["2026/08/01", "2026/08/31"]},
    }
    manifest = {
        "branch": "fixture",
        "training_accepted": True,
        "model_smokes_accepted": True,
        "cases": [case],
    }
    for name in [
        "history_monthly_acceptance.json",
        "training_quality_acceptance.json",
        "local_acceptance.json",
    ]:
        write(folder / name, {"status": "PASS"})
    write(
        folder / "local_acceptance.json",
        {"status": "PASS", "cases": [{"source_sha256": sha, "status": "PASS"}]},
    )
    write(folder / "workflow_state.json", {"stage": "complete"})
    write(folder / "protocol.json", {"origin": "SIMULATED_FIXTURE"})
    for month in ("08", "09"):
        write(
            folder / ("calendar_" + month + ".json"),
            {"dates": [f"2026-{month}-03", f"2026-{month}-04"]},
        )
    status = "COMPLETED_WITH_FINDINGS" if findings else "PASS"
    write(folder / "analysis.json", {"status": status})
    write(
        folder / "final_acceptance.json",
        {"status": status, "runtime_gate_exact_replay_passed": not findings},
    )
    for side in ["long", "short"]:
        for name in ["training_acceptance.json", "hpp_verification.json", "model_metadata.json"]:
            write(folder / side / name, {"status": "PASS"})
        hpp = folder / side / "artificial.hpp"
        hpp.write_text("// artificial exported model, never a financial result\n")
        write(
            folder / side / "hpp_verification.json",
            {
                "status": "PASS",
                "models": [{"hpp_file": str(hpp), "hpp_sha256": file_digest(hpp)[7:]}],
            },
        )
    if retry:
        original = copy.deepcopy(case)
        reference = {**copy.deepcopy(case), "label": "reference", "request_id": "22222222"}
        original.update(status="queued", result_exit_code=4)
        write(folder / "interim" / "manifest.json", {"cases": [reference, original]})
        original.update(status="superseded", superseded_by="replacement")
        replacement = {
            **copy.deepcopy(case),
            "label": "replacement",
            "request_id": "33333333",
            "retry_of": "11111111",
            "logical_case_label": case["label"],
            "submitted_at": 300,
        }
        auth = {
            "source": "Direct human instruction in this chat",
            "instruction": "Artificial owner authorized one replacement",
            "recorded_at_utc": datetime.fromtimestamp(200, UTC).isoformat(),
            "original_request": "11111111",
            "new_request": "33333333",
            "retry_label": "replacement",
            "identical_source_sha256": sha,
            "max_additional_submissions": 1,
            "status": "submitted_once",
            "automatic_retry": False,
        }
        manifest.update(cases=[reference, original, replacement], authorized_retries=[auth])
        write(folder / "RESUBMISSION_AUTHORIZATION.json", auth)
        write(
            folder / "retry_scope_acceptance.json",
            {
                "status": "PASS",
                "new_requests": ["33333333"],
                "candidate_sha256": sha,
                "model_hpp_unchanged": True,
                "automatic_resubmissions": 0,
                "previous_completed_count": 1,
                "previous_completed_unchanged": ["22222222"],
            },
        )
    write(folder / "manifest.json", manifest)
    return manifest


def test_verified_replacement_selects_one_result_and_retains_unknown_original(tmp_path):
    manifest = package(tmp_path, retry=True)
    before = (tmp_path / "manifest.json").read_bytes()
    selection = select_cases(tmp_path, manifest)
    assert len(selection["cases"]) == 2
    assert selection["unresolved_superseded"] == ["11111111"]
    assert len(selection["proof_inputs"]) == 4
    state = model_status(tmp_path)
    assert state["status"] == "READY" and state["effective_cases"] == 2
    assert state["completed_cases"] == 2 and state["physical_requests"] == 3
    assert prepare_baseline(tmp_path, "long", "08").label == "replacement"
    assert (tmp_path / "manifest.json").read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    [
        "source",
        "period",
        "side",
        "orphan",
        "duplicate",
        "missing_authorization",
        "authorization_late",
        "extra_submission",
        "changed_completed",
        "missing_scope_proof",
        "chain",
    ],
)
def test_invalid_replacement_never_bypasses_model_gate(tmp_path, mutation):
    m = package(tmp_path, retry=True)
    if mutation in ("source", "period", "side"):
        m["cases"][-1][{"source": "sha256", "period": "period", "side": "side"}[mutation]] = (
            "changed"
        )
    elif mutation == "orphan":
        del m["cases"][1]["superseded_by"]
    elif mutation == "duplicate":
        m["cases"][-1]["request_id"] = m["cases"][0]["request_id"]
    elif mutation == "missing_authorization":
        (tmp_path / "RESUBMISSION_AUTHORIZATION.json").unlink()
    elif mutation == "authorization_late":
        m["authorized_retries"][0]["recorded_at_utc"] = datetime.fromtimestamp(400, UTC).isoformat()
        write(tmp_path / "RESUBMISSION_AUTHORIZATION.json", m["authorized_retries"][0])
    elif mutation == "extra_submission":
        m["cases"].append({**m["cases"][0], "label": "extra", "request_id": "44444444"})
    elif mutation == "changed_completed":
        m["cases"][0]["job"]["branch"] = "changed"
    elif mutation == "missing_scope_proof":
        (tmp_path / "retry_scope_acceptance.json").unlink()
    else:
        m["cases"][-1]["superseded_by"] = m["cases"][0]["label"]
    write(tmp_path / "manifest.json", m)
    with pytest.raises(IntegrityError):
        select_cases(tmp_path, m)
    state = model_status(tmp_path)
    assert state["status"] == "MODEL_ACCEPTANCE_NEEDS_ATTENTION"
    assert "invalid_case_lineage" in state["attention_codes"]


def test_completed_findings_stop_waiting_without_claiming_pass(tmp_path):
    package(tmp_path, retry=True, findings=True)
    state = model_status(tmp_path)
    assert state["status"] == "MODEL_ACCEPTANCE_NEEDS_ATTENTION"
    assert set(state["attention_codes"]) == {"analysis_findings", "final_findings"}
    assert state["effective_cases"] == 2
    assert "unfinished" not in " ".join(state["reasons"])


def test_unreadable_side_evidence_is_reported_without_crashing_or_admission(tmp_path):
    package(tmp_path)
    (tmp_path / "short" / "hpp_verification.json").write_text("[]")
    assert model_status(tmp_path)["status"] == "MODEL_ACCEPTANCE_NEEDS_ATTENTION"
