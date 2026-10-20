"""Declared development selection constraints, separate from the fixed PL evaluator."""

import math
from typing import Any

from pydantic import Field

from jarvis.contracts import Record


class ResearchAcceptance(Record):
    pr_max_decline_fraction: float = Field(default=0, ge=0, le=1)
    mdd_max_increase_fraction: float = Field(default=0, ge=0, le=10)
    require_positive_pl_gain: bool = True


def assess_candidate(
    candidate: dict[str, Any], baseline: dict[str, Any], rule: ResearchAcceptance
) -> dict[str, Any]:
    try:
        p, b = float(candidate["risk"]["mean_pr"]), float(baseline["risk"]["mean_pr"])
        cm, bm = abs(float(candidate["mdd"])), abs(float(baseline["mdd"]))
        gain = float(candidate["entry_only_pl"]) - float(baseline["entry_only_pl"])
        if not all(math.isfinite(x) for x in [p, b, cm, bm, gain]):
            raise ValueError("nonfinite measured result")
    except (KeyError, TypeError, ValueError):
        return {"status": "INSUFFICIENT_EVIDENCE", "qualified": False}
    tests = {
        "pl": gain > 0 if rule.require_positive_pl_gain else gain >= 0,
        "pr": p >= b - abs(b) * rule.pr_max_decline_fraction,
        "mdd": cm <= bm * (1 + rule.mdd_max_increase_fraction),
    }
    return {
        "status": "ASSESSED",
        "qualified": all(tests.values()),
        "checks": tests,
        "pl_delta": gain,
        "mean_pr": p,
        "baseline_mean_pr": b,
        "mdd_magnitude": cm,
        "baseline_mdd_magnitude": bm,
        "scope": "matched exposed development; not an independent efficacy verdict",
    }
