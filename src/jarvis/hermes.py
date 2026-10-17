"""Opt-in local adapter for existing Hermes artifacts.

This module reads declared inputs only. It does not import private strategy
source, load executable model pickles, train, submit backtests or alter inputs.
All numerical claims concern accounting from previously saved decisions.
"""

import json
import math
import re
from pathlib import Path
from typing import Any, Literal

from jarvis.contracts import (
    Acceptance,
    ActionSpec,
    AdmissionError,
    Budget,
    Cost,
    Hypothesis,
    IntegrityError,
    Observation,
    ResearchState,
    ResearchTask,
    Splits,
)
from jarvis.identity import digest, file_digest


class HermesAdapter:
    adapter_id = "hermes-local-readonly:1"

    def __init__(self, root: Path, side: str, ledger_root: Path | None = None):
        if side not in {"long", "short"}:
            raise AdmissionError("side must be long or short")
        self.root = root.resolve()
        self.side = side
        self.output = self.root / "joint_residual_outputs" / "optimized_v1" / side
        self.ledger_root = ledger_root.resolve() if ledger_root else self.output
        self.metadata = self._read_json("model_metadata.json")
        self.source = self._within(self.root / Path(self.metadata["source_file"]).name)
        self.files = [
            self.source,
            self._within(self.root / "README.md"),
            self._within(self.root / "hermes_joint_policy_pipeline.py"),
        ]
        self.files += [
            self._within(self.output / name)
            for name in [
                "model_metadata.json",
                "verification.json",
            ]
        ]
        for name in ["selected_august_orders.parquet", "selected_first_crossings.parquet"]:
            ledger = (self.ledger_root / name).resolve()
            if not ledger.is_file() or ledger.parent != self.ledger_root:
                raise AdmissionError("missing or unsafe explicit ledger input")
            self.files.append(ledger)
        if self.ledger_root != self.output:
            provenance = self.ledger_root / "provenance.json"
            if not provenance.is_file():
                raise AdmissionError("materialized ledger requires provenance.json")
            self.files.append(provenance)
        self.files += [
            self._within(self.output / name) for name in self.metadata["model_files"].values()
        ]
        self._identity: dict[str, str] | None = None

    def _within(self, path: Path) -> Path:
        path = path.resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise AdmissionError("missing Hermes input or path outside the declared local root")
        return path

    def _read_json(self, name: str) -> dict[str, Any]:
        return json.loads(self._within(self.output / name).read_text())

    def hashes(self) -> dict[str, str]:
        return {
            (
                str(p.relative_to(self.root))
                if p.is_relative_to(self.root)
                else "derived/" + p.name
            ): file_digest(p)
            for p in sorted(set(self.files))
        }

    def identity(self) -> dict[str, str]:
        # Recomputed at campaign admission/resume; execute also compares the
        # frozen input identity before opening its declared artifacts.
        self._identity = {
            "adapter_source": file_digest(Path(__file__)),
            "dataset_identity": digest(self.hashes()),
        }
        return self._identity

    def _verify_sources(self) -> None:
        meta = self._read_json("model_metadata.json")
        verification = self._read_json("verification.json")
        if meta != self.metadata:
            raise IntegrityError("Hermes metadata changed during adapter lifetime")
        if (
            self.metadata["side"].lower() != self.side
            or file_digest(self.source) != "sha256:" + meta["source_sha256"].removeprefix("sha256:")
            or verification["source_sha256"] != meta["source_sha256"]
            or file_digest(self.root / "hermes_joint_policy_pipeline.py")
            != "sha256:" + verification["pipeline_sha256"].removeprefix("sha256:")
        ):
            raise IntegrityError("Hermes source/side/pipeline hash differs from saved run")
        for model, name in meta["model_files"].items():
            if file_digest(self._within(self.output / name)) != "sha256:" + meta["model_sha256"][
                model
            ].removeprefix("sha256:"):
                raise IntegrityError("saved model checksum differs; model will not be opened")
        if meta["config"].get("cutoff_mode") != "fixed_per_model":
            raise AdmissionError("adapter requires declared fixed per-model thresholds")
        if self.ledger_root != self.output:
            provenance = json.loads((self.ledger_root / "provenance.json").read_text())
            if provenance["source_metadata_sha256"] != file_digest(
                self.output / "model_metadata.json"
            ):
                raise IntegrityError(
                    "materialized ledger belongs to a different model metadata version"
                )
            for name, expected in provenance["output_hashes"].items():
                if Path(name).name != name or file_digest(self.ledger_root / name) != expected:
                    raise IntegrityError("materialized output checksum mismatch")

    def task(self) -> ResearchTask:
        return ResearchTask(
            task_id=f"hermes-{self.side}-existing-artifacts-v1",
            domain="hermes",
            goal="Verify and diagnose the existing Hermes baseline using current local data",
            hypotheses_allowed=("diagnostic_analysis",),
            primary_metric="entry_pl",
            metric_direction="maximize",
            secondary_metrics=("daily_drawdown", "cancel_gain"),
            acceptance=Acceptance(minimum_primary_improvement=0.01),
            split_policy=Splits(
                development_id="existing-exposed-period",
                selection_id="existing-prior-oof",
                locked_test_id=None,
            ),
            budget=Budget(
                max_actions=8, max_tool_calls=8, max_compute_units=8, max_wall_seconds=600
            ),
            world_ref=f"hermes-{self.side}-local-v1",
            origin="human_provided",
        )

    def validate(self, task: ResearchTask) -> None:
        if (
            task.domain != "hermes"
            or task.primary_metric != "entry_pl"
            or task.split_policy.locked_test_id
        ):
            raise AdmissionError(
                "Hermes adapter supports existing entry_pl replay only; no locked test"
            )
        self._verify_sources()

    def inspect(self) -> dict[str, Any]:
        import pyarrow.parquet as pq

        self._verify_sources()
        parquet = pq.ParquetFile(self.source)
        columns = parquet.schema_arrow.names
        missing = [f"fs{i}" for i in range(91) if f"fs{i}" not in columns]
        if missing:
            raise IntegrityError("source is missing required feature slots")
        pair = self.metadata["selected_policy"]["pair"]
        table = re.search(
            rf"\|\s*{self.side.upper()}\s*\|\s*([^|]+)", (self.root / "README.md").read_text()
        )
        documented = table.group(1).strip(" `") if table else None
        return {
            "side": self.side,
            "source_rows": parquet.metadata.num_rows,
            "source_columns": len(columns),
            "feature_slots": 91,
            "source_sha256": file_digest(self.source),
            "selected_policy": self.metadata["selected_policy"],
            "fixed_thresholds": {
                k: self.metadata["initial_cutoffs"][k] for k in ["t_cut", "c_risk_cut"]
            },
            "documented_pair": documented,
            "readme_pair_matches_metadata": documented == pair,
            "source_and_model_hashes_verified": True,
            "ledger_origin": "explicit_materialization"
            if self.ledger_root != self.output
            else "upstream_saved_ledger",
            "scope": "existing_dataset_replay_only",
            "fresh_locked_test": False,
            "origin": "human_provided_artifacts",
            "capabilities": {
                "inspect": True,
                "baseline_accounting": True,
                "attribution": True,
                "cost_sensitivity": True,
                "train_model": False,
                "event_backtest": False,
                "live_trading": False,
            },
            "limitations": self.metadata.get("limitations", [])
            + [
                "Existing exposed evaluation period is development, not an untouched test",
                "Raw data and model files are fingerprinted; predictions are not regenerated",
                "Entry PL units and fee inclusion depend on the upstream data generator",
            ],
        }

    def _orders(self) -> Any:
        import numpy as np
        import pandas as pd

        frame = pd.read_parquet(self.ledger_root / "selected_august_orders.parquet")
        required = {
            "row_id",
            "episode_id",
            "txdate",
            "product",
            "placetime",
            "entry_pl",
            "entry_pr",
            "t_score",
            "t_cut",
            "c_max_risk",
            "c_cut",
            "has_event",
            "trading_pass",
            "potential_cancel",
            "cancelled",
            "kept",
            "policy_pl",
        }
        if not required <= set(frame.columns):
            raise IntegrityError("incomplete saved order ledger")
        if (
            frame[["row_id", "episode_id", "txdate", "product", "placetime"]].isna().any().any()
            or not frame.row_id.is_unique
            or not frame.episode_id.is_unique
            or frame.duplicated(["txdate", "product", "placetime"]).any()
        ):
            raise IntegrityError("order lineage is not one row per entry")
        for name in ["entry_pl", "entry_pr", "t_score", "t_cut", "c_cut"]:
            if not np.isfinite(frame[name]).all():
                raise IntegrityError(f"nonfinite entry field: {name}")
        for name in ["has_event", "trading_pass", "potential_cancel", "cancelled", "kept"]:
            if frame[name].dtype != bool:
                raise IntegrityError("saved decision columns must be booleans")
        if not np.isfinite(frame.loc[frame.has_event, "c_max_risk"]).all():
            raise IntegrityError("nonfinite risk for an entry with observed snapshots")
        cuts = self.metadata["initial_cutoffs"]
        if not np.allclose(frame.t_cut, cuts["t_cut"], rtol=0, atol=1e-15) or not np.allclose(
            frame.c_cut, cuts["c_risk_cut"], rtol=0, atol=1e-15
        ):
            raise IntegrityError("fixed thresholds differ from the declared baseline")
        trading = frame.t_score > frame.t_cut
        potential = frame.has_event & (frame.c_max_risk > frame.c_cut)
        cancelled = trading & potential
        kept = trading & ~potential
        for expected, name in [
            (trading, "trading_pass"),
            (potential, "potential_cancel"),
            (cancelled, "cancelled"),
            (kept, "kept"),
        ]:
            if not np.array_equal(expected, frame[name]):
                raise IntegrityError(f"saved strict-threshold accounting mismatch: {name}")
        if not np.allclose(frame.policy_pl, frame.entry_pl.where(kept, 0), rtol=1e-12, atol=1e-12):
            raise IntegrityError("policy PL does not reconcile to unique kept entries")
        crossings = pd.read_parquet(self.ledger_root / "selected_first_crossings.parquet")
        if (
            not crossings.row_id.is_unique
            or set(crossings.row_id) != set(frame.loc[cancelled, "row_id"])
            or not (crossings.start_seconds < crossings.fill_seconds).all()
            or not (crossings.start_seconds >= crossings.place_seconds - 0.001).all()
        ):
            raise IntegrityError("saved first-crossing lineage/timing mismatch")
        frame["date"] = pd.to_datetime(frame.txdate, errors="raise")
        if not frame.date.dt.strftime("%Y-%m").eq(self.metadata["config"]["test_month"]).all():
            raise IntegrityError("unexpected period in saved replay ledger")
        if not cuts["history_end"] < frame.date.min().strftime("%Y-%m-%d"):
            raise IntegrityError("calibration overlaps replay period")
        return frame

    def baseline(self) -> dict[str, float]:
        import numpy as np

        frame = self._orders()
        kept = frame.loc[frame.kept]
        daily = (
            kept.groupby("date").entry_pl.sum().reindex(sorted(frame.date.unique()), fill_value=0.0)
        )
        equity = np.r_[0.0, daily.cumsum().to_numpy()]
        stats = {
            "entry_pl": float(kept.entry_pl.sum()),
            "count": float(len(kept)),
            "approved": float(frame.trading_pass.sum()),
            "cancelled": float(frame.cancelled.sum()),
            "t_only_pl": float(frame.loc[frame.trading_pass, "entry_pl"].sum()),
            "cancel_gain": -float(frame.loc[frame.cancelled, "entry_pl"].sum()),
            "mdd": float((equity - np.maximum.accumulate(equity)).min()),
            "worst_day": float(daily.min()),
        }
        if len(kept):
            stats.update(pr=float(kept.entry_pr.mean()), pl_per_trade=float(kept.entry_pl.mean()))
        if daily.std(ddof=1) > 0:
            stats["sharpe"] = float(math.sqrt(252) * daily.mean() / daily.std(ddof=1))
        if not np.isclose(stats["entry_pl"], stats["t_only_pl"] + stats["cancel_gain"], atol=1e-9):
            raise IntegrityError("PL ledger double counts entries")
        expected = self.metadata["final_result"]
        for name, value in stats.items():
            key = "pl" if name == "entry_pl" else name
            if key not in expected or not np.isclose(value, expected[key], rtol=1e-9, atol=1e-9):
                raise IntegrityError(f"independent baseline reproduction mismatch: {key}")
        return stats

    def frontier(self, task: ResearchTask, state: ResearchState) -> tuple[ActionSpec, ...]:
        seen = {o.action_id for o in state.observations}
        if not seen:
            names = ["inspect"]
        elif "baseline" not in seen:
            names = ["baseline", "stop"]
        elif not seen & {"attribution", "cancel_effect"}:
            base = next(o for o in state.observations if o.action_id == "baseline")
            names = (
                ["cancel_effect", "attribution", "stop"]
                if base.metrics["entry_pl"] <= 0
                else ["attribution", "cancel_effect", "stop"]
            )
        elif "cost_stress" not in seen:
            names = ["cost_stress", "stop"]
        else:
            names = ["stop"]
        result = []
        for name in names:
            kind: Literal["INSPECT", "DIAGNOSE", "STOP"] = (
                "STOP" if name == "stop" else "INSPECT" if name == "inspect" else "DIAGNOSE"
            )
            result.append(
                ActionSpec(
                    action_id=name,
                    action_type=kind,
                    tool_id="hermes." + name,
                    split_id=task.split_policy.development_id,
                    hypothesis=Hypothesis(
                        hypothesis_id="artifact-diagnostic-" + name,
                        claim={
                            "baseline": "Saved entry-level accounting is reproducible",
                            "attribution": "Aggregate returns can hide day or product concentration",
                            "cancel_effect": "Cancellation may avoid losses while sacrificing profitable entries",
                            "cost_stress": "Additional entry costs can remove apparent profitability",
                        }.get(name, "Verify input lineage before further research"),
                        mechanism="Compare fixed saved entry decisions with independently computed aggregates",
                        falsification="Checks fail or aggregates do not support the proposed explanation",
                        evidence_refs=state.visible_evidence_ids,
                        origin="human_provided",
                    ),
                    origin="human_provided",
                    estimated_cost=Cost()
                    if name == "stop"
                    else Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=30),
                )
            )
        return tuple(result)

    def research_frontier(self, task: ResearchTask, state: ResearchState) -> tuple[ActionSpec, ...]:
        """The LLM chooses experiment order/parameters after baseline admission."""
        seen = {o.action_id for o in state.observations}
        if not {"inspect", "baseline"} <= seen:
            return tuple(a for a in self.frontier(task, state) if a.action_type != "STOP")
        prototypes = {a.action_id: a for a in self.frontier(task, state)}
        anchor = next(iter(prototypes.values()))
        names = [n for n in ["attribution", "cancel_effect", "cost_stress"] if n not in seen]
        names += ["conditional_analysis", "threshold_experiment", "stop"]
        return tuple(
            anchor.model_copy(
                update={
                    "action_id": name,
                    "tool_id": "hermes." + name,
                    "action_type": "STOP"
                    if name == "stop"
                    else "RUN_EXPERIMENT"
                    if name == "threshold_experiment"
                    else "DIAGNOSE",
                    "estimated_cost": Cost()
                    if name == "stop"
                    else Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=30),
                }
            )
            for name in names
        )

    def _candidate_metrics(self, frame: Any, kept: Any, cancelled: Any) -> dict[str, float]:
        import numpy as np

        part = frame.loc[kept]
        daily = (
            part.groupby("date").entry_pl.sum().reindex(sorted(frame.date.unique()), fill_value=0.0)
        )
        equity = np.r_[0.0, daily.cumsum().to_numpy()]
        baseline = float(frame.loc[frame.kept, "entry_pl"].sum())
        stats = {
            "entry_pl": float(part.entry_pl.sum()),
            "count": float(len(part)),
            "cancelled": float(cancelled.sum()),
            "baseline_entry_pl": baseline,
            "entry_pl_difference": float(part.entry_pl.sum()) - baseline,
            "mdd": float((equity - np.maximum.accumulate(equity)).min()),
            "worst_day": float(daily.min()),
        }
        if len(part):
            stats.update(pr=float(part.entry_pr.mean()), pl_per_trade=float(part.entry_pl.mean()))
        return stats

    def execute(self, action: ActionSpec) -> Observation:
        from jarvis.researcher import validate_parameters

        if action.tool_id != "hermes." + action.action_id:
            raise AdmissionError("Hermes accepts admitted diagnostic IDs only")
        validate_parameters(action.action_id, action.arguments)
        if self._identity and digest(self.hashes()) != self._identity["dataset_identity"]:
            raise IntegrityError("Hermes inputs changed after admission")
        if action.action_id == "inspect":
            inspected = self.inspect()
            return Observation(
                action_id="inspect",
                result_origin="OBSERVED",
                metrics={"source_rows": float(inspected["source_rows"])},
                findings=(json.dumps(inspected, sort_keys=True),),
                limitations=tuple(inspected["limitations"]),
            )
        if action.action_id == "baseline":
            return Observation(
                action_id="baseline",
                result_origin="REPLAYED",
                metrics=self.baseline(),
                findings=("Saved baseline accounting independently reconciled",),
                limitations=tuple(self.inspect()["limitations"]),
            )
        frame = self._orders()
        if action.action_id == "threshold_experiment":
            import numpy as np

            cutoff = float(self.metadata["initial_cutoffs"]["t_cut"]) + action.arguments.get(
                "trading_cutoff_delta", 0.0
            )
            approved = frame.t_score > cutoff
            potential = (
                frame.potential_cancel
                if action.arguments.get("cancel_mode", "baseline") == "baseline"
                else np.zeros(len(frame), dtype=bool)
            )
            cancelled = approved & potential
            kept = approved & ~potential
            stats = self._candidate_metrics(frame, kept, cancelled)
            stats.update(trading_cutoff=cutoff, approved=float(approved.sum()))
            return Observation(
                action_id=action.action_id,
                result_origin="REPLAYED",
                metrics=stats,
                findings=("Controlled frozen-model comparison; candidate is not deployed",),
                limitations=(
                    "Previously exposed development period; repeated search can overfit",
                    "Baseline cancellation assumptions retained; no new fill/acknowledgement simulation",
                ),
            )
        if action.action_id == "conditional_analysis":
            column = "date" if action.arguments["group_by"] == "day" else "product"
            grouped = frame.loc[frame.kept].groupby(column).entry_pl.agg(["count", "sum"])
            groups = [
                {"group": str(index), "count": int(row["count"]), "entry_pl": float(row["sum"])}
                for index, row in grouped.iterrows()
            ]
            return Observation(
                action_id=action.action_id,
                result_origin="REPLAYED",
                findings=(
                    json.dumps(
                        {"group_by": action.arguments["group_by"], "groups": groups}, sort_keys=True
                    ),
                ),
                limitations=(
                    "Conditional association in saved outcomes, not causal or independent validation",
                ),
            )
        if action.action_id == "attribution":
            kept = frame.loc[frame.kept]
            daily = kept.groupby("date").entry_pl.sum()
            products = kept.groupby("product").entry_pl.agg(["count", "sum"])
            finding = {
                "per_product": products.reset_index().to_dict("records"),
                "positive_days": int((daily > 0).sum()),
                "negative_days": int((daily < 0).sum()),
                "best_day_pl": float(daily.max()) if len(daily) else None,
            }
        elif action.action_id == "cancel_effect":
            cancelled = frame.loc[frame.cancelled]
            finding = {
                "cancelled_entries": len(cancelled),
                "avoided_loss_pl": -float(cancelled.loc[cancelled.entry_pl < 0, "entry_pl"].sum()),
                "sacrificed_profit_pl": float(
                    cancelled.loc[cancelled.entry_pl > 0, "entry_pl"].sum()
                ),
                "assumption": "Immediate successful prefill cancellation; saved outcomes only",
            }
        elif action.action_id == "cost_stress":
            kept = frame.loc[frame.kept]
            finding = {
                "unit": "hypothetical additional source PL units per kept entry",
                "scenarios": [
                    {
                        "additional_cost": cost,
                        "adjusted_entry_pl": float(kept.entry_pl.sum() - cost * len(kept)),
                    }
                    for cost in (
                        (action.arguments["additional_cost"],)
                        if "additional_cost" in action.arguments
                        else (0.0, 0.01, 0.05, 0.1)
                    )
                ],
                "fee_semantics": "Actual fee/slippage inclusion is unknown; this is arithmetic sensitivity",
            }
        else:
            raise AdmissionError("unsupported Hermes diagnostic")
        return Observation(
            action_id=action.action_id,
            result_origin="REPLAYED",
            findings=(json.dumps(finding, sort_keys=True),),
            limitations=("Previously exposed data; diagnostic only, no new event backtest",),
        )

    def formal(self, candidate: str) -> None:
        return None

    def local_manifest(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "side": self.side,
            "ledger_root": str(self.ledger_root) if self.ledger_root != self.output else None,
        }
