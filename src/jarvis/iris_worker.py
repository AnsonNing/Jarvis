"""Opt-in stdlib worker around owner-supplied Iris and entry-only evaluator APIs.

No credentials, raw rows, proprietary strategy or private implementation is copied
into Jarvis. The approved modules are imported only here in the supplied runtime.
"""

import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("trusted module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def perform(operation: str, data: dict[str, Any], directory: Path) -> dict[str, Any]:
    env, request = data["environment"], data["request"]
    root = Path(env["root"])
    sys.path.insert(0, str(root))
    paths = {
        str(root / "iris_agent.py"): env["agent_script_sha256"],
        env["evaluator"]: env["evaluator_sha256"],
        env["template"]: env["template_sha256"],
        **env["accepted_inputs"],
    }
    if any(sha(Path(p)) != expected for p, expected in paths.items()):
        raise ValueError("protected source drift")
    evaluator = load_module(Path(env["evaluator"]), "jarvis_private_entry_evaluator")
    rid = request["request_id"]
    if not re.fullmatch(r"[a-f0-9]{8}", rid):
        raise ValueError("invalid external request ID")
    if operation == "import":
        proof = request["local_import"]
        path = Path(proof["raw"])
        if (
            sha(path) != proof["raw_sha256"]
            or sha(Path(proof["manifest"])) != env["accepted_inputs"][proof["manifest"]]
        ):
            raise ValueError("local baseline evidence changed")
        case = next(
            c
            for c in json.loads(Path(proof["manifest"]).read_text())["cases"]
            if c["label"] == proof["label"]
        )
        if (
            case != request["baseline_case"]
            or case["status"] != "completed"
            or case["result_exit_code"] != 0
            or case["request_id"] != rid
            or "sha256:" + case["sha256"] != env["template_sha256"]
        ):
            raise ValueError("local baseline case lineage mismatch")
        job = case["job"]
        if (
            job["strategy"] != "hermes"
            or job["branch"] != env["branch"]
            or "-".join(job["period"]) != request["period"]
            or Path(case["result_dir"]).resolve() != path.parent.resolve()
        ):
            raise ValueError("local baseline job or result directory mismatch")
        completion = re.search(r"(\d+) days, (\d+) failed", case["result_output"])
        if (
            not completion
            or int(completion[1]) != len(env["period"]["trading_dates"])
            or int(completion[2])
        ):
            raise ValueError("local baseline calendar incomplete")
        return evaluate_result(evaluator, env, request, path, job, None)
    iris = load_module(root / "iris_agent.py", "iris_agent")
    cfg = iris.load_config()  # Never returned, logged or passed to the parent.
    if operation == "quota":
        known = set(request["known_request_ids"])
        candidates = []
        for page in range(1, 6):
            listing = iris.api(
                cfg["token"], "files.list", channel=cfg["channel"], count=200, page=page
            )
            for file in listing["files"]:
                match = re.fullmatch(r"iris-([a-f0-9]{8})-queued\.txt", file.get("name", ""))
                if match and match[1] in known:
                    candidates.append((int(file["created"]), match[1], file))
            if page >= int(listing.get("paging", {}).get("pages", 1)):
                break
        for created, known_id, file in sorted(candidates, key=lambda x: x[0], reverse=True)[:5]:
            ack = iris.download(cfg, file)
            snapshot = quota_snapshot(ack, created, known_id, env["branch"])
            if snapshot:
                return {"status": "QUOTA_OBSERVED", "snapshot": snapshot}
        return {"status": "QUOTA_UNAVAILABLE"}
    if operation == "submit":
        candidate = Path(request["candidate_path"])
        if sha(candidate) != request["candidate_sha256"]:
            raise ValueError("candidate changed")
        sender = f"[{cfg['name']}_agent]" if cfg.get("name") else "[agent]"
        # ID was durably reserved before any upload. Unknown uploads are collected
        # by this same ID; there is deliberately no second submit path.
        iris.upload(cfg, candidate, f'{sender} "{env["branch"]}" "{request["period"]}" req:{rid}')
        return {"status": "SUBMITTED", "request_id": rid, "candidate_sha256": sha(candidate)}
    found = iris.answers(cfg, rid, None)
    queue_ack = iris.download(cfg, found[iris.QUEUED]) if iris.QUEUED in found else None
    queued_meta = found.get(iris.QUEUED, {})
    quota = quota_snapshot(
        queue_ack,
        queued_meta.get("created") if isinstance(queued_meta, dict) else None,
        rid,
        env["branch"],
    )
    if iris.REJECTED in found:
        reason = (
            "quota_exhausted"
            if re.search(r"quota|daily limit|額度", iris.download(cfg, found[iris.REJECTED]), re.I)
            else "backend_rejected"
        )
        return {
            "status": "REJECTED",
            "request_id": rid,
            "queue_ack": queue_ack,
            "quota_snapshot": quota,
            "reason": reason,
        }
    if iris.DONE not in found or iris.JOB not in found:
        return {
            "status": "PENDING",
            "request_id": rid,
            "queue_ack": queue_ack,
            "quota_snapshot": quota,
        }
    job = json.loads(iris.download(cfg, found[iris.JOB]))
    reply = iris.download(cfg, found[iris.DONE])
    if iris.exit_code(reply) != 0:
        return {
            "status": "FAILED",
            "request_id": rid,
            "job": job,
            "reason": "build or execution failure",
            "queue_ack": queue_ack,
            "quota_snapshot": quota,
        }
    actual_period = "-".join(job.get("period", []))
    if (
        job.get("strategy") != "hermes"
        or job.get("branch") != env["branch"]
        or actual_period != request["period"]
    ):
        raise ValueError("Iris job branch/period mismatch")
    completion = re.search(r"(\d+) days, (\d+) failed", reply)
    dates = [str(d) for d in env["period"]["trading_dates"]]
    if not completion or int(completion[1]) != len(dates) or int(completion[2]):
        raise ValueError("incomplete or mismatched market calendar")
    raw_names = [n for n in found if n.endswith(".csv") and "summary" not in n]
    if len(raw_names) != 1:
        return {
            "status": "PENDING",
            "request_id": rid,
            "reason": "waiting for unique raw trade attachment",
        }
    # Private API preserves native files. Its stdout is captured below and never
    # used as financial authority. Evaluation below always uses explicit dates.
    with contextlib.redirect_stdout(io.StringIO()):
        code = iris.outcome(cfg, rid, found, directory / "results")
    if code != 0:
        return {"status": "PENDING", "request_id": rid}
    folder = iris.result_dir(cfg, rid, found, directory / "results")
    path = folder / Path(raw_names[0]).name
    return {
        **evaluate_result(evaluator, env, request, path, job, queue_ack),
        "quota_snapshot": quota,
    }


def quota_snapshot(ack: Any, created: Any, request_id: str, branch: str) -> dict[str, Any] | None:
    if not ack or not created or branch not in ack:
        return None
    match = re.search(r"today\s+(\d+)/(\d+)", ack)
    if not match:
        return None
    return {
        "schema_version": 1,
        "kind": "IrisQuotaSnapshot",
        "used": int(match[1]),
        "limit": int(match[2]),
        "ack_created_at": datetime.fromtimestamp(float(created), UTC).isoformat(),
        "request_id": request_id,
        "source": "IRIS_QUEUE_ACK",
        "authoritative_current": False,
    }


def evaluate_result(
    evaluator: Any,
    env: dict[str, Any],
    request: dict[str, Any],
    path: Path,
    job: dict[str, Any],
    queue_ack: Any,
) -> dict[str, Any]:
    dates = [str(d) for d in env["period"]["trading_dates"]]
    evaluated = evaluator.evaluate(path, trading_dates=dates, expected_commit=job["sha"])
    if evaluated.get("status") != "PASS" or evaluated["daily_calendar"] != "explicit_trading_dates":
        raise ValueError("entry-only evaluation not admitted")
    metrics = evaluated["metrics"]
    products: dict[str, Decimal] = defaultdict(Decimal)
    entry_pl: list[float] = []
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            if row["product"] not in {"begin", "end"} and int(row["dynamicexit"]) == 0:
                products[row["product"]] += Decimal(row["pl"])
                entry_pl.append(float(row["pl"]))
    positive = sum((max(Decimal(0), p) for p in products.values()), Decimal(0))
    concentration = float(max(products.values()) / positive) if positive else None
    daily = {k: float(v) for k, v in metrics["daily_pl"].items()}
    pl = float(metrics["pl"])
    return {
        "status": "SUCCEEDED",
        "request_id": request["request_id"],
        "result_origin": "OBSERVED_IRIS_BACKTEST",
        "candidate_sha256": request["candidate_sha256"],
        "commit": job["sha"],
        "queue_ack": queue_ack,
        "entry_only_pl": pl,
        "count": metrics["count"],
        "mdd": float(metrics["daily_realized_mdd"]),
        "worst_day": float(metrics["worst_day_pl"]),
        "daily_pl": daily,
        "risk": {
            "best_day_removed_pl": pl - max(daily.values()),
            "largest_positive_product_share": concentration,
            "mean_pr": metrics["mean_pr"],
            "zero_entry_days": metrics["zero_entry_days"],
            "worst_entry_pl": min(entry_pl) if entry_pl else None,
            "best_entry_pl": max(entry_pl) if entry_pl else None,
            "entry_win_rate": sum(p > 0 for p in entry_pl) / len(entry_pl) if entry_pl else None,
            "five_worst_entries_pl": sum(sorted(entry_pl)[:5]),
            "drawdown_scope": "daily_realized_not_intraday",
            "fee_semantics": "source PL; complete live costs unverified",
        },
        "provenance": {
            "raw_sha256": sha(path),
            "entry_execution_sha256": evaluated["entry_execution_sha256"],
            "evaluator_sha256": env["evaluator_sha256"],
            "calendar": dates,
            "snapshots_excluded": evaluated["snapshot_rows_excluded"],
        },
    }


def main() -> None:
    operation, inp, out = sys.argv[1:]
    output = Path(out)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = perform(operation, json.loads(Path(inp).read_text()), output.parent)
    except Exception as exc:
        # Errors may contain secrets in upstream exception strings. Retain only
        # the type; the durable request is available for operator reconciliation.
        result = {
            "status": "UNKNOWN",
            "error_type": type(exc).__name__,
            "request_id": json.loads(Path(inp).read_text())["request"]["request_id"],
        }
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, allow_nan=False))
    temporary.replace(output)


if __name__ == "__main__":
    main()
