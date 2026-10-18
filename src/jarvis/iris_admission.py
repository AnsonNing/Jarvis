"""Read-only admission of completed owner-supplied multi-month model packages."""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from jarvis.contracts import AdmissionError, IntegrityError, Record
from jarvis.identity import digest, file_digest
from jarvis.iris import render_candidate, verify_environment
from jarvis.iris_cases import read_object, select_cases, selected_case
from jarvis.iris_contracts import (
    IrisBaselineProof,
    IrisBinding,
    IrisEnvironment,
    IrisPeriod,
    IrisSettings,
)
from jarvis.iris_design import IrisDesignProfile, design_capabilities


class IrisDevelopmentUse(Record):
    """Owner-bound preparation decision, never a submission or scientific PASS."""

    kind: Literal["IrisDevelopmentUse"] = "IrisDevelopmentUse"
    owner_instruction: str = Field(min_length=1, max_length=6000)
    scope: Literal["development_exposed"] = "development_exposed"
    execution_authorized: Literal[False] = False
    independent_validation: Literal[False] = False
    evidence: dict[str, str] = Field(min_length=1)
    attention_codes: tuple[Literal["analysis_findings", "final_findings"], ...] = ()
    known_limits: tuple[str, ...] = ()


def development_inputs(folder: Path) -> dict[str, str]:
    """Pin supplied source/model/evaluator identities without importing strategy code."""
    folder = folder.resolve()
    paths = [
        folder / name
        for name in (
            "workflow_state.json",
            "manifest.json",
            "protocol.json",
            "analysis.json",
            "final_acceptance.json",
            "history_monthly_acceptance.json",
            "training_quality_acceptance.json",
            "local_acceptance.json",
            "calendar_08.json",
            "calendar_09.json",
        )
    ]
    for side in ("long", "short"):
        paths.extend(
            folder / side / name
            for name in (
                "training_acceptance.json",
                "hpp_verification.json",
                "model_metadata.json",
            )
        )
        proof = read_object(folder / side / "hpp_verification.json")
        for model in proof.get("models", []):
            path = Path(model["hpp_file"])
            if file_digest(path) != "sha256:" + model["hpp_sha256"]:
                raise IntegrityError("HPP bytes differ from verification")
            paths.append(path)
    paths.extend(folder.parents[1] / name for name in ("iris_agent.py", "iris_entry_evaluation.py"))
    result = {str(p.resolve()): file_digest(p) for p in paths}
    manifest = read_object(folder / "manifest.json")
    result.update(select_cases(folder, manifest)["proof_inputs"])
    for case in select_cases(folder, manifest)["cases"]:
        source = Path(case["file"])
        if file_digest(source) != "sha256:" + case["sha256"]:
            raise IntegrityError("owner candidate source changed")
        result[str(source.resolve())] = file_digest(source)
    return result


def development_use(folder: Path, owner_instruction: str) -> IrisDevelopmentUse:
    status = model_status(folder)
    codes = set(status.get("attention_codes", []))
    if status["status"] not in {"READY", "MODEL_ACCEPTANCE_NEEDS_ATTENTION"} or not codes <= {
        "analysis_findings",
        "final_findings",
    }:
        raise AdmissionError("engineering/source/lineage gates must pass before development use")
    # An arbitrary non-PASS analysis is not a supported research finding.
    for name in ("analysis.json", "final_acceptance.json"):
        if read_object(folder / name).get("status") not in {"PASS", "COMPLETED_WITH_FINDINGS"}:
            raise AdmissionError("unsupported upstream finding status")
    final = read_object(folder / "final_acceptance.json")
    return IrisDevelopmentUse(
        owner_instruction=owner_instruction,
        evidence=development_inputs(folder),
        attention_codes=tuple(sorted(codes)),
        known_limits=tuple(final.get("limits", [])),
    )


def require_admission(folder: Path, use_path: Path | None) -> dict[str, str]:
    if use_path is None:
        status = model_status(folder)
        if status["status"] != "READY":
            raise AdmissionError(status["status"] + ": " + "; ".join(status["reasons"]))
        return {}
    supplied = IrisDevelopmentUse.model_validate(read_object(use_path))
    current = development_use(folder, supplied.owner_instruction)
    if supplied != current:
        raise IntegrityError("development-use evidence, source or findings changed")
    return {**supplied.evidence, str(use_path.resolve()): file_digest(use_path)}


def attach_design(env: IrisEnvironment, profile_path: Path) -> IrisEnvironment:
    """Owner-prepared integration; no agent-generated source or feature mapping."""
    profile_path = profile_path.resolve()
    profile = IrisDesignProfile.model_validate_json(profile_path.read_bytes())
    pinned = {**env.accepted_inputs, str(profile_path): file_digest(profile_path)}
    for name in [profile.build.compiler, *profile.build.chunks]:
        if Path(name).is_absolute():
            pinned[name] = file_digest(Path(name))
    value = IrisEnvironment.model_validate(
        {
            **env.model_dump(mode="json"),
            "bindings": [
                b.model_dump(mode="json") for b in (*env.bindings, *profile.scalar_bindings)
            ],
            "design_profile": str(profile_path),
            "accepted_inputs": pinned,
        }
    )
    verify_environment(value)
    # Validate scalar source matching, declared units and rule profile before use.
    from jarvis.iris import setting_capabilities

    if any(b["baseline_value"] is None for b in setting_capabilities(value)):
        raise AdmissionError("design scalar binding has no unique source constant")
    design_capabilities(value)
    return value


def model_status(folder: Path) -> dict[str, Any]:
    required = [
        "workflow_state.json",
        "manifest.json",
        "history_monthly_acceptance.json",
        "training_quality_acceptance.json",
        "local_acceptance.json",
        "analysis.json",
    ]
    reasons: list[str] = []
    codes: list[str] = []
    loaded: dict[str, Any] = {}
    for name in required:
        try:
            loaded[name] = read_object(folder / name)
        except IntegrityError as exc:
            reasons.append(str(exc))
            codes.append("missing_evidence")
    stage = loaded.get("workflow_state.json", {}).get("stage")
    manifest = loaded.get("manifest.json", {})
    cases = manifest.get("cases", [])
    if not isinstance(cases, list):
        cases = []
    progress = {
        "upstream_stage": stage,
        "completed_cases": sum(
            isinstance(c, dict) and c.get("status") == "completed" for c in cases
        ),
        "physical_requests": len(cases),
        "unresolved_superseded": [],
    }
    failed = stage == "needs_attention" or any(
        v.get("status") in {"FAIL", "FAILED", "REJECTED"} for v in loaded.values()
    )
    if failed:
        return {
            "status": "MODEL_ACCEPTANCE_FAILED",
            "reasons": [*reasons, "upstream workflow or acceptance requires attention"],
            **progress,
        }
    if reasons:
        return {
            "status": "MODEL_ACCEPTANCE_NEEDS_ATTENTION"
            if stage == "complete"
            else "WAITING_FOR_MODEL_ACCEPTANCE",
            "reasons": reasons,
            "attention_codes": codes,
            **progress,
        }
    if stage != "complete":
        reasons.append("upstream workflow has not completed")
        codes.append("workflow_incomplete")
    for name in required[2:]:
        if loaded[name].get("status") != "PASS":
            reasons.append("acceptance not PASS: " + name)
            codes.append(
                "analysis_findings" if name == "analysis.json" else "engineering_incomplete"
            )
    final_path = folder / "final_acceptance.json"
    if final_path.exists():
        try:
            final = read_object(final_path)
            if final.get("status") in {"FAIL", "FAILED", "REJECTED"}:
                return {
                    "status": "MODEL_ACCEPTANCE_FAILED",
                    "reasons": ["final acceptance failed"],
                    **progress,
                }
            if final.get("status") != "PASS":
                reasons.append("acceptance not PASS: final_acceptance.json")
                codes.append("final_findings")
        except IntegrityError as exc:
            reasons.append(str(exc))
            codes.append("missing_evidence")
    if not manifest.get("training_accepted") or not manifest.get("model_smokes_accepted"):
        reasons.append("training or model smoke gate incomplete")
        codes.append("engineering_incomplete")
    try:
        selection = select_cases(folder, manifest)
        progress["effective_cases"] = len(selection["cases"])
        progress["unresolved_superseded"] = selection["unresolved_superseded"]
        if any(c.get("status") != "completed" for c in selection["cases"]):
            reasons.append("unfinished effective upstream backtest cases")
            codes.append("unfinished_cases")
    except IntegrityError as exc:
        reasons.append(str(exc))
        codes.append("invalid_case_lineage")
    for side in ["long", "short"]:
        for name in ["training_acceptance.json", "hpp_verification.json", "model_metadata.json"]:
            path = folder / side / name
            try:
                value = read_object(path)
                if name != "model_metadata.json" and value.get("status") != "PASS":
                    reasons.append(side + " not accepted: " + name)
                    codes.append("engineering_incomplete")
            except IntegrityError as exc:
                reasons.append(side + ": " + str(exc))
                codes.append("missing_evidence")
    return {
        "status": "READY"
        if not reasons
        else (
            "MODEL_ACCEPTANCE_NEEDS_ATTENTION"
            if stage == "complete"
            else "WAITING_FOR_MODEL_ACCEPTANCE"
        ),
        "reasons": reasons,
        "attention_codes": sorted(set(codes)),
        **progress,
        "source": "owner-supplied corrected multi-month OOF/residual workflow; read-only inspection",
    }


def prepare_environment(
    folder: Path,
    side: Literal["long", "short"],
    month: str,
    python: Path,
    *,
    window: Literal["full", "smoke"] = "full",
    development_use_path: Path | None = None,
) -> IrisEnvironment:
    folder = folder.resolve()
    development_pins = require_admission(folder, development_use_path)
    root = folder.parents[1]
    manifest = json.loads((folder / "manifest.json").read_text())
    label = (
        f"multimonth_{side}_smoke"
        if window == "smoke"
        else f"multimonth_{side}_trading_cancel_{month}"
    )
    case = selected_case(folder, label)
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
    pinned.update(select_cases(folder, manifest)["proof_inputs"])
    pinned.update(development_pins)
    if (folder / "final_acceptance.json").exists():
        pinned[str(folder / "final_acceptance.json")] = file_digest(
            folder / "final_acceptance.json"
        )
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
    folder: Path,
    side: str,
    month: str,
    *,
    window: Literal["full", "smoke"] = "full",
    development_use_path: Path | None = None,
) -> IrisBaselineProof:
    require_admission(folder, development_use_path)
    manifest = folder / "manifest.json"
    label = (
        f"multimonth_{side}_smoke"
        if window == "smoke"
        else f"multimonth_{side}_trading_cancel_{month}"
    )
    case = selected_case(folder, label)
    candidates = [
        p
        for p in Path(case["result_dir"]).glob("*.csv")
        if "summary" not in p.name and p.name.startswith("sim.hermes_trades")
    ]
    if len(candidates) != 1:
        raise IntegrityError("baseline requires one native entry/snapshot ledger")
    return IrisBaselineProof(
        manifest=str(manifest),
        label=case["label"],
        raw=str(candidates[0]),
        raw_sha256=file_digest(candidates[0]),
    )
