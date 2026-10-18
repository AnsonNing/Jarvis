"""Explicit read-only adapter for an admitted new-format frozen baseline."""

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jarvis.contracts import (
    ActionSpec,
    AdmissionError,
    Cost,
    IntegrityError,
    Observation,
    ResearchState,
    ResearchTask,
)
from jarvis.hermes import HermesAdapter
from jarvis.identity import digest, file_digest


def normalized_hash(value: str) -> str:
    return "sha256:" + value.removeprefix("sha256:")


class NewBaselineAdapter(HermesAdapter):
    adapter_id = "hermes-new-baseline:1"

    def __init__(
        self,
        root: Path,
        side: str,
        artifacts: Path,
        ledger_root: Path | None = None,
        prior_context: Path | None = None,
    ):
        if side not in {"long", "short"}:
            raise AdmissionError("side must be long or short")
        self.root, self.side, self.output = root.resolve(), side, artifacts.resolve()
        self.ledger_root = ledger_root.resolve() if ledger_root else self.output
        self.prior_context = prior_context.resolve() if prior_context else None
        self.original = json.loads((self.output / "model_metadata.json").read_text())
        self.source = Path(self.original["config"]["source"]).resolve()
        self.files = [
            self.output / "model_metadata.json",
            self.source,
            self.source.with_suffix(".schema.json"),
            self.root / "hermes_new_baseline_pipeline.py",
            self.output / "calibration_descriptive_replay.csv",
            self.output / "data_audit.json",
            self.output / "past_only_fold_audit.json",
            self.output / "hpp_verification.json",
            self.output / "booster_manifest.json",
        ]
        for name in [
            "hermes_joint_policy_pipeline.py",
            "hermes_joint_deployment_hpp.py",
            "hermes_joint_policy_hpp.py",
        ]:
            if (self.root / name).is_file():
                self.files.append(self.root / name)
        sidecar = json.loads(self.source.with_suffix(".schema.json").read_text())
        csv = Path(sidecar["input"]).resolve()
        if not csv.is_file():
            raise AdmissionError("raw source contract evidence is missing")
        self.files.append(csv)
        for record in self.original["model_files"].values():
            self.files.append(Path(record["file"]).resolve())
        for record in json.loads((self.output / "booster_manifest.json").read_text()):
            self.files.append(Path(record["booster_file"]).resolve())
        for record in json.loads((self.output / "hpp_verification.json").read_text())["models"]:
            self.files.append(Path(record["hpp_file"]).resolve())
        if ledger_root:
            self.files.extend(
                self.ledger_root / n
                for n in ["orders.parquet", "events.parquet", "provenance.json"]
            )
        if self.prior_context:
            self.files.append(self.prior_context)
        if any(not p.is_file() for p in self.files):
            raise AdmissionError("new-baseline declared input is missing")
        self.metadata = {
            "selected_policy": {"pair": self.original["pair"], "variant": "new_frozen_baseline"},
            "initial_cutoffs": self.original["cutoffs"],
            "config": {"cutoff_mode": "fixed_per_model"},
            "limitations": self.original.get("limitations", []),
        }
        self._identity = None

    def _verify_sources(self) -> None:
        meta = json.loads((self.output / "model_metadata.json").read_text())
        if (
            meta != self.original
            or meta["side"].lower() != self.side
            or meta["pair"] != "T0+C0"
            or meta["snapshot_index_offset"] != 0
            or meta["fixed_seed"] != meta["config"]["seed"]
        ):
            raise IntegrityError("new-baseline metadata/side/feature contract changed")
        cfg = meta["config"]
        embargo = timedelta(days=int(cfg["label_embargo_days"]))
        if (
            not datetime.fromisoformat(cfg["fit_end"])
            < datetime.fromisoformat(cfg["calibration_start"]) - embargo
            or not datetime.fromisoformat(cfg["calibration_end"])
            < datetime.fromisoformat(cfg["test_start"]) - embargo
        ):
            raise IntegrityError("new-baseline chronological split/embargo invalid")
        for path, expected in [
            (self.source, meta["source_sha256"]),
            (self.root / "hermes_new_baseline_pipeline.py", meta["pipeline_sha256"]),
        ]:
            if file_digest(path) != normalized_hash(expected):
                raise IntegrityError("new-baseline source/pipeline checksum differs")
        sidecar = json.loads(self.source.with_suffix(".schema.json").read_text())
        if (
            sidecar["schema"] != "cotrain_controls_v1"
            or normalized_hash(sidecar["output_sha256"]) != file_digest(self.source)
            or normalized_hash(sidecar["input_sha256"]) != file_digest(Path(sidecar["input"]))
        ):
            raise IntegrityError("canonical/raw feature source checksum differs")
        for key, rec in meta["model_files"].items():
            path = Path(rec["file"]).resolve()
            features = (
                [f"t_fs{i}" for i in range(81)] if key == "T0" else [f"fs{i}" for i in range(91)]
            )
            if (
                path.parent != self.output
                or file_digest(path) != normalized_hash(rec["sha256"])
                or rec["feature_names"] != features
            ):
                raise IntegrityError("frozen model checksum or feature order differs")
        for fold in json.loads((self.output / "past_only_fold_audit.json").read_text()):
            if not datetime.fromisoformat(fold["max_fit_label_time"]) < datetime.fromisoformat(
                fold["embargo_boundary"]
            ):
                raise IntegrityError("past-only model support contains future labels")
        parity = json.loads((self.output / "hpp_verification.json").read_text())
        if parity["status"] != "PASS" or len(parity["models"]) != 2:
            raise IntegrityError("both HPP parity records required")
        for rec in parity["models"]:
            path = Path(rec["hpp_file"]).resolve()
            if (
                not path.is_relative_to(self.output)
                or rec["decision_mismatches"] != 0
                or rec["max_score_error"] > 1e-12
                or file_digest(path) != normalized_hash(rec["hpp_sha256"])
            ):
                raise IntegrityError("HPP parity/checksum is not valid")
        if (self.ledger_root / "provenance.json").is_file():
            prov = json.loads((self.ledger_root / "provenance.json").read_text())
            if (
                prov["metadata_sha256"] != file_digest(self.output / "model_metadata.json")
                or prov["source_sha256"] != file_digest(self.source)
                or prov["feature_only_calibration"] != meta["cutoffs"]
                or prov["training_runs"] != 0
            ):
                raise IntegrityError("derived ledger does not match frozen baseline")
            for name, expected in prov["output_hashes"].items():
                if (
                    name not in {"orders.parquet", "events.parquet"}
                    or file_digest(self.ledger_root / name) != expected
                ):
                    raise IntegrityError("derived ledger checksum mismatch")
        if self._identity and digest(self.hashes()) != self._identity["dataset_identity"]:
            raise IntegrityError("new-baseline inputs changed after admission")

    def identity(self) -> dict[str, str]:
        self._verify_sources()
        self._identity = {
            "adapter_source": file_digest(Path(__file__)),
            "dataset_identity": digest(self.hashes()),
        }
        return self._identity

    def task(self) -> ResearchTask:
        base = super().task()
        return ResearchTask.model_validate(
            {
                **base.model_dump(mode="json"),
                "task_id": f"hermes-new-{self.side}-calibration-v1",
                "world_ref": f"hermes-new-{self.side}-development-v1",
                "goal": "Reproduce and research the admitted new frozen baseline on exposed calibration data; preserve all results",
                "budget": {
                    **base.budget.model_dump(),
                    "max_actions": 12,
                    "max_tool_calls": 12,
                    "max_compute_units": 12,
                },
                "split_policy": {
                    "development_id": "exposed-calibration",
                    "selection_id": "fixed-past-only-fit",
                    "locked_test_id": None,
                },
            }
        )

    def validate(self, task: ResearchTask) -> None:
        if (
            task.domain != "hermes"
            or task.primary_metric != "entry_pl"
            or task.split_policy != self.task().split_policy
        ):
            raise AdmissionError("new baseline accepts exposed calibration replay only")
        self._verify_sources()
        self.baseline()

    def inspect(self) -> dict[str, Any]:
        import pyarrow.parquet as pq

        self._verify_sources()
        schema = pq.read_schema(self.source)
        if (
            not all(f"fs{i}" in schema.names for i in range(91))
            or schema.metadata.get(b"iris_schema") != b"cotrain_controls_v1"
        ):
            raise IntegrityError("normalized source feature/schema contract differs")
        return {
            "format": "cotrain_controls_v1",
            "side": self.side,
            "source_rows": pq.ParquetFile(self.source).metadata.num_rows,
            "feature_slots": 91,
            "selected_policy": self.metadata["selected_policy"],
            "fixed_thresholds": self.original["cutoffs"],
            "model_target_description": self.original.get("target", "not supplied"),
            "risk_units": "Frozen loader's risk output; classifiers use probability, regressors use negative predicted PR. Do not assume probability bounds.",
            "split_dates": {
                k: self.original["config"][k]
                for k in [
                    "fit_start",
                    "fit_end",
                    "calibration_start",
                    "calibration_end",
                    "test_start",
                ]
            },
            "ledger_ready": (self.ledger_root / "provenance.json").is_file(),
            "fresh_locked_test": False,
            "capabilities": {
                "frozen_threshold_comparison": True,
                "frozen_cancel_comparison": True,
                "prefill_age_replay": True,
                "cancel_rule_fields": ["cancel_score", "order_age_ms"],
                "price_path_exit_backtest": False,
                "cancel_ack_replay": False,
                "train_model": False,
            },
            "limitations": self.original.get("limitations", [])
            + [
                "Only exposed calibration is admitted for adaptive research; original September protocol is preserved",
                "All records are from the filled baseline population; missing unfilled/cancelled orders cannot be inferred",
                "No postfill quote path, partial fills or cancellation acknowledgement is available",
                "Episode identifiers attach outcome labels only; they are not policy features",
            ],
        }

    def _orders(self) -> Any:
        import numpy as np
        import pandas as pd

        self._verify_sources()
        if not (self.ledger_root / "provenance.json").is_file():
            raise AdmissionError("materialize and admit the new frozen baseline first")
        frame = pd.read_parquet(self.ledger_root / "orders.parquet")
        required = {
            "row_id",
            "episode_id",
            "date",
            "txdate",
            "product",
            "place_seconds",
            "fill_seconds",
            "entry_pl",
            "entry_pr",
            "t_score",
            "t_cut",
            "c_cut",
            "c_max_risk",
            "has_event",
            "potential_cancel",
            "trading_pass",
            "cancelled",
            "kept",
            "policy_pl",
            "split",
        }
        if (
            not required <= set(frame.columns)
            or not frame.row_id.is_unique
            or not frame.episode_id.is_unique
            or frame.empty
            or not frame.split.eq("calibration").all()
        ):
            raise IntegrityError("invalid new-baseline one-entry calibration ledger")
        for name in [
            "entry_pl",
            "entry_pr",
            "t_score",
            "t_cut",
            "c_cut",
            "place_seconds",
            "fill_seconds",
        ]:
            if not np.isfinite(frame[name]).all():
                raise IntegrityError("nonfinite new-baseline entry field")
        cfg = self.original["config"]
        if (
            not pd.to_datetime(frame.date)
            .between(cfg["calibration_start"], cfg["calibration_end"])
            .all()
        ):
            raise IntegrityError("ledger includes an unadmitted date")
        for name in ["has_event", "potential_cancel", "trading_pass", "cancelled", "kept"]:
            if frame[name].dtype != bool:
                raise IntegrityError("decision fields must be boolean")
        cuts = self.original["cutoffs"]
        if not np.allclose(frame.t_cut, cuts["t_cut"], rtol=0, atol=1e-15) or not np.allclose(
            frame.c_cut, cuts["c_risk_cut"], rtol=0, atol=1e-15
        ):
            raise IntegrityError("ledger thresholds differ from frozen calibration")
        trading = frame.t_score > frame.t_cut
        potential = frame.has_event & (frame.c_max_risk > frame.c_cut)
        for key, value in {
            "trading_pass": trading,
            "potential_cancel": potential,
            "cancelled": trading & potential,
            "kept": trading & ~potential,
        }.items():
            if not np.array_equal(frame[key], value):
                raise IntegrityError("new-baseline decision mismatch: " + key)
        if not np.allclose(frame.policy_pl, frame.entry_pl.where(frame.kept, 0), rtol=0, atol=1e-9):
            raise IntegrityError("entry accounting double counts outcomes")
        return frame

    def _events(self, frame: Any) -> Any:
        import numpy as np
        import pandas as pd

        events = pd.read_parquet(self.ledger_root / "events.parquet")
        required = {
            "episode_id",
            "row_id",
            "event_seconds",
            "place_seconds",
            "fill_seconds",
            "fs0",
            "fs1",
            "c_risk",
            "order_age_ms",
        }
        if (
            not required <= set(events.columns)
            or events.duplicated(["episode_id", "fs0"]).any()
            or not np.isfinite(events[list(required - {"episode_id"})]).all().all()
        ):
            raise IntegrityError("invalid snapshot ledger")
        clock = events.fs1
        seconds = (
            (clock // 10000000) * 3600 + ((clock // 100000) % 100) * 60 + (clock % 100000) / 1000
        )
        if (
            not np.array_equal(events.fs0, events.fs0.astype(int))
            or (events.fs0 < 0).any()
            or not np.array_equal(clock, clock.astype(int))
            or (clock < 0).any()
            or (clock // 10000000 >= 24).any()
            or ((clock // 100000) % 100 >= 60).any()
            or ((clock // 1000) % 100 >= 60).any()
            or not np.allclose(seconds, events.event_seconds, rtol=0, atol=1e-8)
        ):
            raise IntegrityError("invalid snapshot index or clock")
        joined = events.merge(
            frame[["episode_id", "row_id", "place_seconds", "fill_seconds"]],
            on="episode_id",
            how="left",
            validate="many_to_one",
            suffixes=("", "_entry"),
        )
        if joined.row_id_entry.isna().any() or any(
            not np.array_equal(joined[k], joined[k + "_entry"])
            for k in ["row_id", "place_seconds", "fill_seconds"]
        ):
            raise IntegrityError("snapshot/entry lineage differs")
        if not (
            (events.event_seconds >= events.place_seconds - 0.001)
            & (events.event_seconds < events.fill_seconds)
        ).all() or not np.allclose(
            events.order_age_ms,
            (events.event_seconds - events.place_seconds) * 1000,
            rtol=0,
            atol=1e-8,
        ):
            raise IntegrityError("snapshot is not causally prefill")
        maxima = events.groupby("episode_id").c_risk.max()
        expected = frame.episode_id.map(maxima)
        if not np.array_equal(frame.has_event, expected.notna()) or not np.allclose(
            frame.c_max_risk, expected, equal_nan=True
        ):
            raise IntegrityError("snapshot maximum differs from entry ledger")
        return events.sort_values(["row_id", "event_seconds", "fs0"], kind="stable")

    def baseline(self) -> dict[str, float]:
        import numpy as np
        import pandas as pd

        frame = self._orders()
        self._events(frame)
        saved = pd.read_csv(self.output / "calibration_descriptive_replay.csv")
        dates = sorted(frame.date.unique())
        for name, mask in [
            ("Baseline", np.ones(len(frame), bool)),
            ("Trading only", frame.trading_pass),
            ("Cancel only replay", ~frame.potential_cancel),
            ("Trading + Cancel replay", frame.kept),
        ]:
            part = frame.loc[mask]
            daily = part.groupby("date").entry_pl.sum().reindex(dates, fill_value=0.0)
            equity = np.r_[0.0, daily.cumsum().to_numpy()]
            actual = {
                "count": len(part),
                "pl": float(part.entry_pl.sum()),
                "mean_pr": float(part.entry_pr.mean()) if len(part) else None,
                "mdd": float((equity - np.maximum.accumulate(equity)).min()),
                "zero_entry_days": int(
                    (part.groupby("date").size().reindex(dates, fill_value=0) == 0).sum()
                ),
            }
            expected = saved.loc[saved.policy.eq(name)]
            if len(expected) != 1 or any(
                value is not None
                and not np.isclose(value, expected.iloc[0][key], rtol=1e-9, atol=1e-9)
                for key, value in actual.items()
            ):
                raise IntegrityError("independent four-policy baseline mismatch: " + name)
        stats = self._candidate_metrics(frame, frame.kept, frame.cancelled)
        stats.update(
            approved=float(frame.trading_pass.sum()),
            t_only_pl=float(frame.loc[frame.trading_pass, "entry_pl"].sum()),
            cancel_gain=-float(frame.loc[frame.cancelled, "entry_pl"].sum()),
        )
        if not np.isclose(
            stats["entry_pl"], stats["t_only_pl"] + stats["cancel_gain"], rtol=0, atol=1e-9
        ):
            raise IntegrityError("single entry PL identity failed")
        return stats

    def local_manifest(self) -> dict[str, Any]:
        return {
            "format": "cotrain_controls_v1",
            "root": str(self.root),
            "side": self.side,
            "artifacts": str(self.output),
            "ledger_root": str(self.ledger_root),
            "prior_context": str(self.prior_context) if self.prior_context else None,
        }

    def frontier(self, task: ResearchTask, state: ResearchState) -> tuple[ActionSpec, ...]:
        return tuple(
            a.model_copy(update={"tool_id": "hermes_new." + a.action_id})
            for a in super().frontier(task, state)
        )

    def research_frontier(self, task: ResearchTask, state: ResearchState) -> tuple[ActionSpec, ...]:
        seen = {o.action_id for o in state.observations}
        if not {"inspect", "baseline"} <= seen:
            return tuple(a for a in self.frontier(task, state) if a.action_type != "STOP")
        anchor = self.frontier(task, state)[0]
        if self.prior_context and "prior_round_review" not in seen:
            names = ["prior_round_review"]
        else:
            names = [
                n
                for n in ["attribution", "cancel_effect", "cost_stress", "backend_requirements"]
                if n not in seen
            ]
            names += [
                "conditional_analysis",
                "threshold_experiment",
                "cancel_experiment",
                "joint_experiment",
                "stop",
            ]
        return tuple(
            anchor.model_copy(
                update={
                    "action_id": name,
                    "tool_id": "hermes_new." + name,
                    "action_type": "STOP"
                    if name == "stop"
                    else "RUN_EXPERIMENT"
                    if name.endswith("experiment")
                    else "DIAGNOSE",
                    "estimated_cost": Cost()
                    if name == "stop"
                    else Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=30),
                }
            )
            for name in names
        )

    def _compare(self, arguments: dict[str, Any]) -> tuple[dict[str, float], dict[str, Any]]:
        import numpy as np

        from jarvis.search_space import CancelRule

        frame = self._orders()
        events = self._events(frame)
        cuts = self.original["cutoffs"]
        trading_cut = float(cuts["t_cut"]) + arguments.get("trading_cutoff_delta", 0.0)
        cancel_cut = float(cuts["c_risk_cut"]) + arguments.get("cancel_risk_delta", 0.0)
        approved = frame.t_score > trading_cut
        crossing = events.c_risk > cancel_cut
        if "min_order_age_ms" in arguments:
            crossing &= events.order_age_ms >= arguments["min_order_age_ms"]
        if arguments.get("cancel_mode") == "disabled":
            crossing &= False
        if "cancel_rule" in arguments:
            rule = CancelRule.model_validate(arguments["cancel_rule"])
            values = []
            for condition in rule.conditions:
                series = events["c_risk" if condition.field == "cancel_score" else "order_age_ms"]
                values.append(getattr(series, condition.operator)(condition.value))
            crossing = (
                np.logical_and.reduce(values)
                if rule.combine == "all"
                else np.logical_or.reduce(values)
            )
        first = events.loc[crossing].drop_duplicates("episode_id", keep="first")
        potential = frame.episode_id.isin(first.episode_id)
        cancelled = approved & potential
        kept = approved & ~potential
        metrics = self._candidate_metrics(frame, kept, cancelled)
        metrics.update(
            approved=float(approved.sum()),
            trading_cutoff=trading_cut,
            cancel_cutoff=cancel_cut,
            first_crossings=float(len(first)),
            avoided_loss_pl=-float(frame.loc[cancelled & frame.entry_pl.lt(0), "entry_pl"].sum()),
            sacrificed_profit_pl=float(
                frame.loc[cancelled & frame.entry_pl.gt(0), "entry_pl"].sum()
            ),
        )
        daily = (
            frame.assign(candidate_pl=frame.entry_pl.where(kept, 0.0))
            .groupby("date")
            .agg(candidate_pl=("candidate_pl", "sum"), baseline_pl=("policy_pl", "sum"))
        )
        finding = {
            "intervention": arguments,
            "baseline_population": "filled calibration episodes only",
            "daily_deltas": [
                {"date": str(k), "entry_pl_difference": float(r.candidate_pl - r.baseline_pl)}
                for k, r in daily.iterrows()
            ],
            "first_crossing_count": len(first),
            "number_of_affected_entries": int((kept != frame.kept).sum()),
            "semantics": "One contribution per episode. First matching prefill snapshot only. Immediate cancellation success assumed.",
        }
        return metrics, finding

    def execute(self, action: ActionSpec) -> Observation:
        from jarvis.new_research import validate_new_parameters

        if action.tool_id != "hermes_new." + action.action_id:
            raise AdmissionError("unadmitted new-baseline tool")
        validate_new_parameters(action.action_id, action.arguments)
        self._verify_sources()
        if action.action_id in {"threshold_experiment", "cancel_experiment", "joint_experiment"}:
            metrics, finding = self._compare(action.arguments)
            return Observation(
                action_id=action.action_id,
                result_origin="REPLAYED",
                metrics=metrics,
                findings=(json.dumps(finding, sort_keys=True),),
                limitations=tuple(self.inspect()["limitations"])
                + ("Adaptive comparisons on exposed calibration; no independent strategy verdict",),
            )
        if action.action_id == "prior_round_review":
            if self.prior_context is None:
                raise AdmissionError("prior-round evidence is not configured")
            value = json.loads(self.prior_context.read_text())
            if value.get("kind") != "HermesPriorRound" or value[
                "baseline_metadata_sha256"
            ] != file_digest(self.output / "model_metadata.json"):
                raise IntegrityError("prior context is from a different frozen model")
            return Observation(
                action_id=action.action_id,
                result_origin="OBSERVED",
                findings=(json.dumps(value, sort_keys=True),),
                limitations=(
                    "Previously exposed development results, not fresh validation; interpretations remain advisory",
                ),
            )
        if action.action_id == "backend_requirements":
            return Observation(
                action_id=action.action_id,
                result_origin="OBSERVED",
                findings=(json.dumps(self.backend_requirements(), sort_keys=True),),
                limitations=(
                    "Capability evidence, not an executed price-path or acknowledgement backtest",
                ),
            )
        return super().execute(action.model_copy(update={"tool_id": "hermes." + action.action_id}))

    def backend_requirements(self) -> dict[str, Any]:
        import pyarrow.parquet as pq

        columns = set(pq.read_schema(self.source).names)
        return {
            "kind": "BacktestCapabilityAudit",
            "source_columns": sorted(columns - {f"fs{i}" for i in range(91)}),
            "available": {
                "entry_outcome": True,
                "entry_fill_time": True,
                "episode_lineage": True,
                "prefill_risk_snapshots": True,
                "prefill_order_age": True,
            },
            "take_profit_stop_loss": {
                "status": "REQUIRES_CAPABILITY",
                "missing": [
                    "postfill_bid_ask_price_path",
                    "explicit_exit_trigger_and_fill_semantics",
                    "position_quantity_and_fee_units",
                    "gap_slippage_and_end_of_path_policy",
                ],
            },
            "execution_cancel": {
                "status": "REQUIRES_CAPABILITY",
                "missing": [
                    "unfilled_and_cancelled_baseline_orders",
                    "cancel_request_and_ack_times",
                    "fill_cancel_race_and_partial_fills",
                    "reject_and_expiry_events",
                ],
            },
            "offline_prefill_rules": {
                "status": "AVAILABLE_WITH_ASSUMPTIONS",
                "fields": ["cancel_score", "order_age_ms"],
                "assumption": "Immediate successful cancellation conditional on historically filled orders",
            },
            "source_exit_fields_are_not_a_path": sorted(
                columns & {"exitprice", "starttime", "endtime", "buyp", "sellp", "pl", "pr"}
            ),
            "external_submission": "NOT_SUBMITTED",
        }


def materialize_new(
    root: Path, side: str, artifacts: Path, python: Path, output: Path
) -> dict[str, Any]:
    adapter = NewBaselineAdapter(root, side, artifacts)
    adapter._verify_sources()
    output = output.resolve()
    if (
        output.exists()
        or output.is_relative_to(root.resolve())
        or output.is_relative_to(artifacts.resolve())
    ):
        raise AdmissionError("choose a new output outside protected source and artifacts")
    before = adapter.hashes()
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"}}
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="2")
    result = subprocess.run(
        [
            str(python.absolute()),
            "-B",
            str(Path(__file__).with_name("new_baseline_worker.py")),
            "--root",
            str(root.resolve()),
            "--artifacts",
            str(artifacts.resolve()),
            "--side",
            side,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if adapter.hashes() != before:
        raise IntegrityError("protected upstream inputs changed during inference")
    if result.returncode:
        raise AdmissionError("new-baseline inference failed: " + result.stderr[-3000:])
    derived = NewBaselineAdapter(root, side, artifacts, output)
    derived.validate(derived.task())
    return {
        "status": "MATERIALIZED_AND_VERIFIED",
        "scope": "exposed_calibration_development_replay",
        "metrics": derived.baseline(),
        "upstream_inputs_unchanged": True,
        "training_runs": 0,
        "external_backtests": 0,
        "output": str(output),
    }
