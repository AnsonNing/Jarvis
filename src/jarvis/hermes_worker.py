"""Run only with an explicitly selected, trusted upstream Hermes environment.

Model pickles and the local module are executable trusted inputs. SHA-256 checks
establish version consistency, not protection against malicious local code.
The default reader never opens those pickles. This opt-in worker only predicts;
it disables LightGBM fit and redirects every loader output to a new private folder.
"""

import argparse
import hashlib
import importlib
import json
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--side", choices=["long", "short"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, target = args.root.resolve(), args.output.resolve()
    if target.exists() or target.is_relative_to(root):
        raise ValueError("materialization must use a new folder outside upstream")
    upstream = root / "joint_residual_outputs" / "optimized_v1" / args.side
    meta_path = upstream / "model_metadata.json"
    meta = json.loads(meta_path.read_text())
    verification = json.loads((upstream / "verification.json").read_text())
    source = (root / Path(meta["source_file"]).name).resolve()
    module = root / "hermes_joint_policy_pipeline.py"
    if checksum(source) != "sha256:" + meta["source_sha256"].removeprefix("sha256:"):
        raise ValueError("source checksum mismatch")
    if checksum(module) != "sha256:" + verification["pipeline_sha256"].removeprefix("sha256:"):
        raise ValueError("upstream source module checksum mismatch")
    paths = {name: (upstream / path).resolve() for name, path in meta["model_files"].items()}
    for name, path in paths.items():
        if not path.is_relative_to(upstream) or checksum(path) != "sha256:" + meta["model_sha256"][
            name
        ].removeprefix("sha256:"):
            raise ValueError("model path/checksum mismatch")

    # This process is a trusted local integration, not a generated-code sandbox.
    import pickle

    import lightgbm
    import numpy as np

    def no_training(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("training is disabled in this inference-only worker")

    lightgbm.LGBMClassifier.fit = no_training
    lightgbm.LGBMRegressor.fit = no_training
    sys.path.insert(0, str(root))
    api = importlib.import_module("hermes_joint_policy_pipeline")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=".materialize-", dir=target.parent) as scratch:
        stage = Path(scratch)

        BaseConfig: Any = api.Config

        class LocalConfig(BaseConfig):
            @property
            def output(self) -> Path:
                return stage / "loader_audit"

        values = dict(meta["config"], root=str(root), use_cache=False)
        for key in ["seeds", "t_quantiles", "cancel_fractions", "variants"]:
            values[key] = tuple(values[key])
        cfg = LocalConfig(**values)
        data = api.load_data(cfg)
        if data.source_sha256 != meta["source_sha256"]:
            raise ValueError("loader source identity mismatch")
        names = meta["selected_policy"]["pair"].split("+")
        with paths[names[0]].open("rb") as stream:
            trading = pickle.load(stream)
        with paths[names[1]].open("rb") as stream:
            cancel = pickle.load(stream)
        orders = data.orders
        current = orders.loc[orders.month.eq(cfg.test_month)].copy()
        events = data.events.loc[data.events.month.eq(cfg.test_month)]
        cuts = meta["initial_cutoffs"]
        score = trading.predict(current[api.T_FEATURES])
        risk = api.risk_score(cancel, events)
        maximum = api.order_max(events, risk, len(orders))
        thresholds = np.full(len(orders), cuts["c_risk_cut"])
        potential, trace = api.first_crossing(events, risk, thresholds, len(orders))
        current["t_score"] = score
        current["t_cut"] = cuts["t_cut"]
        current["c_cut"] = cuts["c_risk_cut"]
        current["c_max_risk"] = maximum[current.row_id]
        current["trading_pass"] = score > cuts["t_cut"]
        current["potential_cancel"] = potential[current.row_id]
        current["cancelled"] = current.trading_pass & current.potential_cancel
        current["kept"] = current.trading_pass & ~current.potential_cancel
        current["policy_pl"] = current.entry_pl.where(current.kept, 0)
        summary = api.tradesummery(current, current.kept)
        expected = meta["final_result"]
        for key in ["count", "pl", "pr", "sharpe", "mdd", "worst_day"]:
            if not np.isclose(summary[key], expected[key], rtol=1e-9, atol=1e-9):
                raise ValueError("frozen-model replay differs from saved result: " + key)
        current = current.merge(
            trace[["row_id", "start_seconds"]].rename(
                columns={"start_seconds": "first_potential_cancel_seconds"}
            ),
            on="row_id",
            how="left",
            validate="one_to_one",
        )
        columns = [
            "row_id",
            "episode_id",
            "txdate",
            "product",
            "placetime",
            "fill_seconds",
            "label_available",
            "entry_pl",
            "entry_pr",
            "has_event",
            "t_score",
            "t_cut",
            "c_max_risk",
            "c_cut",
            "trading_pass",
            "potential_cancel",
            "cancelled",
            "kept",
            "policy_pl",
            "first_potential_cancel_seconds",
        ]
        current[columns].to_parquet(stage / "selected_august_orders.parquet", index=False)
        trace.loc[trace.row_id.isin(current.loc[current.cancelled, "row_id"])].to_parquet(
            stage / "selected_first_crossings.parquet", index=False
        )
        provenance = {
            "schema_version": 1,
            "origin": "human_provided_models_trusted_local_inference",
            "source_metadata_sha256": checksum(meta_path),
            "source_sha256": checksum(source),
            "module_sha256": checksum(module),
            "worker_sha256": checksum(Path(__file__)),
            "model_hashes": {name: checksum(path) for name, path in paths.items()},
            "python": platform.python_version(),
            "training_runs": 0,
            "model_predictions": 2,
            "event_backtests": 0,
            "elapsed_seconds": time.monotonic() - started,
            "output_hashes": {
                name: checksum(stage / name)
                for name in ["selected_august_orders.parquet", "selected_first_crossings.parquet"]
            },
            "scope": "existing_exposed_period_dataset_replay_only",
        }
        (stage / "provenance.json").write_text(json.dumps(provenance, indent=2))
        (stage / "worker_source.py").write_bytes(Path(__file__).read_bytes())
        # Rename the complete directory atomically; no source artifact is replaced.
        stage.rename(target)
    print(json.dumps({"status": "MATERIALIZED", "output": str(target)}))


if __name__ == "__main__":
    main()
