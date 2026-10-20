"""Owner-selected frozen package admission, without weakening upstream model gates.

Inputs and source bindings are supplied privately by the trusted operator.
This adapter verifies bytes, completed-job lineage and an explicit market calendar;
it never trains a model, uploads a candidate or converts findings into PASS.
"""

import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field

from jarvis.contracts import IntegrityError, Record
from jarvis.identity import digest, file_digest
from jarvis.iris import render_candidate, verify_environment
from jarvis.iris_admission import attach_design
from jarvis.iris_contracts import (
    IrisBaselineProof,
    IrisBinding,
    IrisEnvironment,
    IrisPeriod,
    IrisSettings,
)


class SelectedBaselineSpec(Record):
    kind: Literal["SelectedBaselineSpec"] = "SelectedBaselineSpec"
    package: str
    manifest: str
    settings: str
    hash_index: str
    calendar: str
    source: str
    api_root: str
    python: str
    evaluator: str
    evaluator_dependencies: tuple[str, ...] = ()
    side: Literal["long", "short"]
    baseline_commit: str = Field(pattern=r"^[a-f0-9]{7,40}$")
    case_label: str
    period: IrisPeriod
    bindings: tuple[IrisBinding, ...] = Field(min_length=1)
    design_profile: str | None = None
    owner_instruction: str = Field(min_length=1, max_length=6000)
    known_findings: tuple[str, ...] = ()
    selection_record: str | None = None


def prepare_selected(spec: SelectedBaselineSpec) -> tuple[IrisEnvironment, IrisBaselineProof]:
    if spec.period.exposure != "development_exposed":
        raise IntegrityError("selected exposed package cannot establish unseen validation")
    package = Path(spec.package).resolve()
    index = json.loads(Path(spec.hash_index).read_text())
    pinned = {}
    if spec.selection_record:
        if (
            SelectedBaselineSpec.model_validate_json(Path(spec.selection_record).read_bytes())
            != spec
        ):
            raise IntegrityError("owner selection record differs from requested declaration")
        pinned[str(Path(spec.selection_record).resolve())] = file_digest(
            Path(spec.selection_record)
        )
    for relative, expected in index.items():
        path = (package / relative).resolve()
        if not path.is_relative_to(package) or not path.is_file():
            raise IntegrityError("selected package index escapes package or has missing assets")
        actual = file_digest(path)
        if actual != "sha256:" + expected:
            raise IntegrityError("selected package bytes differ from index: " + relative)
        pinned[str(path)] = actual
    for name in [
        spec.manifest,
        spec.settings,
        spec.hash_index,
        spec.calendar,
        spec.source,
        spec.evaluator,
        *spec.evaluator_dependencies,
    ]:
        pinned[str(Path(name).resolve())] = file_digest(Path(name))
    settings = json.loads(Path(spec.settings).read_text())
    manifest = json.loads(Path(spec.manifest).read_text())
    cases = [c for c in manifest["cases"] if c["label"] == spec.case_label]
    if len(cases) != 1:
        raise IntegrityError("selected baseline requires one explicit original case")
    case = cases[0]
    source_hash = file_digest(Path(spec.source))
    selected = [c for c in settings["commits"] if c["side"].lower() == spec.side]
    if (
        not selected
        or {c["commit"] for c in selected} != {spec.baseline_commit}
        or {"sha256:" + c["source_sha256"] for c in selected} != {source_hash}
        or case["side"].lower() != spec.side
        or case["status"] != "completed"
        or case["result_exit_code"] != 0
        or "sha256:" + case["sha256"] != source_hash
        or case["job"]["branch"] != settings["branch"]
        or manifest["branch"] != settings["branch"]
    ):
        raise IntegrityError("selected source/model/side/commit/job lineage mismatch")
    models = [m for m in settings["models"] if m["side"].lower() == spec.side]
    if len(models) != 2 or any(
        file_digest(package / m["hpp_file"]) != "sha256:" + m["hpp_sha256"]
        or m.get("max_score_error") != 0
        or m.get("decision_mismatches") != 0
        for m in models
    ):
        raise IntegrityError("selected model bytes or declared parity evidence disagree")
    raw_calendar = json.loads(Path(spec.calendar).read_text())
    all_dates = sorted({d for values in raw_calendar.values() for d in values})
    begin, end = case["job"]["period"]
    parent_dates = [d for d in all_dates if begin.replace("/", "-") <= d <= end.replace("/", "-")]
    selected_dates = [str(d) for d in spec.period.trading_dates]
    completion = re.search(r"(\d+) days, (\d+) failed", case["result_output"])
    if (
        not completion
        or int(completion[1]) != len(parent_dates)
        or int(completion[2])
        or not set(selected_dates) <= set(parent_dates)
    ):
        raise IntegrityError("selected baseline calendar/completion mismatch")
    raw_files = [
        p
        for p in Path(case["result_dir"]).glob("sim.hermes_trades*.csv")
        if "summary" not in p.name
    ]
    if len(raw_files) != 1:
        raise IntegrityError("selected baseline requires one original raw ledger")
    env = IrisEnvironment(
        root=str(Path(spec.api_root).resolve()),
        python=str(Path(spec.python).resolve()),
        branch=settings["branch"],
        template=str(Path(spec.source).resolve()),
        template_sha256=source_hash,
        evaluator=str(Path(spec.evaluator).resolve()),
        evaluator_sha256=file_digest(Path(spec.evaluator)),
        agent_script_sha256=file_digest(Path(spec.api_root) / "iris_agent.py"),
        model_lineage=digest(
            {"baseline_commit": spec.baseline_commit, "models": models, "source": source_hash}
        ),
        accepted_inputs=pinned,
        bindings=spec.bindings,
        period=spec.period,
        side=spec.side,
    )
    if spec.design_profile:
        env = attach_design(env, Path(spec.design_profile))
    if render_candidate(env, IrisSettings()) != Path(spec.source).read_text():
        raise IntegrityError("selected baseline rendering changed bytes")
    verify_environment(env)
    sliced = selected_dates != parent_dates
    proof = IrisBaselineProof(
        manifest=str(Path(spec.manifest).resolve()),
        label=spec.case_label,
        raw=str(raw_files[0].resolve()),
        raw_sha256=file_digest(raw_files[0]),
        selection_dates=tuple(date.fromisoformat(d) for d in selected_dates) if sliced else None,
        parent_trading_dates=tuple(date.fromisoformat(d) for d in parent_dates) if sliced else None,
    )
    return env, proof
