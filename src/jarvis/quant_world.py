"""Synthetic Hermes-like world for end-to-end autonomous research tests.

Artificial orders carry decision-time features, an observed adverse price move and a
would-be outcome. A heuristic cancels orders whose adverse move exceeds a bp
threshold; a logistic cancel model trained on the surviving population cancels the rest
it scores as likely losers. Changing the bp shifts the population the model sees, so
retraining can matter, and a regime-aware rule can beat any single bp. All strategy,
model and pipeline code runs inside the OS workbench. Results are SIMULATED_FIXTURE.
"""

import csv
import json
import math
import random
import shutil
from pathlib import Path
from typing import Any

from jarvis.autonomy_contracts import (
    AcceptanceProfile,
    AutonomyContract,
    Candidate,
    Grants,
    ImplementTarget,
    MetricSpec,
    ModelArtifact,
    Objective,
    RiskLimit,
    SettingSpec,
    SourceScope,
)
from jarvis.contracts import AdmissionError
from jarvis.identity import digest, file_digest
from jarvis.research_executors import ResolvedCandidate
from jarvis.storage import Store
from jarvis.workbench import AnalysisEnvironment, Workbench, WorkbenchLimits

FAMILIES: dict[str, dict[str, float]] = {
    # volatile-day share, calm/volatile edge, loser adverse-move coupling, volatile noise multiplier
    "balanced": {"volatile": 0.4, "edge": 1.0, "edge_vol": 4.0, "coupling": 2.0, "vol": 2.5},
    "volatile": {"volatile": 0.6, "edge": 0.8, "edge_vol": 5.0, "coupling": 1.8, "vol": 2.7},
    "calm": {"volatile": 0.25, "edge": 1.2, "edge_vol": 3.0, "coupling": 2.2, "vol": 2.2},
}

CANCEL_RULE = '''"""Heuristic cancel rule. `order` holds decision-time fields only."""


def should_cancel(order, settings):
    """Cancel when the observed adverse move exceeds the heuristic bp threshold."""
    return order["adverse_move_bp"] > settings["heuristic_cancel_bps"]
'''

FEATURES = '''"""Training labels and model features for the cancel model."""


def make_label(row):
    """1 = the order would lose money if kept (the cancel model's target)."""
    return 1 if float(row["outcome_pl_bp"]) < 0 else 0


def features(order):
    """Market-signal features at decision time; outcome fields are never available here."""
    return [float(order["f1"]), float(order["f2"]), float(order["f1"]) * float(order["f2"])]
'''

BACKTEST_RUNNER = """import csv, json, math, sys, importlib.util
from pathlib import Path

cfg = json.loads(Path("config.json").read_text())

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

rule = load("cancel_rule", cfg["cancel_rule"])
feat = load("features", cfg["features"])
model = json.loads(Path(cfg["model"]).read_text())
settings = cfg["settings"]
cutoff = model["cutoff"] + float(settings.get("cancel_cutoff_delta", 0.0))
days = set(cfg["days"])
DECISION = ("order_id", "day", "regime", "vol", "f1", "f2", "adverse_move_bp")
rows, daily = [], {}
with open(cfg["orders"], newline="") as f:
    for raw in csv.DictReader(f):
        if int(raw["day"]) not in days:
            continue
        order = {k: (raw[k] if k in ("order_id", "regime") else float(raw[k])) for k in DECISION}
        order["day"] = int(raw["day"])
        heuristic = bool(rule.should_cancel(dict(order), dict(settings)))
        x = feat.features(dict(order))
        z = model["bias"] + sum(w * v for w, v in zip(model["weights"], x))
        score = 1 / (1 + math.exp(-max(-30, min(30, z))))
        model_cancel = (not heuristic) and score > cutoff and not settings.get("bypass_cancel_model", False)
        filled = raw["filled"] == "1"
        outcome = float(raw["outcome_pl_bp"])
        executed = filled and not heuristic and not model_cancel
        pl = outcome if executed else 0.0
        daily[order["day"]] = daily.get(order["day"], 0.0) + pl
        rows.append({**{k: raw[k] for k in DECISION}, "filled": int(filled), "heuristic_cancel": int(heuristic),
                     "model_score": round(score, 6), "model_cancel": int(model_cancel), "executed": int(executed),
                     "outcome_pl_bp": outcome, "pl_bp": pl})
with open("trades.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader(); w.writerows(rows)
executed = [r for r in rows if r["executed"]]
cum, peak, mdd = 0.0, 0.0, 0.0
for d in sorted(daily):
    cum += daily[d]; peak = max(peak, cum); mdd = max(mdd, peak - cum)
with open("daily.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["day", "pl_bp"]); w.writerows(sorted(daily.items()))
count = len(executed)
metrics = {
    "pl": round(sum(r["pl_bp"] for r in executed), 6),
    "mean_pr": round(sum(r["pl_bp"] for r in executed) / count, 6) if count else 0.0,
    "count": count,
    "mdd": round(mdd, 6),
    "win_rate": round(sum(r["pl_bp"] > 0 for r in executed) / count, 6) if count else 0.0,
    "heuristic_cancels": sum(r["heuristic_cancel"] for r in rows),
    "model_cancels": sum(r["model_cancel"] for r in rows),
}
Path("metrics.json").write_text(json.dumps({"metrics": metrics, "daily": {str(k): v for k, v in daily.items()}}))
"""

TRAIN_RUNNER = """import csv, json, math, random, importlib.util
from pathlib import Path

cfg = json.loads(Path("config.json").read_text())
spec = importlib.util.spec_from_file_location("features", cfg["features"])
feat = importlib.util.module_from_spec(spec); spec.loader.exec_module(feat)
rows = []
with open(cfg["data"], newline="") as f:
    for raw in csv.DictReader(f):
        if raw.get("heuristic_cancel", "0") == "1" or raw.get("filled", "1") != "1":
            continue
        rows.append(raw)
if len(rows) < 50:
    raise SystemExit("not enough surviving filled rows to train")
X = [feat.features(r) for r in rows]
y = [int(feat.make_label(r)) for r in rows]
pl = [float(r["outcome_pl_bp"]) for r in rows]
days = [int(r["day"]) for r in rows]
l2, epochs, lr = float(cfg["l2"]), int(cfg["epochs"]), float(cfg["learning_rate"])

def fit(idx):
    n = len(X[0]); w = [0.0] * n; b = 0.0
    for _ in range(epochs):
        gw = [0.0] * n; gb = 0.0
        for i in idx:
            z = b + sum(a * c for a, c in zip(w, X[i])); p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            e = p - y[i]; gb += e
            for k in range(n): gw[k] += e * X[i][k]
        m = max(1, len(idx))
        w = [w[k] - lr * (gw[k] / m + l2 * w[k]) for k in range(n)]; b -= lr * gb / m
    return w, b

def score(w, b, i):
    z = b + sum(a * c for a, c in zip(w, X[i])); return 1 / (1 + math.exp(-max(-30, min(30, z))))

def best_cutoff(w, b, idx):
    pairs = sorted((score(w, b, i), pl[i]) for i in idx)
    total = sum(p for _, p in pairs); kept = total; best = (total, 1.0)
    for s, p in pairs[::-1]:
        kept -= p
        if kept > best[0]: best = (kept, s)
    return best[1]

def auc(scores, labels):
    pos = [s for s, l in zip(scores, labels) if l]; neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg: return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg[:400])
    return wins / (len(pos) * min(len(neg), 400))

ordered = sorted(set(days)); folds = [ordered[i::4] for i in range(4)]
oof_scores, oof_labels, oof_gain = [], [], 0.0
for fold in folds:
    test = [i for i, d in enumerate(days) if d in fold]; train = [i for i, d in enumerate(days) if d not in fold]
    w, b = fit(train); c = best_cutoff(w, b, train) if cfg["cutoff_mode"] == "pl_optimal" else None
    if c is None:
        s = sorted(score(w, b, i) for i in train); c = s[int((1 - float(cfg["cancel_fraction"])) * (len(s) - 1))]
    for i in test:
        s = score(w, b, i); oof_scores.append(s); oof_labels.append(y[i])
        if s > c: oof_gain -= pl[i]
w, b = fit(list(range(len(X))))
if cfg["cutoff_mode"] == "pl_optimal":
    cutoff = best_cutoff(w, b, range(len(X)))
else:
    s = sorted(score(w, b, i) for i in range(len(X))); cutoff = s[int((1 - float(cfg["cancel_fraction"])) * (len(s) - 1))]
Path("model.json").write_text(json.dumps({"weights": w, "bias": b, "cutoff": cutoff}))
Path("validation.json").write_text(json.dumps({"rows": len(rows), "oof_auc": auc(oof_scores, oof_labels),
    "oof_cancel_pl_gain": round(oof_gain, 4), "positive_rate": sum(y) / len(y)}))
"""


def generate_orders(
    seed: int, family: str, days: int = 60, per_day: int = 70
) -> list[dict[str, Any]]:
    f = FAMILIES[family]
    rng = random.Random(seed)
    rows = []
    for day in range(days):
        volatile = rng.random() < f["volatile"]
        vol = f["vol"] if volatile else 1.0
        shock = rng.gauss(0, 4.0 * vol)
        for k in range(per_day):
            f1, f2 = rng.gauss(0, 1), rng.gauss(0, 1)
            quality = 0.8 * f1 - 0.5 * f2 + rng.gauss(0, 0.6)
            edge = f["edge_vol"] if volatile else f["edge"]
            outcome = edge + 6.0 * quality + rng.gauss(0, 6.0 * vol) + shock
            adverse = abs(rng.gauss(0, 10.0 * vol)) + f["coupling"] * max(0.0, -outcome)
            rows.append(
                {
                    "order_id": f"d{day:03d}-{k:03d}",
                    "day": day,
                    "regime": "volatile" if volatile else "calm",
                    "vol": round(vol, 3),
                    "f1": round(f1, 5),
                    "f2": round(f2, 5),
                    "adverse_move_bp": round(adverse, 4),
                    "filled": int(rng.random() < 0.75),
                    "outcome_pl_bp": round(outcome, 4),
                }
            )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


PERIODS = {"dev": range(0, 40), "holdout": range(40, 60)}


class WorldBacktester:
    def __init__(self, world: Path, workbench: Workbench):
        self.world, self.workbench = world, workbench

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "synthetic-quant-world-backtester",
            "orders": file_digest(self.world / "orders.csv"),
            "runner": digest(BACKTEST_RUNNER),
            "workbench": self.workbench.identity(),
        }

    def run(
        self, candidate: ResolvedCandidate, settings: dict[str, Any], period: str, workdir: Path
    ) -> dict[str, Any]:
        if period not in PERIODS:
            raise AdmissionError("unknown synthetic period")
        if candidate.model_dir is None:
            raise AdmissionError("candidate has no model")
        workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        rule = self.world / "strategy" / "cancel_rule.py"
        for patch, folder in zip(candidate.patches, candidate.patch_dirs, strict=True):
            if patch["target"] == "cancel_rule":
                rule = folder / "cancel_rule.py"
        (workdir / "run.py").write_text(BACKTEST_RUNNER)
        (workdir / "config.json").write_text(
            json.dumps(
                {
                    "cancel_rule": str(rule.resolve()),
                    "features": str((candidate.model_dir / "features.py").resolve()),
                    "model": str((candidate.model_dir / "model.json").resolve()),
                    "orders": str((self.world / "orders.csv").resolve()),
                    "settings": settings,
                    "days": list(PERIODS[period]),
                }
            )
        )
        result = self.workbench.execute(
            "python",
            ["run.py"],
            workdir,
            readonly=(self.world, candidate.root),
            log_name="backtest",
        )
        if result["status"] != "PASS":
            return {
                "status": "FAILED",
                "error": result["stderr"][-3000:],
                "result_origin": "SIMULATED_FIXTURE",
            }
        value = json.loads((workdir / "metrics.json").read_text())
        return {
            "status": "SUCCEEDED",
            "metrics": value["metrics"],
            "daily": value["daily"],
            "tables": [
                {
                    "name": "trades",
                    "path": str(workdir / "trades.csv"),
                    "description": "Every order in the period with decision-time fields, heuristic/model decisions, would-be outcome and realized PL (bp)",
                },
                {
                    "name": "daily",
                    "path": str(workdir / "daily.csv"),
                    "description": "Daily realized PL (bp)",
                },
            ],
            "result_origin": "SIMULATED_FIXTURE",
            "provenance": {"runner": digest(BACKTEST_RUNNER), "period": period},
        }


TRAINING_SPACE: dict[str, dict[str, Any]] = {
    "l2": {"kind": "float", "minimum": 0.0, "maximum": 1.0, "default": 0.01},
    "epochs": {"kind": "int", "minimum": 20, "maximum": 400, "default": 120},
    "learning_rate": {"kind": "float", "minimum": 0.01, "maximum": 2.0, "default": 0.5},
    "cutoff_mode": {
        "kind": "choice",
        "choices": ["pl_optimal", "quantile"],
        "default": "pl_optimal",
    },
    "cancel_fraction": {"kind": "float", "minimum": 0.0, "maximum": 0.6, "default": 0.15},
}


class WorldTrainer:
    def __init__(self, world: Path, workbench: Workbench):
        self.world, self.workbench = world, workbench

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "synthetic-quant-world-trainer",
            "runner": digest(TRAIN_RUNNER),
            "space": digest(TRAINING_SPACE),
        }

    def validate_config(self, space: dict[str, Any] | None, config: dict[str, Any]) -> None:
        space = space or TRAINING_SPACE
        for name, value in config.items():
            if name not in space:
                raise ValueError(f"undeclared training option {name}")
            spec = space[name]
            if spec["kind"] == "choice":
                if value not in spec["choices"]:
                    raise ValueError(f"training option {name} outside choices")
            elif (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not (spec["minimum"] <= value <= spec["maximum"])
            ):
                raise ValueError(f"training option {name} outside declared range")

    def run(
        self,
        base: ResolvedCandidate,
        config: dict[str, Any],
        training_data: Path | None,
        pipeline_patch: Path | None,
        workdir: Path,
    ) -> dict[str, Any]:
        workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        options = {k: v["default"] for k, v in TRAINING_SPACE.items()} | config
        data = training_data or (self.world / "baseline_training.csv")
        features = (
            (pipeline_patch / "features.py")
            if pipeline_patch
            else (self.world / "pipeline" / "features.py")
        )
        shutil.copy2(features, workdir / "features.py")
        local_data = workdir / "data.csv"
        shutil.copy2(data, local_data)
        (workdir / "train.py").write_text(TRAIN_RUNNER)
        (workdir / "config.json").write_text(
            json.dumps({**options, "features": "features.py", "data": "data.csv"})
        )
        result = self.workbench.execute("python", ["train.py"], workdir, log_name="train")
        if result["status"] != "PASS":
            return {"status": "FAILED", "error": (result["stderr"] or result["stdout"])[-3000:]}
        validation = json.loads((workdir / "validation.json").read_text())
        return {
            "status": "SUCCEEDED",
            "model_id": "world-cancel-" + file_digest(workdir / "model.json")[7:19],
            "files": [
                {"name": "model.json", "path": str(workdir / "model.json")},
                {"name": "features.py", "path": str(workdir / "features.py")},
            ],
            "validation": {**validation, "options": options, "oof": "time-blocked 4-fold by day"},
            "data_policy": None,
        }


VALIDATE_RULE = """import importlib.util, json, random, sys
spec = importlib.util.spec_from_file_location("cancel_rule", "cancel_rule.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
rng = random.Random(7); checks = []
settings = {"heuristic_cancel_bps": 40.0, "cancel_cutoff_delta": 0.0, "bypass_cancel_model": False}
for regime, vol in (("calm", 1.0), ("volatile", 2.0)):
    for i in range(200):
        order = {"order_id": f"t{i}", "day": i % 5, "regime": regime, "vol": vol, "f1": rng.gauss(0, 1),
                 "f2": rng.gauss(0, 1), "adverse_move_bp": abs(rng.gauss(0, 15 * vol))}
        a = mod.should_cancel(dict(order), dict(settings)); b = mod.should_cancel(dict(order), dict(settings))
        if not isinstance(a, bool) or a != b:
            raise SystemExit("should_cancel must deterministically return bool")
        checks.append(a)
rate = sum(checks) / len(checks)
if rate > 0.9:
    raise SystemExit("rule cancels almost every order")
print(json.dumps({"cancel_rate_on_fixture": rate, "cases": len(checks)}))
"""

VALIDATE_FEATURES = """import importlib.util, json, random
spec = importlib.util.spec_from_file_location("features", "features.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
rng = random.Random(3); widths = set()
for i in range(200):
    order = {"order_id": f"t{i}", "day": 1, "regime": "calm", "vol": 1.0, "f1": rng.gauss(0, 1), "f2": rng.gauss(0, 1),
             "adverse_move_bp": abs(rng.gauss(0, 15))}
    row = {**{k: str(v) for k, v in order.items()}, "outcome_pl_bp": str(rng.gauss(0, 10)), "filled": "1"}
    x = mod.features(dict(order)); widths.add(len(x))
    if not all(isinstance(v, float) for v in x):
        raise SystemExit("features must return floats")
    if mod.make_label(row) not in (0, 1):
        raise SystemExit("make_label must return 0 or 1")
if len(widths) != 1:
    raise SystemExit("feature width must be constant")
print(json.dumps({"feature_width": widths.pop(), "cases": 200, "leakage": "features receive decision-time fields only"}))
"""


class WorldValidator:
    def __init__(self, world: Path, filename: str, script: str):
        self.world, self.filename, self.script = world, filename, script

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "synthetic-world-validator",
            "file": self.filename,
            "script": digest(self.script),
        }

    def base_files(self, target: ImplementTarget, base: ResolvedCandidate) -> dict[str, bytes]:
        for patch, folder in zip(base.patches, base.patch_dirs, strict=True):
            if patch["target"] == target.name:
                return {self.filename: (folder / self.filename).read_bytes()}
        section = "strategy" if self.filename == "cancel_rule.py" else "pipeline"
        return {self.filename: (self.world / section / self.filename).read_bytes()}

    def validate(
        self, target: ImplementTarget, base: ResolvedCandidate, work: Path, workbench: Workbench
    ) -> dict[str, Any]:
        check = work.parent / "validation"
        shutil.rmtree(check, ignore_errors=True)
        check.mkdir(mode=0o700)
        shutil.copy2(work / self.filename, check / self.filename)
        (check / "check.py").write_text(self.script)
        result = workbench.execute("python", ["check.py"], check, log_name="validate")
        return {
            "status": "PASS" if result["status"] == "PASS" else "FAIL",
            "stdout": result["stdout"][-2000:],
            "stderr": result["stderr"][-3000:],
            "checks": [
                "imports",
                "deterministic bool interface",
                "fixture cases",
                "decision-time inputs only",
            ],
        }


def analysis_environment() -> AnalysisEnvironment:
    import sys

    site = tuple(p for p in sys.path if p.endswith("site-packages"))
    return AnalysisEnvironment(
        site_paths=site,
        packages={"pandas": "declared", "numpy": "declared"},
        limits=WorkbenchLimits(wall_seconds=120, cpu_seconds=100),
    )


def build_world(
    store: Store,
    root: Path,
    *,
    seed: int = 11,
    family: str = "balanced",
    task_id: str = "world",
    per_day: int = 70,
) -> dict[str, Any]:
    """Write the world files and owner declarations; returns contract/baseline refs."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    orders = generate_orders(seed, family, per_day=per_day)
    write_csv(root / "orders.csv", orders)
    (root / "strategy").mkdir(exist_ok=True)
    (root / "pipeline").mkdir(exist_ok=True)
    (root / "strategy" / "cancel_rule.py").write_text(CANCEL_RULE)
    (root / "pipeline" / "features.py").write_text(FEATURES)
    (root / "pipeline" / "train.py").write_text(TRAIN_RUNNER)
    (root / "strategy" / "README.md").write_text(
        "# Synthetic cancel strategy\n\nEach order is evaluated once at its decision time. The heuristic "
        "cancel rule (cancel_rule.py) runs first with settings['heuristic_cancel_bps']. Orders it keeps are "
        "scored by the cancel model (features.py + model.json); scores above the model cutoff "
        "(+ settings['cancel_cutoff_delta']) are cancelled. Kept filled orders realize their outcome PL in bp. "
        "The cancel model was trained on orders that survived the baseline heuristic at 40 bp. "
        "Regimes: calm days and volatile days (vol field)."
    )
    workbench = Workbench(analysis_environment())
    # Baseline model: trained by the owner on the dev period surviving the 40 bp heuristic.
    baseline_rows = [
        {**o, "heuristic_cancel": int(o["adverse_move_bp"] > 40.0)}
        for o in orders
        if o["day"] in PERIODS["dev"]
    ]
    write_csv(root / "baseline_training.csv", baseline_rows)
    trainer = WorldTrainer(root, workbench)
    trained = trainer.run(
        ResolvedCandidate(
            "",
            Candidate(
                label="seed",
                parent_ref=None,
                model_ref=None,
                origin="owner_baseline",
                data_policy="owner",
            ),
            root,
            None,
            None,
            (),
            (),
            {},
        ),
        {"epochs": 120},
        root / "baseline_training.csv",
        None,
        root / "baseline-model",
    )
    if trained["status"] != "SUCCEEDED":
        raise AdmissionError("baseline model training failed: " + trained.get("error", ""))
    model = ModelArtifact(
        model_id="world-baseline-M0",
        files={f["name"]: store.blobs.put_file(Path(f["path"])) for f in trained["files"]},
        training_config={"epochs": 120},
        training_data_ref=None,
        data_policy="owner trained on dev orders surviving the 40 bp heuristic",
        validation=trained["validation"],
        origin="owner_provided",
        trainer=trainer.identity(),
    )
    baseline = Candidate(
        label="baseline",
        parent_ref=None,
        model_ref=store.artifacts.put(model),
        settings={
            "heuristic_cancel_bps": 40.0,
            "cancel_cutoff_delta": 0.0,
            "bypass_cancel_model": False,
        },
        origin="owner_baseline",
        data_policy="owner baseline: M0 with the 40 bp heuristic",
    )
    baseline_ref = store.artifacts.put(baseline)
    scopes = [
        (
            "strategy_readme",
            root / "strategy" / "README.md",
            "document",
            "How the cancel strategy works",
        ),
        (
            "cancel_rule",
            root / "strategy" / "cancel_rule.py",
            "strategy",
            "Heuristic cancel rule (editable target)",
        ),
        (
            "features",
            root / "pipeline" / "features.py",
            "pipeline",
            "Model labels and features (editable target)",
        ),
        (
            "training",
            root / "pipeline" / "train.py",
            "pipeline",
            "Training procedure with time-blocked OOF",
        ),
    ]
    contract = AutonomyContract(
        task_id=task_id,
        instruction=(
            "Hermes-like task: we have a baseline trading + cancel model. Research and optimize the heuristic "
            "cancel bp. You may try parameters, compare model versions, retrain or improve the training method "
            "when the evidence calls for it, and propose new cancel rules. Find a candidate whose PL or mean PR "
            "beats the baseline under the risk limits, with complete research evidence."
        ),
        objective="Improve PL or mean PR over the baseline on the dev period without breaching drawdown limits",
        focus="heuristic cancel bp and everything it interacts with (cancel model population, retraining, rules)",
        domain="synthetic-quant-world",
        evaluator_id=digest(BACKTEST_RUNNER),
        dataset_id=file_digest(root / "orders.csv"),
        periods=("dev",),
        exposure="synthetic",
        baseline_candidate_ref=baseline_ref,
        settings=(
            SettingSpec(
                name="heuristic_cancel_bps",
                kind="float",
                minimum=5,
                maximum=150,
                baseline=40.0,
                description="Cancel when adverse move (bp) exceeds this",
                affects_model_inputs=True,
            ),
            SettingSpec(
                name="cancel_cutoff_delta",
                kind="float",
                minimum=-0.3,
                maximum=0.3,
                baseline=0.0,
                description="Shift of the cancel model cutoff",
            ),
            SettingSpec(
                name="bypass_cancel_model",
                kind="bool",
                baseline=False,
                description="Disable the cancel model (diagnostic control)",
            ),
        ),
        training_space=TRAINING_SPACE,
        implement_targets=(
            ImplementTarget(
                name="cancel_rule",
                kind="strategy_patch",
                files=("cancel_rule.py",),
                description="Heuristic cancel rule run before the cancel model",
                interface="def should_cancel(order: dict, settings: dict) -> bool; order has order_id, day, regime, vol, f1, f2, adverse_move_bp",
            ),
            ImplementTarget(
                name="features",
                kind="pipeline_patch",
                files=("features.py",),
                description="Cancel-model labels and features used by training and backtests",
                interface="def make_label(row) -> 0|1 (row has outcome_pl_bp); def features(order) -> list[float] from decision-time fields only",
            ),
        ),
        source_scopes=tuple(
            SourceScope(name=n, path=str(p), sha256=file_digest(p), kind=k, description=d)  # type: ignore[arg-type]
            for n, p, k, d in scopes
        ),
        metrics=(
            MetricSpec(
                name="pl",
                direction="maximize",
                description="Total realized PL (bp) of executed orders",
            ),
            MetricSpec(
                name="mean_pr", direction="maximize", description="Mean PL per executed order (bp)"
            ),
            MetricSpec(
                name="mdd",
                direction="minimize",
                description="Max drawdown of cumulative daily PL (bp)",
            ),
            MetricSpec(name="count", direction="maximize", description="Executed orders"),
            MetricSpec(
                name="win_rate",
                direction="maximize",
                description="Share of executed orders with PL > 0",
            ),
        ),
        acceptance_profiles=(
            AcceptanceProfile(
                name="pl_first",
                objectives=(Objective(metric="pl", min_gain=0.02, relative=True),),
                risk_limits=(
                    RiskLimit(metric="mdd", max_worsening=0.25),
                    RiskLimit(metric="mean_pr", max_worsening=0.15),
                ),
                description="PL up at least 2% with drawdown and PR guarded",
            ),
            AcceptanceProfile(
                name="pr_first",
                objectives=(Objective(metric="mean_pr", min_gain=0.05, relative=True),),
                risk_limits=(
                    RiskLimit(metric="pl", max_worsening=0.05),
                    RiskLimit(metric="mdd", max_worsening=0.25),
                ),
                min_count_fraction=0.6,
                description="Mean PR up at least 5% while keeping PL within 5%",
            ),
        ),
        analysis_environment=analysis_environment(),
        grants=Grants(max_training_runs=6, max_implementations=4, max_analysis_steps=8),
        constraints=(
            "Synthetic engineering world; results are SIMULATED_FIXTURE and establish no trading claim.",
            "Do not change the evaluator, data or periods.",
        ),
        origin="declared_fixture",
    )
    return {
        "contract_ref": store.artifacts.put(contract),
        "baseline_ref": baseline_ref,
        "root": root,
        "workbench": workbench,
    }


def world_executors(world: dict[str, Any]) -> Any:
    from jarvis.research_executors import Executors

    root, workbench = world["root"], world["workbench"]
    return Executors(
        workbench=workbench,
        backtester=WorldBacktester(root, workbench),
        trainer=WorldTrainer(root, workbench),
        validators={
            "cancel_rule": WorldValidator(root, "cancel_rule.py", VALIDATE_RULE),
            "features": WorldValidator(root, "features.py", VALIDATE_FEATURES),
        },
    )


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


class QuantWorldFactory:
    """Dream task factory: one synthetic world per task; holdout days stay hidden."""

    def __init__(self, per_day: int = 70):
        self.per_day = per_day

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "synthetic-quant-world-factory",
            "backtest": digest(BACKTEST_RUNNER),
            "train": digest(TRAIN_RUNNER),
            "families": digest(FAMILIES),
            "per_day": self.per_day,
        }

    def build(self, store: Store, task: Any, root: Path) -> dict[str, Any]:
        from jarvis.research_executors import resolve_candidate

        world = build_world(
            store,
            root,
            seed=task.seed,
            family=task.family,
            task_id=task.task_id,
            per_day=self.per_day,
        )
        executors = world_executors(world)

        def holdout(candidate_ref: str, settings: dict[str, Any]) -> dict[str, Any]:
            resolved = resolve_candidate(store, candidate_ref, root / "holdout-candidates")
            out = executors.backtester.run(
                resolved,
                settings,
                "holdout",
                root / "holdout" / digest([candidate_ref, settings])[7:23],
            )
            if out["status"] != "SUCCEEDED":
                raise AdmissionError("holdout evaluation failed: " + str(out.get("error"))[:300])
            return dict(out["metrics"])

        return {"contract_ref": world["contract_ref"], "executors": executors, "holdout": holdout}


def declared_world(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    """Declaration adapter for `jarvis autonomy start`: a synthetic world under args.root."""
    root = Path(args["root"]).resolve()
    world = build_world(
        store,
        root,
        seed=int(args.get("seed", 11)),
        family=str(args.get("family", "balanced")),
        task_id=str(args.get("task_id", "world")),
    )
    return {
        "contract_ref": world["contract_ref"],
        "executors": world_executors(world),
        "input_refs": (),
    }
