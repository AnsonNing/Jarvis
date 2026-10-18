"""Artificial backend for queue/meta-policy acceptance, never market evidence."""

from pathlib import Path
from typing import Any

from jarvis.identity import file_digest
from jarvis.iris_contracts import IrisEnvironment


class FixtureIrisTransport:
    def __init__(self, environment: IrisEnvironment, *, shift: bool = False):
        if environment.result_origin != "SIMULATED_FIXTURE":
            raise ValueError("fixture backend requires explicit artificial environment")
        self.environment = environment
        self.shift = shift
        self.submissions: list[str] = []
        self.requests: dict[str, dict[str, Any]] = {}

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "fixture-iris-not-market",
            "implementation": file_digest(Path(__file__)),
            "shift": self.shift,
        }

    def submit(self, request: dict[str, Any], directory: Path) -> dict[str, Any]:
        self.submissions.append(request["request_id"])
        self.requests[request["request_id"]] = request
        return {"status": "SUBMITTED", "request_id": request["request_id"]}

    def import_baseline(self, request: dict[str, Any], directory: Path) -> dict[str, Any]:
        raise ValueError("artificial backend cannot import observed market evidence")

    def collect(self, request: dict[str, Any], directory: Path) -> dict[str, Any]:
        # This artificial objective consumes only toy constants. It is not a
        # model of trading, prices, cancellation or the actual private strategy.
        import math
        import re

        text = Path(request["candidate_path"]).read_text()
        constants = {
            k: float(v) for k, v in re.findall(r"constexpr double (\w+) = ([-+0-9.eE]+);", text)
        }
        t = constants.get("toy_entry", 0)
        x = constants.get("toy_exit", 0.004)
        h = constants.get("toy_heuristic", 0.004)
        family = self.environment.model_lineage
        if family.startswith("quadratic"):
            gain = 4 - ((x * 10000 - 30) / 10) ** 2
        elif family.startswith("piecewise"):
            gain = 3 if x <= 0.003 else 0
        elif family.startswith("saturating"):
            gain = 4 * (1 - x / 0.004)
        elif family.startswith("cubic"):
            gain = ((40 - x * 10000) / 10) ** 3
        elif family.startswith("logarithmic"):
            gain = math.log(max(0.001, 41 - x * 10000))
        elif family.startswith("hinge"):
            gain = max(0, 40 - x * 10000) / 5
        elif family.startswith("exponential"):
            gain = math.exp((40 - x * 10000) / 10) - 1
        else:
            gain = 0
        baseline = t == 0 and x == 0.004 and h == 0.004
        pl = (
            1.0
            if baseline
            else 1
            + (-5 if self.shift and x != 0.004 else gain)
            - abs(t * 10000)
            - abs(h * 10000 - 40) / 10
        )
        if self.shift and t != 0:
            pl = 3.0
        dates = [str(d) for d in self.environment.period.trading_dates]
        daily = {d: pl / len(dates) for d in dates}
        return {
            "status": "SUCCEEDED",
            "request_id": request["request_id"],
            "result_origin": "SIMULATED_FIXTURE",
            "candidate_sha256": request["candidate_sha256"],
            "commit": "fixture-" + request["request_id"],
            "entry_only_pl": pl,
            "count": 3,
            "mdd": min(0.0, pl),
            "worst_day": pl / len(dates),
            "daily_pl": daily,
            "risk": {"fixture_only": True, "best_day_removed_pl": pl - pl / len(dates)},
            "provenance": {
                "calendar": dates,
                "evaluator_sha256": self.environment.evaluator_sha256,
                "raw_sha256": "fixture-no-raw-market-data",
                "snapshots_excluded": 2,
            },
        }
