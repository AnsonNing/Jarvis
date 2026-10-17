"""Synthetic artifact fixtures only; no real order data or private source."""

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from jarvis.campaign import CampaignEngine
from jarvis.contracts import AdmissionError, IntegrityError
from jarvis.hermes import HermesAdapter
from jarvis.identity import file_digest
from jarvis.materialize import materialize
from jarvis.reporting import report
from jarvis.storage import Store


def write_json(path, value):
    path.write_text(json.dumps(value))


@pytest.fixture
def hermes(tmp_path):
    root = tmp_path / "synthetic-inputs"
    out = root / "joint_residual_outputs" / "optimized_v1" / "long"
    out.mkdir(parents=True)
    (root / "README.md").write_text("| LONG | T0+C0 |\n")
    source = root / "synthetic.parquet"
    pd.DataFrame({f"fs{i}": [0.0] * 4 for i in range(91)}).to_parquet(source)
    module = root / "hermes_joint_policy_pipeline.py"
    module.write_text("# Synthetic fixture placeholder; never imported.\n")
    (out / "models").mkdir()
    model = out / "models" / "synthetic.pkl"
    model.write_bytes(b"Synthetic bytes, deliberately not a valid pickle")
    frame = pd.DataFrame(
        {
            "row_id": [0, 1, 2, 3],
            "episode_id": ["a", "b", "c", "d"],
            "txdate": ["20260803", "20260803", "20260804", "20260804"],
            "product": ["TOY"] * 4,
            "placetime": ["1", "2", "3", "4"],
            "entry_pl": [5.0, -2.0, 8.0, -4.0],
            "entry_pr": [0.05, -0.02, 0.08, -0.04],
            "t_score": [0.9, 0.8, 0.7, 0.1],
            "t_cut": [0.5] * 4,
            "c_max_risk": [0.2, 0.9, 0.3, float("nan")],
            "c_cut": [0.5] * 4,
            "has_event": [True, True, True, False],
            "trading_pass": [True, True, True, False],
            "potential_cancel": [False, True, False, False],
            "cancelled": [False, True, False, False],
            "kept": [True, False, True, False],
            "policy_pl": [5.0, 0.0, 8.0, 0.0],
        }
    )
    frame.to_parquet(out / "selected_august_orders.parquet", index=False)
    pd.DataFrame(
        {"row_id": [1], "start_seconds": [2.0], "place_seconds": [1.0], "fill_seconds": [3.0]}
    ).to_parquet(out / "selected_first_crossings.parquet", index=False)
    metadata = {
        "source_file": str(source),
        "source_sha256": file_digest(source)[7:],
        "side": "LONG",
        "config": {"cutoff_mode": "fixed_per_model", "test_month": "2026-08"},
        "model_files": {"toy": "models/synthetic.pkl"},
        "model_sha256": {"toy": file_digest(model)[7:]},
        "selected_policy": {"pair": "T1+C1", "variant": "synthetic"},
        "initial_cutoffs": {"t_cut": 0.5, "c_risk_cut": 0.5, "history_end": "2026-07-31"},
        "final_result": {
            "pl": 13.0,
            "count": 2,
            "approved": 3,
            "cancelled": 1,
            "t_only_pl": 11.0,
            "cancel_gain": 2.0,
            "mdd": 0.0,
            "worst_day": 5.0,
            "pr": 0.065,
            "pl_per_trade": 6.5,
            "sharpe": math.sqrt(252) * 6.5 / math.sqrt(4.5),
        },
        "limitations": ["Synthetic fixture"],
    }
    write_json(out / "model_metadata.json", metadata)
    write_json(
        out / "verification.json",
        {"source_sha256": file_digest(source)[7:], "pipeline_sha256": file_digest(module)[7:]},
    )
    return root


def ledger_path(root):
    return root / "joint_residual_outputs/optimized_v1/long/selected_august_orders.parquet"


def test_independent_baseline_does_not_load_pickles(hermes, monkeypatch):
    import pickle

    monkeypatch.setattr(pickle, "load", lambda *_: pytest.fail("default reader opened a pickle"))
    monkeypatch.setattr(pickle, "loads", lambda *_: pytest.fail("default reader opened a pickle"))
    adapter = HermesAdapter(hermes, "long")
    before = adapter.hashes()
    stats = adapter.baseline()
    assert stats["entry_pl"] == 13
    assert stats["cancel_gain"] == 2
    assert stats["count"] == 2
    assert adapter.inspect()["readme_pair_matches_metadata"] is False
    assert adapter.hashes() == before


@pytest.mark.parametrize(
    "change", ["duplicate_entry", "wrong_kept", "wrong_cutoff", "nan_risk", "wrong_pl"]
)
def test_incompatible_artifacts_fail_closed(hermes, change):
    path = ledger_path(hermes)
    frame = pd.read_parquet(path)
    if change == "duplicate_entry":
        frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    elif change == "wrong_kept":
        frame.loc[0, "kept"] = False
    elif change == "wrong_cutoff":
        frame.loc[:, "t_cut"] = 0.6
    elif change == "nan_risk":
        frame.loc[1, "c_max_risk"] = float("nan")
    elif change == "wrong_pl":
        frame.loc[0, "policy_pl"] = 10.0
    frame.to_parquet(path, index=False)
    with pytest.raises(IntegrityError):
        HermesAdapter(hermes, "long").baseline()


def test_first_crossing_counted_once(hermes):
    path = ledger_path(hermes).with_name("selected_first_crossings.parquet")
    frame = pd.read_parquet(path)
    pd.concat([frame, frame], ignore_index=True).to_parquet(path, index=False)
    with pytest.raises(IntegrityError, match="first-crossing"):
        HermesAdapter(hermes, "long").baseline()


def test_source_or_model_change_refused(hermes):
    adapter = HermesAdapter(hermes, "long")
    (hermes / "synthetic.parquet").write_bytes(b"changed")
    with pytest.raises(IntegrityError):
        adapter.validate(adapter.task())


def test_model_path_cannot_escape_declared_root(hermes):
    path = ledger_path(hermes).with_name("model_metadata.json")
    meta = json.loads(path.read_text())
    meta["model_files"]["toy"] = "../../../../../../outside.pkl"
    write_json(path, meta)
    with pytest.raises(AdmissionError):
        HermesAdapter(hermes, "long")


def test_hermes_campaign_and_recovery_with_same_local_inputs(hermes, tmp_path):
    adapter = HermesAdapter(hermes, "long")
    before = adapter.hashes()
    store = Store(tmp_path / "evidence")
    engine = CampaignEngine(store)
    campaign = engine.create(adapter.task(), adapter)
    assert engine.run(campaign, max_steps=2) == "PAUSED"
    assert engine.run(campaign) == "CLOSED"
    value = report(store, campaign)
    assert [x["action"] for x in value["experiments"]] == [
        "inspect",
        "baseline",
        "attribution",
        "cost_stress",
    ]
    assert value["formal"]["status"] == "NOT_RUN"
    assert value["scope"] == "existing_dataset_replay_only"
    assert value["experiments"][1]["evaluation"]["acceptance"] == "INCONCLUSIVE"
    assert before == adapter.hashes()


def test_changed_source_cannot_resume_frozen_world(hermes, tmp_path):
    adapter = HermesAdapter(hermes, "long")
    store = Store(tmp_path / "evidence")
    engine = CampaignEngine(store)
    campaign = engine.create(adapter.task(), adapter)
    engine.run(campaign, max_steps=1)
    path = ledger_path(hermes)
    frame = pd.read_parquet(path)
    frame.to_parquet(path, index=False, compression="gzip")
    with pytest.raises(IntegrityError, match="changed"):
        engine.run(campaign)


def test_materializer_refuses_source_writes_and_existing_outputs(hermes, tmp_path):
    with pytest.raises(AdmissionError):
        materialize(hermes, "long", Path("python"), hermes / "new-output")
    with pytest.raises(AdmissionError):
        materialize(hermes, "long", Path("python"), tmp_path)


def test_explicit_derived_ledger_must_match_pinned_metadata(hermes, tmp_path):
    import shutil

    derived = tmp_path / "derived"
    derived.mkdir()
    for name in ["selected_august_orders.parquet", "selected_first_crossings.parquet"]:
        shutil.copyfile(ledger_path(hermes).with_name(name), derived / name)
    provenance = {"source_metadata_sha256": "sha256:wrong", "output_hashes": {}}
    write_json(derived / "provenance.json", provenance)
    with pytest.raises(IntegrityError, match="metadata version"):
        HermesAdapter(hermes, "long", derived).validate(HermesAdapter(hermes, "long").task())
