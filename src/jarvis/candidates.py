"""Cross-candidate comparison under owner-declared, multi-objective acceptance profiles.

Candidates may use different models. Comparisons still require the same evaluator,
dataset and period, and keep model/data-generation lineage visible in the result.
"""

import math
from typing import Any

from jarvis.autonomy_contracts import AcceptanceProfile, AutonomyContract, MetricSpec
from jarvis.contracts import AdmissionError


def _gain(spec: MetricSpec, candidate: float, baseline: float, relative: bool) -> float:
    raw = candidate - baseline if spec.direction == "maximize" else baseline - candidate
    return raw / abs(baseline) if relative and baseline else raw


def _measured(metrics: dict[str, Any], name: str) -> bool:
    value = metrics.get(name)
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def assess_profile(
    contract: AutonomyContract,
    profile: AcceptanceProfile,
    candidate: dict[str, float],
    baseline: dict[str, float],
) -> dict[str, Any]:
    specs = {m.name: m for m in contract.metrics}
    objectives: list[dict[str, Any]] = []
    for objective in profile.objectives:
        if not _measured(candidate, objective.metric) or not _measured(baseline, objective.metric):
            objectives.append({"metric": objective.metric, "status": "MISSING"})
            continue
        gain = _gain(
            specs[objective.metric],
            candidate[objective.metric],
            baseline[objective.metric],
            objective.relative,
        )
        objectives.append(
            {
                "metric": objective.metric,
                "gain": gain,
                "relative": objective.relative,
                "status": "IMPROVED" if gain > objective.min_gain else "NOT_IMPROVED",
            }
        )
    risks: list[dict[str, Any]] = []
    for limit in profile.risk_limits:
        needs_baseline = limit.max_worsening is not None
        if not _measured(candidate, limit.metric) or (
            needs_baseline and not _measured(baseline, limit.metric)
        ):
            risks.append({"metric": limit.metric, "status": "MISSING"})
            continue
        row: dict[str, Any] = {"metric": limit.metric}
        breached = False
        if limit.max_worsening is not None:
            worsening = -_gain(
                specs[limit.metric], candidate[limit.metric], baseline[limit.metric], limit.relative
            )
            row.update(worsening=worsening, limit=limit.max_worsening)
            breached = worsening > limit.max_worsening + 1e-12
        if limit.floor is not None:
            value = candidate[limit.metric]
            below = (
                value < limit.floor
                if specs[limit.metric].direction == "maximize"
                else value > limit.floor
            )
            row["floor"] = limit.floor
            breached = breached or below
        risks.append({**row, "status": "BREACHED" if breached else "WITHIN"})
    count_ok = True
    if _measured(baseline, "count") and baseline["count"] > 0:
        count_ok = _measured(candidate, "count")
    if (
        count_ok
        and _measured(candidate, "count")
        and _measured(baseline, "count")
        and baseline["count"] > 0
    ):
        count_ok = candidate["count"] >= profile.min_count_fraction * baseline["count"]
    missing = any(x["status"] == "MISSING" for x in [*objectives, *risks])
    passed = (
        not missing
        and count_ok
        and any(o["status"] == "IMPROVED" for o in objectives)
        and all(r["status"] == "WITHIN" for r in risks)
    )
    return {
        "profile": profile.name,
        "verdict": "INSUFFICIENT_DATA" if missing else ("PASS" if passed else "FAIL"),
        "objectives": objectives,
        "risk_limits": risks,
        "count_sufficient": count_ok,
    }


def pareto_front(contract: AutonomyContract, rows: list[dict[str, Any]]) -> list[str]:
    """Non-dominated result refs over every metric used as a profile objective."""
    specs = {m.name: m for m in contract.metrics}
    names = sorted({o.metric for p in contract.acceptance_profiles for o in p.objectives})

    def oriented(row: dict[str, Any]) -> list[float] | None:
        values = []
        for name in names:
            if not _measured(row["metrics"], name):
                return None
            value = float(row["metrics"][name])
            values.append(value if specs[name].direction == "maximize" else -value)
        return values

    points = [(row["result_ref"], oriented(row)) for row in rows]
    valid = [(ref, p) for ref, p in points if p is not None]
    front = []
    for ref, p in valid:
        dominated = any(
            all(a >= b for a, b in zip(q, p, strict=True))
            and any(a > b for a, b in zip(q, p, strict=True))
            for _, q in valid
        )
        if not dominated:
            front.append(ref)
    return sorted(front)


def compare_results(
    contract: AutonomyContract, baseline: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Rows are observed backtest results: result_ref, candidate_ref, period, metrics."""
    for row in [baseline, *rows]:
        if row.get("status") != "SUCCEEDED":
            raise AdmissionError("only completed observations can be compared")
        identity = (row["evaluator_id"], row["dataset_id"], row["period"])
        if identity != (baseline["evaluator_id"], baseline["dataset_id"], baseline["period"]):
            raise AdmissionError("comparison requires the same evaluator, dataset and period")
    table = []
    for row in rows:
        table.append(
            {
                "result_ref": row["result_ref"],
                "candidate_ref": row["candidate_ref"],
                "label": row.get("label"),
                "model_id": row.get("model_id"),
                "data_policy": row.get("data_policy"),
                "metrics": row["metrics"],
                "profiles": [
                    assess_profile(contract, p, row["metrics"], baseline["metrics"])
                    for p in contract.acceptance_profiles
                ],
            }
        )
    return {
        "kind": "CandidateComparison",
        "baseline_result_ref": baseline["result_ref"],
        "baseline_metrics": baseline["metrics"],
        "period": baseline["period"],
        "rows": table,
        "pareto_front": pareto_front(contract, [baseline, *rows]),
        "candidates_compared": len(rows),
        "selection_scope": "development comparison on exposed or synthetic data; repeated selection inflates apparent gains",
    }


def select_finalist(contract: AutonomyContract, comparison: dict[str, Any]) -> dict[str, Any]:
    """Pre-declared rule: earliest profile with a passing candidate; best first objective."""
    specs = {m.name: m for m in contract.metrics}
    for index, profile in enumerate(contract.acceptance_profiles):
        passing = [row for row in comparison["rows"] if row["profiles"][index]["verdict"] == "PASS"]
        if passing:
            metric = profile.objectives[0].metric
            sign = 1 if specs[metric].direction == "maximize" else -1
            best = sorted(passing, key=lambda r: (-sign * r["metrics"][metric], r["result_ref"]))[0]
            return {
                "status": "SELECTED",
                "profile": profile.name,
                "result_ref": best["result_ref"],
                "candidate_ref": best["candidate_ref"],
                "rule": "first declared profile with a PASS; best on its first objective",
            }
    return {"status": "NO_PASSING_CANDIDATE", "rule": "first declared profile with a PASS"}
