"""Receipted local operations and background action execution.

Local, side-effect-free operations (sandbox runs, local training, local scoring) may be
re-run after an interrupted process; the interrupted attempt is settled at its full
reservation and kept in the ledger. Paid provider calls never take this path.
"""

import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from jarvis.contracts import Cost, IntegrityError, JarvisError
from jarvis.identity import digest
from jarvis.storage import Store


def _payloads(store: Store, owner: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in store.events(owner) if e["event_type"] == kind]


def local_job(
    store: Store,
    owner: str,
    key: str,
    request: dict[str, Any],
    function: Callable[[], Any],
    *,
    reserved: Cost | None = None,
) -> dict[str, Any]:
    """Run once per key; returns {"value", "error", "cost"}; errors are recorded, not raised."""
    reserved = reserved or Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=120)
    with store.campaign_lock(owner, scope="local-job", blocking=True):
        done = [p for p in _payloads(store, owner, "LOCAL_JOB_COMPLETED") if p["key"] == key]
        if done:
            return dict(store.artifacts.get(done[-1]["receipt_ref"])["outcome"])
        attempts = [p for p in _payloads(store, owner, "LOCAL_JOB_INTERRUPTED") if p["key"] == key]
        envelope = {
            "kind": "LocalJob",
            "key": key,
            "attempt": len(attempts),
            "request": request,
            "world_id": store.manifest(owner)["world_id"],
        }
        jobs = [j for j in store.jobs(owner) if j["request_ref"] == digest(envelope)]
        if jobs and not jobs[0]["finalized"] and store.read_receipt(jobs[0]["id"]) is None:
            if jobs[0]["status"] == "RUNNING":
                # A previous process died mid-run: settle conservatively, then retry.
                store.write_receipt(
                    jobs[0]["id"],
                    {
                        "job_id": jobs[0]["id"],
                        "request_ref": jobs[0]["request_ref"],
                        "world_id": envelope["world_id"],
                        "outcome": {"value": None, "error": "INTERRUPTED", "cost": None},
                        "cost": Cost.model_validate_json(jobs[0]["reservation"]).model_dump(),
                    },
                )
                store.finish_auxiliary_job(jobs[0]["id"], "LOCAL_JOB_INTERRUPTED", {"key": key})
                envelope["attempt"] += 1
                jobs = []
        if not jobs:
            job_id = store.reserve(owner, envelope, reserved)
        else:
            job_id = jobs[0]["id"]
        job = next(j for j in store.jobs(owner) if j["id"] == job_id)
        if job["status"] == "QUEUED":
            store.start_job(job_id)
    receipt = store.read_receipt(job_id)
    if receipt is None:
        started = time.monotonic()
        try:
            value, error = function(), None
        except Exception as exc:  # recorded as a charged negative outcome
            value, error = None, type(exc).__name__ + ": " + str(exc)[:1500]
        elapsed = time.monotonic() - started
        cost = Cost(
            actions=1,
            tool_calls=1,
            compute_units=max(1.0, elapsed / 60),
            wall_seconds=elapsed,
        )
        outcome = {"value": value, "error": error, "cost": cost.model_dump()}
        receipt = {
            "job_id": job_id,
            "request_ref": digest(envelope),
            "world_id": envelope["world_id"],
            "outcome": outcome,
            "cost": cost.model_dump(),
        }
        store.write_receipt(job_id, receipt)
    store.finish_auxiliary_job(
        job_id, "LOCAL_JOB_COMPLETED", {"key": key, "error": receipt["outcome"]["error"]}
    )
    return dict(receipt["outcome"])


class ProviderUnavailable(JarvisError):
    """The provider refused service (usage or rate limit); resume later, nothing is lost."""


def is_quota_refusal(exc: BaseException) -> bool:
    return "PROVIDER_QUOTA_EXHAUSTED" in str(exc)


ENDED_PROVIDER_ERRORS = (
    "PROVIDER_TIMEOUT",
    "PROVIDER_INCOMPLETE",
    "USAGE_UNAVAILABLE",
    "PROVIDER_INTERRUPTED",
)


def settle_ended_provider_calls(store: Store, owner: str) -> int:
    """Settle provider calls that provably ended without usage; returns how many.

    The provider process was killed or exited (its receipt records why), so nothing more
    can be charged; the reservation is an upper bound. Calls without a receipt stay
    unknown and keep the campaign in NEEDS_RECONCILIATION.
    """
    settled = 0
    for job in store.jobs(owner):
        if job["finalized"]:
            continue
        receipt = store.read_receipt(job["id"])
        if receipt is None or receipt.get("cost") is not None:
            continue
        if str(receipt.get("error") or "").startswith(ENDED_PROVIDER_ERRORS):
            store.settle_at_reservation(job["id"], str(receipt["error"]))
            settled += 1
    if settled and store.status(owner) == "NEEDS_RECONCILIATION":
        if not any(
            not j["finalized"] and store.read_receipt(j["id"]) is None and j["status"] == "RUNNING"
            for j in store.jobs(owner)
        ):
            store.set_status(owner, "RUNNING")
    return settled


class ActionRunner:
    """Executes launched actions in background threads of the coordinating process."""

    def __init__(self, workers: int = 4):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="jarvis-action")
        self.futures: dict[str, Future[None]] = {}
        self.lock = threading.Lock()

    def submit(self, child: str, function: Callable[[], None]) -> None:
        with self.lock:
            if child in self.futures and not self.futures[child].done():
                return
            self.futures[child] = self.pool.submit(function)

    def running(self, child: str) -> bool:
        with self.lock:
            future = self.futures.get(child)
            return future is not None and not future.done()

    def failure(self, child: str) -> str | None:
        with self.lock:
            future = self.futures.get(child)
        if future is None or not future.done():
            return None
        exc = future.exception()
        return None if exc is None else "".join(traceback.format_exception(exc))[-4000:]

    def wait_any(self, children: list[str], timeout: float) -> None:
        """Return when any of `children` finishes (or none is running here)."""
        deadline = time.monotonic() + timeout
        watched = [c for c in children if self.running(c)]
        while watched and time.monotonic() < deadline:
            if any(not self.running(c) for c in watched):
                return
            time.sleep(0.05)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True)


def verify_local_jobs(store: Store, owner: str) -> int:
    jobs = {j["id"]: j for j in store.jobs(owner)}
    count = 0
    for kind in ("LOCAL_JOB_COMPLETED", "LOCAL_JOB_INTERRUPTED"):
        for p in _payloads(store, owner, kind):
            job = jobs[p["job_id"]]
            receipt = store.artifacts.get(p["receipt_ref"])
            if (
                not job["finalized"]
                or receipt["request_ref"] != job["request_ref"]
                or store.artifacts.get(job["request_ref"])["key"] != p["key"]
                or Cost.model_validate(receipt["cost"]) != Cost.model_validate_json(job["actual"])
            ):
                raise IntegrityError("local job receipt mismatch")
            count += 1
    return count
