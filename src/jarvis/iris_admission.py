"""Read-only admission of completed owner-supplied multi-month model packages."""

import json
from pathlib import Path
from typing import Any, Literal

from jarvis.contracts import AdmissionError, IntegrityError
from jarvis.identity import digest, file_digest
from jarvis.iris import render_candidate, verify_environment
from jarvis.iris_contracts import (
    IrisBaselineProof,
    IrisBinding,
    IrisEnvironment,
    IrisPeriod,
    IrisSettings,
)


def model_status(folder: Path) -> dict[str, Any]:
    required = [
        "workflow_state.json",
        "manifest.json",
        "history_monthly_acceptance.json",
        "training_quality_acceptance.json",
        "local_acceptance.json",
        "analysis.json",
    ]
    reasons = []
    for name in required:
        if not (folder / name).is_file():
            reasons.append("missing " + name)
    if reasons:
        return {"status": "WAITING_FOR_MODEL_ACCEPTANCE", "reasons": reasons}
    loaded = {n: json.loads((folder / n).read_text()) for n in required}
    if loaded["workflow_state.json"].get("stage") != "complete":
        reasons.append("upstream workflow has not completed")
    for name in required[2:]:
        if loaded[name].get("status") != "PASS":
            reasons.append("acceptance not PASS: " + name)
    manifest = loaded["manifest.json"]
    if not manifest.get("training_accepted") or not manifest.get("model_smokes_accepted"):
        reasons.append("training or model smoke gate incomplete")
    if any(c["status"] != "completed" for c in manifest["cases"]):
        reasons.append("unfinished upstream backtest cases")
    for side in ["long", "short"]:
        for name in ["training_acceptance.json", "hpp_verification.json", "model_metadata.json"]:
            path = folder / side / name
            if not path.is_file():
                reasons.append(side + " missing " + name)
            elif (
                name != "model_metadata.json"
                and json.loads(path.read_text()).get("status") != "PASS"
            ):
                reasons.append(side + " not accepted: " + name)
    return {
        "status": "READY" if not reasons else "WAITING_FOR_MODEL_ACCEPTANCE",
        "reasons": reasons,
        "upstream_stage": loaded["workflow_state.json"].get("stage"),
        "completed_cases": sum(c["status"] == "completed" for c in manifest["cases"]),
        "source": "owner-supplied corrected multi-month OOF/residual workflow; read-only inspection",
    }


def prepare_environment(
    folder: Path,
    side: Literal["long", "short"],
    month: str,
    python: Path,
    *,
    window: Literal["full", "smoke"] = "full",
) -> IrisEnvironment:
    folder = folder.resolve()
    status = model_status(folder)
    if status["status"] != "READY":
        raise AdmissionError("WAITING_FOR_MODEL_ACCEPTANCE: " + "; ".join(status["reasons"]))
    root = folder.parents[1]
    manifest = json.loads((folder / "manifest.json").read_text())
    label = (
        f"multimonth_{side}_smoke"
        if window == "smoke"
        else f"multimonth_{side}_trading_cancel_{month}"
    )
    case = next(c for c in manifest["cases"] if c["label"] == label)
    if window == "smoke" and month != "08":
        raise AdmissionError("accepted smoke calendar belongs to month 08")
    template = Path(case["file"])
    if file_digest(template) != "sha256:" + case["sha256"]:
        raise IntegrityError("accepted candidate source changed")
    local = json.loads((folder / "local_acceptance.json").read_text())
    if not any(
        c["source_sha256"] == case["sha256"] and c["status"] == "PASS" for c in local["cases"]
    ):
        raise IntegrityError("candidate does not have compiled-source acceptance")
    pinned = {
        str(folder / name): file_digest(folder / name)
        for name in [
            "manifest.json",
            "protocol.json",
            "history_monthly_acceptance.json",
            "training_quality_acceptance.json",
            "local_acceptance.json",
            "analysis.json",
        ]
    }
    for direction in ["long", "short"]:
        for name in ["model_metadata.json", "training_acceptance.json", "hpp_verification.json"]:
            path = folder / direction / name
            pinned[str(path)] = file_digest(path)
        proof = json.loads((folder / direction / "hpp_verification.json").read_text())
        for model in proof["models"]:
            p = Path(model["hpp_file"])
            if file_digest(p) != "sha256:" + model["hpp_sha256"]:
                raise IntegrityError("HPP source changed after acceptance")
            pinned[str(p)] = file_digest(p)
    dates = json.loads((folder / f"calendar_{month}.json").read_text())["dates"]
    pinned[str(folder / f"calendar_{month}.json")] = file_digest(folder / f"calendar_{month}.json")
    period = case.get("period") or "-".join(case["job"]["period"])
    begin, end = period.split("-")
    dates = [d for d in dates if begin.replace("/", "-") <= d <= end.replace("/", "-")]
    upper = side.upper()
    bindings = [
        IrisBinding(
            setting="take_profit_bps",
            symbol=f"RATIO_{upper}_PT",
            region="config",
            syntax="double",
            scale=0.0001,
            minimum=0.0001,
            maximum=0.1,
        ),
        IrisBinding(
            setting="stop_loss_bps",
            symbol=f"RATIO_{upper}_SL",
            region="config",
            syntax="double",
            scale=0.0001,
            minimum=0.0001,
            maximum=0.1,
        ),
        IrisBinding(
            setting="heuristic_cancel_bps",
            symbol=f"{upper}_CANCEL_THRESHOLD",
            region="config",
            syntax="double",
            scale=0.0001,
            minimum=0,
            maximum=0.1,
        ),
        IrisBinding(
            setting="trading_cutoff_delta",
            symbol=f"THRESHOLD_HANG_{upper}",
            region="config",
            syntax="double",
            mode="delta",
        ),
        IrisBinding(
            setting="sampling_ms",
            symbol="TIME_LOG_CHECK_CANCEL_MS",
            region="config",
            syntax="int",
            minimum=10,
            maximum=5000,
        ),
        IrisBinding(
            setting="bypass_heuristic_cancel",
            symbol="BYPASS_HEURISTIC_CANCEL",
            region="config",
            syntax="macro_bool",
        ),
        IrisBinding(
            setting="bypass_cancel_model",
            symbol="BYPASS_CANCEL_MODELS",
            region="config",
            syntax="macro_bool",
        ),
    ]
    env = IrisEnvironment(
        root=str(root),
        python=str(python.absolute()),
        branch=manifest["branch"],
        template=str(template),
        template_sha256=file_digest(template),
        evaluator=str(root / "iris_entry_evaluation.py"),
        evaluator_sha256=file_digest(root / "iris_entry_evaluation.py"),
        agent_script_sha256=file_digest(root / "iris_agent.py"),
        accepted_inputs=pinned,
        model_lineage=digest(
            {
                "training": pinned[str(folder / "training_quality_acceptance.json")],
                "models": [v for p, v in pinned.items() if p.endswith("model_metadata.json")],
            }
        ),
        bindings=tuple(bindings),
        period=IrisPeriod.model_validate(
            {
                "start": begin.replace("/", "-"),
                "end": end.replace("/", "-"),
                "trading_dates": dates,
                "group_id": f"retrospective-{month}",
                "exposure": "development_exposed",
            }
        ),
        side=side,
    )
    # Infer only the optional exported cutoff binding, never a feature formula.
    risk = IrisBinding(
        setting="cancel_risk_delta",
        symbol="kInitialCutoff",
        region="cancelmodel" + side,
        syntax="double",
        mode="delta",
        minimum=0
        if "score: p_bad"
        in template.read_text()
        .split("// @start:cancelmodel" + side, 1)[1]
        .split("// @end:cancelmodel" + side, 1)[0]
        else None,
        maximum=1
        if "score: p_bad"
        in template.read_text()
        .split("// @start:cancelmodel" + side, 1)[1]
        .split("// @end:cancelmodel" + side, 1)[0]
        else None,
    )
    candidate = env.model_copy(update={"bindings": (*env.bindings, risk)})
    try:
        render_candidate(candidate, IrisSettings(cancel_risk_delta=0.000001))
    except AdmissionError:
        pass  # e.g. an intentionally infinite/disabled exported cutoff.
    else:
        env = candidate
    verify_environment(env)
    return env


def prepare_baseline(
    folder: Path, side: str, month: str, *, window: Literal["full", "smoke"] = "full"
) -> IrisBaselineProof:
    if model_status(folder)["status"] != "READY":
        raise AdmissionError("multi-month model package is not fully accepted")
    manifest = folder / "manifest.json"
    label = (
        f"multimonth_{side}_smoke"
        if window == "smoke"
        else f"multimonth_{side}_trading_cancel_{month}"
    )
    case = next(c for c in json.loads(manifest.read_text())["cases"] if c["label"] == label)
    candidates = [
        p
        for p in Path(case["result_dir"]).glob("*.csv")
        if "summary" not in p.name and p.name.startswith("sim.hermes_trades")
    ]
    if len(candidates) != 1:
        raise IntegrityError("baseline requires one native entry/snapshot ledger")
    return IrisBaselineProof(
        manifest=str(manifest),
        label=label,
        raw=str(candidates[0]),
        raw_sha256=file_digest(candidates[0]),
    )
