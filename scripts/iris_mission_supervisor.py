"""Trusted operating harness; leaves the running library/source identity unchanged."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from jarvis.campaign import source_identity
from jarvis.contracts import IntegrityError
from jarvis.mission_runner import checkpoint
from jarvis.research_lab import payloads
from jarvis.research_queue import IrisResearchQueue, queue_tasks
from jarvis.storage import Store, utcnow


def reconcile_known_read(
    store: Store, mission: str, queue_engine: IrisResearchQueue | None = None
) -> dict[str, Any]:
    """Only collect a durably submitted request; never submit, repair LLMs or clear unknowns."""
    if store.manifest(mission)["source_identity"] != source_identity():
        raise IntegrityError("operating harness cannot change the pinned runtime")
    failed = payloads(store, mission, "MISSION_RUNNER_FAILED")
    if not failed:
        return {"status": "NEEDS_ATTENTION", "reason": "no diagnosed runner failure"}
    error = store.artifacts.get(failed[-1]["error_ref"])
    if (
        error.get("type") != "JarvisError"
        or "child research requires attention: NEEDS_RECONCILIATION"
        not in error.get("traceback", "")
    ):
        return {"status": "NEEDS_ATTENTION", "reason": "not a known child collection failure"}
    session = payloads(store, mission, "MISSION_SESSION_CREATED")[-1]["session"]
    episode = payloads(store, session, "SESSION_STAGE_CREATED")[-1]["episode"]
    program = payloads(store, episode, "LAB_PROGRAM_CREATED")[-1]["program_id"]
    queue = payloads(store, program, "RESEARCH_QUEUE_ATTACHED")[-1]["queue_id"]
    chain = [queue, program, episode, session, mission]
    if any(
        store.get_control(x) in {"PAUSED", "CANCELLED"}
        or store.status(x) in {"PAUSED", "CANCELLED", "CLOSED", "BUDGET_EXHAUSTED"}
        for x in chain
    ):
        return {
            "status": "NEEDS_ATTENTION",
            "reason": "owner stop, completed scope or budget boundary",
        }
    unfinished = [
        j for m in store.resource_members(mission) for j in store.jobs(m) if not j["finalized"]
    ]
    if any(store.artifacts.get(j["request_ref"]).get("role") for j in unfinished):
        return {
            "status": "NEEDS_ATTENTION",
            "reason": "unsettled provider operation; no read recovery",
        }
    reads = payloads(store, queue, "IRIS_COLLECTED")
    if not reads:
        return {"status": "NEEDS_ATTENTION", "reason": "no collection evidence"}
    latest = store.artifacts.get(reads[-1]["result_ref"])
    task_id = reads[-1]["task_id"]
    row = next(t for t in queue_tasks(store, queue) if t["task_id"] == task_id)
    job = next(j for j in store.jobs(queue) if j["id"] == row["job_id"])
    request = store.artifacts.get(job["request_ref"])
    if len(unfinished) > 1 or (unfinished and unfinished[0]["id"] != job["id"]):
        return {"status": "NEEDS_ATTENTION", "reason": "another unfinished operation exists"}
    submissions = [
        p for p in payloads(store, queue, "IRIS_SUBMIT_RETURNED") if p["task_id"] == task_id
    ]
    if (
        not submissions
        or store.artifacts.get(submissions[-1]["response_ref"]).get("status") != "SUBMITTED"
    ):
        return {"status": "NEEDS_ATTENTION", "reason": "submission completion is not known"}
    if not any(
        store.artifacts.get(p["result_ref"]).get("error_type") == "SlackError"
        for p in reads
        if p["task_id"] == task_id
    ):
        return {"status": "NEEDS_ATTENTION", "reason": "no classified read-only Slack error"}
    if latest.get("request_id") != request["request_id"]:
        raise IntegrityError("collection request identity changed")
    if latest["status"] == "UNKNOWN":
        if (
            latest.get("error_type") != "SlackError"
            or row["status"] != "RUNNING"
            or job["status"] != "RUNNING"
        ):
            return {
                "status": "NEEDS_ATTENTION",
                "reason": "unknown operation is not an eligible read",
            }
        result = (queue_engine or IrisResearchQueue(store)).tick(queue)
        if result["status"] not in {
            "WAITING_FOR_IRIS",
            "SUCCEEDED",
            "FAILED",
            "REJECTED",
            "INVALID_RESULT",
            "ADOPTED",
        }:
            return {
                "status": "WAITING_FOR_READ_RECOVERY",
                "request_id": request["request_id"],
                "result": result,
            }
    elif latest["status"] not in {"PENDING", "SUCCEEDED", "FAILED", "REJECTED", "INVALID_RESULT"}:
        return {"status": "NEEDS_ATTENTION", "reason": "unrecognized read outcome"}
    proof = {
        "request_id": request["request_id"],
        "queue": queue,
        "known_read_ref": payloads(store, queue, "IRIS_COLLECTED")[-1]["result_ref"],
        "source_identity": source_identity(),
        "submission_count_unchanged": True,
        "reason": "Known upload, same-request read-only recovery; original failure and reservation retained",
    }
    for owner in [episode, session, mission]:
        if store.status(owner) not in {"LAB_NEEDS_ATTENTION", "NEEDS_RECONCILIATION"}:
            raise IntegrityError("ancestor failure scope changed")
    for owner in [episode, session, mission]:
        store.append(owner, "KNOWN_IRIS_READ_RECONCILED", proof)
        store.set_status(owner, "READY")
    return {"status": "RECOVERED", **proof}


def process_matches(meta: dict[str, Any]) -> bool:
    result = subprocess.run(
        ["ps", "-p", str(meta["pid"]), "-o", "command="], capture_output=True, text=True, timeout=5
    )
    return (
        result.returncode == 0
        and meta["mission_id"] in result.stdout
        and "mission-run" in result.stdout
    )


def restart(meta_path: Path, store: Store) -> dict[str, Any]:
    meta = json.loads(meta_path.read_text())
    if process_matches(meta):
        raise IntegrityError("refuse duplicate mission runner")
    if meta["source_identity"] != source_identity():
        raise IntegrityError("saved runtime changed")
    with Path(meta["log"]).open("ab", buffering=0) as log:
        process = subprocess.Popen(
            meta["arguments"],
            cwd=meta["cwd"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    with meta_path.with_name("process-history.jsonl").open("a") as stream:
        stream.write(json.dumps(meta) + "\n")
    meta.update(
        pid=process.pid,
        started_at=utcnow(),
        restart_reason="Trusted supervisor: same mission/store/arguments",
    )
    checkpoint(meta_path, meta)
    store.append(
        meta["task_id"],
        "MISSION_SUPERVISOR_RESTARTED",
        {"pid": process.pid, "mission": meta["mission_id"]},
    )
    return meta


def supervise(meta_path: Path, state_path: Path, *, seconds: float = 86400) -> None:
    meta = json.loads(meta_path.read_text())
    store = Store(Path(meta["store"]))
    mission = meta["mission_id"]
    script_hash = "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    status: dict[str, Any] = {
        "mission": mission,
        "pid": os.getpid(),
        "harness_sha256": script_hash,
        "started_at": utcnow(),
        "process_running": True,
    }
    with store.campaign_lock(mission, scope="mission-supervisor"):
        store.append(meta["task_id"], "MISSION_SUPERVISOR_ATTACHED", status)
        deadline = time.monotonic() + seconds
        previous = None
        try:
            while time.monotonic() < deadline:
                meta = json.loads(meta_path.read_text())
                if process_matches(meta):
                    status.update(status="SUPERVISING", runner_pid=meta["pid"])
                else:
                    worker = json.loads(Path(meta["state"]).read_text())
                    if worker["status"] in {"CLOSED", "PAUSED", "CANCELLED"}:
                        status.update(status=worker["status"])
                        return
                    if worker["status"] == "RUNNER_LIMIT":
                        status.update(
                            status="RESUMED_RUNNER_LIMIT",
                            runner_pid=restart(meta_path, store)["pid"],
                        )
                    elif worker["status"] == "NEEDS_RECONCILIATION":
                        recovery = reconcile_known_read(store, mission)
                        status.update(status=recovery["status"], recovery=recovery)
                        if recovery["status"] == "RECOVERED":
                            status["runner_pid"] = restart(meta_path, store)["pid"]
                    else:
                        status.update(
                            status="NEEDS_ATTENTION",
                            reason="unexpected runner exit; no blind restart",
                        )
                status.update(updated_at=utcnow())
                checkpoint(state_path, status)
                signature = (status["status"], status.get("runner_pid"))
                if signature != previous:
                    store.append(meta["task_id"], "MISSION_SUPERVISOR_STATUS", status)
                    previous = signature
                time.sleep(30 if status["status"] == "SUPERVISING" else 120)
            status.update(status="SUPERVISOR_LIMIT")
        finally:
            status.update(process_running=False, stopped_at=utcnow())
            checkpoint(state_path, status)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--wall-seconds", type=float, default=86400)
    args = parser.parse_args()
    supervise(args.process, args.state, seconds=args.wall_seconds)
