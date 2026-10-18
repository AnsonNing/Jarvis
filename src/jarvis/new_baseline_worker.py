"""Opt-in inference of approved local artifacts; no fitting or upstream writes.

The supplied loader and checksummed pickles are trusted executable inputs. This
process is not an adversarial sandbox and never runs generated research code.
"""

import argparse
import hashlib
import importlib
import json
import platform
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any


def checksum(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--side", choices=["long", "short"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, artifacts, target = args.root.resolve(), args.artifacts.resolve(), args.output.resolve()
    if target.exists() or target.is_relative_to(root) or target.is_relative_to(artifacts):
        raise ValueError("choose a new private output outside protected inputs")
    meta = json.loads((artifacts / "model_metadata.json").read_text())
    module = root / "hermes_new_baseline_pipeline.py"
    source = Path(meta["config"]["source"]).resolve()
    for path, expected in [(module, meta["pipeline_sha256"]), (source, meta["source_sha256"])]:
        if checksum(path) != "sha256:" + expected.removeprefix("sha256:"):
            raise ValueError("frozen source checksum mismatch")
    for rec in meta["model_files"].values():
        p = Path(rec["file"]).resolve()
        if p.parent != artifacts or checksum(p) != "sha256:" + rec["sha256"].removeprefix(
            "sha256:"
        ):
            raise ValueError("model path/checksum mismatch")

    import pickle

    import lightgbm as lgb
    import numpy as np
    import pandas as pd

    def no_training(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("training is disabled in the new-baseline worker")

    lgb.LGBMClassifier.fit = no_training
    lgb.LGBMRegressor.fit = no_training
    lgb.train = no_training
    sys.path.insert(0, str(root))
    api = importlib.import_module("hermes_new_baseline_pipeline")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".new-baseline-", dir=target.parent) as directory:
        stage = Path(directory)
        cfg = api.NewBaselineConfig(**{**meta["config"], "output": str(stage / "loader_audit")})
        orders, events, audit = api.load_baseline(cfg)
        models = {
            key: pickle.loads(Path(rec["file"]).read_bytes())
            for key, rec in meta["model_files"].items()
        }
        for key, model in models.items():
            if model.booster_.feature_name() != meta["model_files"][key]["feature_names"]:
                raise ValueError("frozen feature order mismatch")
        tm, cm = models["T0"], models["C0"]
        # Recompute the declared feature-only calibration, never choose a new cutoff.
        cuts = api.calibrate(cfg, orders, events, tm, cm)
        if cuts != meta["cutoffs"]:
            raise ValueError("frozen feature-only calibration differs")
        tscore = tm.predict(orders[api.T_FEATURES], num_threads=2)
        crisk = (
            cm.predict_proba(events[api.C_FEATURES], num_threads=2)[:, 1]
            if isinstance(cm, lgb.LGBMClassifier)
            else -cm.predict(events[api.C_FEATURES], num_threads=2)
        )
        parity = []
        portable = json.loads((artifacts / "booster_manifest.json").read_text())
        for rec in portable:
            p = Path(rec["booster_file"]).resolve()
            if p.parent != artifacts or checksum(p) != "sha256:" + rec["sha256"].removeprefix(
                "sha256:"
            ):
                raise ValueError("portable booster checksum mismatch")
            booster = lgb.Booster(model_file=str(p))
            key = rec["model"]
            predicted = booster.predict(
                orders[api.T_FEATURES] if key == "T0" else events[api.C_FEATURES], num_threads=2
            )
            if key == "C0" and not isinstance(cm, lgb.LGBMClassifier):
                predicted = -predicted
            expected = tscore if key == "T0" else crisk
            error = float(np.max(np.abs(expected - predicted))) if len(expected) else 0.0
            if error > 1e-12:
                raise ValueError("portable/frozen model prediction mismatch")
            parity.append({"model": key, "rows": len(expected), "max_score_error": error})
        if {r["model"] for r in parity} != {"T0", "C0"}:
            raise ValueError("both portable models are required")
        orders = orders.copy()
        events = events.copy()
        orders["t_score"] = tscore
        events["c_risk"] = crisk
        events["order_age_ms"] = (events.event_seconds - events.place_seconds) * 1000
        maximum = events.groupby("episode_id").c_risk.max()
        orders["c_max_risk"] = orders.episode_id.map(maximum)
        orders["t_cut"] = cuts["t_cut"]
        orders["c_cut"] = cuts["c_risk_cut"]
        orders["trading_pass"] = orders.t_score > orders.t_cut
        orders["potential_cancel"] = orders.has_event & (orders.c_max_risk > orders.c_cut)
        orders["cancelled"] = orders.trading_pass & orders.potential_cancel
        orders["kept"] = orders.trading_pass & ~orders.potential_cancel
        orders["policy_pl"] = orders.entry_pl.where(orders.kept, 0.0)
        cal = orders.loc[orders.split.eq("calibration")].copy()
        ev = events.loc[events.split.eq("calibration")].copy()
        saved = pd.read_csv(artifacts / "calibration_descriptive_replay.csv")
        summaries = []
        policies = {
            "Baseline": np.ones(len(cal), bool),
            "Trading only": cal.trading_pass,
            "Cancel only replay": ~cal.potential_cancel,
            "Trading + Cancel replay": cal.kept,
        }
        for name, mask in policies.items():
            measured = api.entry_metrics(cal, mask)
            expected = saved.loc[saved.policy.eq(name)].iloc[0]
            for key, value in measured.items():
                if value is not None and not np.isclose(value, expected[key], rtol=1e-9, atol=1e-9):
                    raise ValueError("saved baseline mismatch: " + name + "/" + key)
            summaries.append({"policy": name, **measured})
        order_columns = [
            "row_id",
            "episode_id",
            "txdate",
            "date",
            "product",
            "placetime",
            "place_seconds",
            "fill_seconds",
            "entry_pl",
            "entry_pr",
            "split",
            "has_event",
            "t_score",
            "t_cut",
            "c_cut",
            "c_max_risk",
            "trading_pass",
            "potential_cancel",
            "cancelled",
            "kept",
            "policy_pl",
        ]
        event_columns = [
            "row_id",
            "episode_id",
            "event_seconds",
            "place_seconds",
            "fill_seconds",
            "fs0",
            "fs1",
            "c_risk",
            "order_age_ms",
        ]
        cal[order_columns].to_parquet(stage / "orders.parquet", index=False)
        ev[event_columns].to_parquet(stage / "events.parquet", index=False)
        provenance = {
            "kind": "NewBaselineMaterialization",
            "format": "cotrain_controls_v1",
            "side": args.side,
            "metadata_sha256": checksum(artifacts / "model_metadata.json"),
            "source_sha256": checksum(source),
            "pipeline_sha256": checksum(module),
            "worker_sha256": checksum(Path(__file__)),
            "original_config": meta["config"],
            "loader_config": asdict(cfg),
            "feature_only_calibration": cuts,
            "portable_model_parity": parity,
            "calibration_policies": summaries,
            "data_audit": audit,
            "environment": {
                "python": platform.python_version(),
                "lightgbm": lgb.__version__,
                "pandas": pd.__version__,
            },
            "training_runs": 0,
            "external_backtests": 0,
            "scope": "exposed_calibration_development_replay",
            "output_hashes": {n: checksum(stage / n) for n in ["orders.parquet", "events.parquet"]},
        }
        (stage / "provenance.json").write_text(
            json.dumps(api.json_safe(provenance), indent=2, allow_nan=False) + "\n"
        )
        stage.rename(target)


if __name__ == "__main__":
    main()
