"""Artificial artifact contracts; no private strategy or weights in CI."""

import json

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from jarvis.campaign import CampaignEngine
from jarvis.contracts import IntegrityError
from jarvis.hermes_new import NewBaselineAdapter
from jarvis.identity import file_digest
from jarvis.storage import Store


def dump(path, value):
    path.write_text(json.dumps(value))


@pytest.fixture
def new_baseline(tmp_path):
    root = tmp_path / "upstream"
    out = tmp_path / "artifacts"
    ledger = tmp_path / "derived"
    for d in [root, out, ledger]:
        d.mkdir()
    module = root / "hermes_new_baseline_pipeline.py"
    module.write_text("# Inert fixture, never imported.\n")
    source = root / "canonical.parquet"
    table = pa.Table.from_pydict({f"fs{i}": [0.0] for i in range(91)}).replace_schema_metadata(
        {b"iris_schema": b"cotrain_controls_v1"}
    )
    pq.write_table(table, source)
    raw = root / "raw.csv"
    raw.write_text("fixture\n")
    dump(
        source.with_suffix(".schema.json"),
        {
            "schema": "cotrain_controls_v1",
            "input": str(raw),
            "input_sha256": file_digest(raw)[7:],
            "output_sha256": file_digest(source)[7:],
        },
    )
    records = {}
    for name, count, prefix in [("T0", 81, "t_fs"), ("C0", 91, "fs")]:
        path = out / (name + ".pkl")
        path.write_bytes(b"intentionally not a pickle")
        records[name] = {
            "file": str(path),
            "sha256": file_digest(path)[7:],
            "feature_names": [prefix + str(i) for i in range(count)],
        }
    cuts = {"t_cut": 0.5, "c_risk_cut": 0.5}
    cfg = {
        "side": "LONG",
        "source": str(source),
        "seed": 42,
        "fit_end": "2026-08-18",
        "calibration_start": "2026-08-20",
        "calibration_end": "2026-08-28",
        "test_start": "2026-09-01",
        "label_embargo_days": 1,
        "fit_start": "2026-08-03",
    }
    dump(
        out / "model_metadata.json",
        {
            "side": "LONG",
            "pair": "T0+C0",
            "snapshot_index_offset": 0,
            "fixed_seed": 42,
            "config": cfg,
            "model_files": records,
            "cutoffs": cuts,
            "pipeline_sha256": file_digest(module)[7:],
            "source_sha256": file_digest(source)[7:],
        },
    )
    dump(out / "data_audit.json", {})
    dump(
        out / "past_only_fold_audit.json",
        [{"max_fit_label_time": "2026-08-17", "embargo_boundary": "2026-08-18"}],
    )
    boosters = []
    headers = []
    for name in ["T0", "C0"]:
        p = out / (name + ".txt")
        p.write_text("inert booster")
        boosters.append({"model": name, "booster_file": str(p), "sha256": file_digest(p)[7:]})
        h = out / (name + ".hpp")
        h.write_text("inert fixture")
        headers.append(
            {
                "hpp_file": str(h),
                "hpp_sha256": file_digest(h)[7:],
                "max_score_error": 0.0,
                "decision_mismatches": 0,
            }
        )
    dump(out / "booster_manifest.json", boosters)
    dump(out / "hpp_verification.json", {"status": "PASS", "models": headers})
    frame = pd.DataFrame(
        {
            "row_id": [0, 1, 2],
            "episode_id": ["a", "b", "c"],
            "txdate": ["2026-08-20"] * 3,
            "date": pd.to_datetime(["2026-08-20"] * 3),
            "product": ["TOY"] * 3,
            "placetime": ["x"] * 3,
            "place_seconds": [1.0] * 3,
            "fill_seconds": [5.0] * 3,
            "entry_pl": [5.0, -2.0, -1.0],
            "entry_pr": [0.05, -0.02, -0.01],
            "split": ["calibration"] * 3,
            "has_event": [True, True, False],
            "t_score": [0.9, 0.7, 0.1],
            "t_cut": [0.5] * 3,
            "c_cut": [0.5] * 3,
            "c_max_risk": [0.2, 0.9, float("nan")],
            "trading_pass": [True, True, False],
            "potential_cancel": [False, True, False],
            "cancelled": [False, True, False],
            "kept": [True, False, False],
            "policy_pl": [5.0, 0.0, 0.0],
        }
    )
    frame.to_parquet(ledger / "orders.parquet", index=False)
    events = pd.DataFrame(
        {
            "row_id": [0, 1, 1],
            "episode_id": ["a", "b", "b"],
            "event_seconds": [2.0, 2.0, 3.0],
            "place_seconds": [1.0] * 3,
            "fill_seconds": [5.0] * 3,
            "fs0": [0, 0, 1],
            "fs1": [2000, 2000, 3000],
            "c_risk": [0.2, 0.8, 0.9],
            "order_age_ms": [1000.0, 1000.0, 2000.0],
        }
    )
    events.to_parquet(ledger / "events.parquet", index=False)
    rows = []
    for name, mask in [
        ("Baseline", [True] * 3),
        ("Trading only", frame.trading_pass),
        ("Cancel only replay", ~frame.potential_cancel),
        ("Trading + Cancel replay", frame.kept),
    ]:
        part = frame.loc[mask]
        pl = float(part.entry_pl.sum())
        rows.append(
            {
                "policy": name,
                "count": len(part),
                "pl": pl,
                "mean_pr": float(part.entry_pr.mean()),
                "mdd": min(0.0, pl),
                "zero_entry_days": 0,
            }
        )
    pd.DataFrame(rows).to_csv(out / "calibration_descriptive_replay.csv", index=False)
    dump(
        ledger / "provenance.json",
        {
            "metadata_sha256": file_digest(out / "model_metadata.json"),
            "source_sha256": file_digest(source),
            "feature_only_calibration": cuts,
            "training_runs": 0,
            "output_hashes": {
                n: file_digest(ledger / n) for n in ["orders.parquet", "events.parquet"]
            },
        },
    )
    return root, out, ledger


def test_new_adapter_reconciles_four_policies_without_loading_executable_models(
    new_baseline, monkeypatch
):
    import pickle

    monkeypatch.setattr(pickle, "loads", lambda *_: pytest.fail("reader executed a pickle"))
    root, out, ledger = new_baseline
    adapter = NewBaselineAdapter(root, "long", out, ledger)
    before = adapter.hashes()
    adapter.validate(adapter.task())
    assert adapter.baseline()["entry_pl"] == 5
    assert adapter.baseline()["count"] == 1
    assert adapter.inspect()["ledger_ready"]
    assert adapter.hashes() == before


def test_new_adapter_campaign_roundtrip_and_frozen_world(new_baseline, tmp_path):
    root, out, ledger = new_baseline
    adapter = NewBaselineAdapter(root, "long", out, ledger)
    store = Store(tmp_path / "evidence")
    engine = CampaignEngine(store)
    cid = engine.create(adapter.task(), adapter)
    assert engine.run(cid, max_steps=2) == "PAUSED"
    assert engine.adapter(store.manifest(cid)).adapter_id == adapter.adapter_id
    p = ledger / "events.parquet"
    data = pd.read_parquet(p)
    data.loc[0, "event_seconds"] = 6.0
    data.to_parquet(p, index=False)
    with pytest.raises(IntegrityError):
        engine.run(cid)


def test_prefill_and_single_entry_constraints_are_independent(new_baseline):
    root, out, ledger = new_baseline
    # Duplicate legacy (day,product,place) values are legal when episode identity differs.
    adapter = NewBaselineAdapter(root, "long", out, ledger)
    assert adapter.baseline()["count"] == 1
    p = out / "calibration_descriptive_replay.csv"
    saved = pd.read_csv(p)
    saved.loc[saved.policy.eq("Trading only"), "pl"] += 1
    saved.to_csv(p, index=False)
    with pytest.raises(IntegrityError, match="four-policy"):
        adapter.baseline()


def test_prefill_conditions_first_crossing_and_missing_fields(new_baseline, tmp_path):
    from jarvis.contracts import AdmissionError
    from jarvis.new_research import validate_new_parameters

    root, out, ledger = new_baseline
    adapter = NewBaselineAdapter(root, "long", out, ledger)
    store = Store(tmp_path / "evidence")
    engine = CampaignEngine(store)
    cid = engine.create(adapter.task(), adapter)
    engine.run(cid, max_steps=2)
    state = engine.state(cid, adapter)
    template = next(
        a
        for a in adapter.research_frontier(adapter.task(), state)
        if a.action_id == "cancel_experiment"
    )
    delayed = adapter.execute(
        template.model_copy(update={"arguments": {"min_order_age_ms": 3000.0}})
    )
    assert delayed.metrics["cancelled"] == 0
    assert delayed.metrics["entry_pl"] == 3.0
    rule = {
        "combine": "all",
        "conditions": [{"field": "cancel_score", "operator": "gt", "value": 0.85}],
    }
    crossing = adapter.execute(template.model_copy(update={"arguments": {"cancel_rule": rule}}))
    assert crossing.metrics["cancelled"] == 1
    assert crossing.metrics["first_crossings"] == 1
    assert crossing.metrics["entry_pl"] == 5.0
    with pytest.raises(AdmissionError, match="REQUIRES_CAPABILITY"):
        validate_new_parameters(
            "cancel_experiment",
            {
                "cancel_rule": {
                    "combine": "any",
                    "conditions": [{"field": "spread_bps", "operator": "gt", "value": 1.0}],
                }
            },
        )
    with pytest.raises(AdmissionError, match="exactly"):
        validate_new_parameters(
            "cancel_experiment", {"min_order_age_ms": 3000.0, "cancel_risk_delta": 0.01}
        )


def test_new_turn_is_versioned_and_can_be_admitted(new_baseline, tmp_path):
    from jarvis.contracts import Hypothesis
    from jarvis.new_research import ExperimentDesignV2, ExperimentParametersV2, ResearchTurnV2
    from jarvis.researcher import admit_turn, parse_turn, prompt_context, structured_schema

    root, out, ledger = new_baseline
    adapter = NewBaselineAdapter(root, "long", out, ledger)
    store = Store(tmp_path / "evidence")
    engine = CampaignEngine(store)
    cid = engine.create(adapter.task(), adapter)
    engine.run(cid, max_steps=2)
    state = engine.state(cid, adapter).model_copy(
        update={
            "available_actions": adapter.research_frontier(
                adapter.task(), engine.state(cid, adapter)
            )
        }
    )
    request = prompt_context(adapter.task(), state, [])
    turn = ResearchTurnV2(
        request_id=request["request_id"],
        state_hash=state.identity,
        analysis_summary="Test whether delayed cancellation changes measured entry accounting.",
        evidence_refs=state.visible_evidence_ids,
        hypothesis=Hypothesis(
            hypothesis_id="draft",
            claim="Delay preserves good entries",
            mechanism="Wait before prefill cancellation",
            falsification="Measured difference is negative",
            origin="agent_proposed",
        ),
        experiment=ExperimentDesignV2(
            template_id="cancel_experiment",
            intervention="Delay only",
            comparator="frozen_baseline",
            controlled_variables=("frozen model",),
            parameters=ExperimentParametersV2(min_order_age_ms=3000.0),
            expected_observation="Fewer cancelled episodes",
            success_criterion="Positive measured difference",
        ),
        next_direction="CONTINUE",
        limitations=("Exposed filled-population replay",),
    )
    assert parse_turn(turn.model_dump()) == turn
    _, action = admit_turn(turn, state, request["request_id"])
    assert action.arguments == {"min_order_age_ms": 3000.0}
    schema = structured_schema(state, request["request_id"])
    assert schema["properties"]["kind"]["const"] == "ResearchTurnV2"
    assert schema["$defs"]["CancelCondition"]["properties"]["field"]["enum"] == [
        "cancel_score",
        "order_age_ms",
    ]
