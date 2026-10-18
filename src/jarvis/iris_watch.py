"""Persistent model-admission watcher and single rolling research coordinator."""

import fcntl
import json
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from jarvis.campaign import source_identity
from jarvis.contracts import AdmissionError, IntegrityError, JarvisError
from jarvis.identity import canonical, digest
from jarvis.iris_admission import model_status
from jarvis.iris_continuous import IrisContinuousStudy, continuous_report, prepare_continuous
from jarvis.storage import Store


def write_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical(value) + b"\n")
    temporary.replace(path)


def watch_research(
    store: Store,
    upstream: Path,
    python: Path,
    owner_instruction: str,
    state_path: Path,
    *,
    wall_seconds: int = 7200,
    poll_seconds: int = 45,
    max_steps: int = 2000,
    engine: IrisContinuousStudy | None = None,
    design_root: Path | None = None,
    predecessor_state: Path | None = None,
) -> dict[str, Any]:
    if not 1 <= wall_seconds <= 14400 or not 1 <= poll_seconds <= 60 or not 1 <= max_steps <= 5000:
        raise AdmissionError("watcher requires finite invocation bounds and 1..60 second polling")
    state_path = state_path.resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity = digest(
        {
            "upstream": str(upstream.resolve()),
            "python": str(python.resolve()),
            "owner_instruction": owner_instruction,
            **({"design_root": str(design_root.resolve())} if design_root else {}),
            **(
                {"predecessor_state": str(predecessor_state.resolve())} if predecessor_state else {}
            ),
        }
    )
    with ExitStack() as predecessor_leases, state_path.with_suffix(".lock").open("a") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise JarvisError("research watcher already running; existing state retained") from exc
        state: dict[str, Any] = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {
                "kind": "IrisResearchWatch",
                "configuration_id": identity,
                "status": "WAITING_FOR_MODEL_ACCEPTANCE",
            }
        )
        if state["configuration_id"] != identity:
            raise IntegrityError(
                "watcher scope or owner instruction changed; preserve the existing study"
            )
        if state.get("study_id") and store.status(state["study_id"]) in {
            "CLOSED",
            "NEEDS_RECONCILIATION",
        }:
            return state
        if state.get("study_id") and state["source_identity"] != source_identity():
            raise IntegrityError(
                "active research runtime changed; reconcile existing study before execution"
            )
        engine = engine or IrisContinuousStudy(store)
        started = time.monotonic()
        last_quota_read = 0.0
        predecessor_locked = False
        for _ in range(max_steps):
            if time.monotonic() - started >= wall_seconds:
                break
            predecessor = state.get("predecessor")
            if predecessor_state and not predecessor_locked:
                from jarvis.iris_handoff import predecessor_status

                stream = predecessor_state.with_suffix(".lock").open("a")
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    stream.close()
                    predecessor = {"status": "WAITING_FOR_PREDECESSOR"}
                else:
                    predecessor = predecessor_status(
                        store, predecessor_state, lease_held=True, successor=state.get("study_id")
                    )
                    if predecessor["status"] == "READY":
                        predecessor_leases.enter_context(stream)
                        predecessor_locked = True
                    else:
                        stream.close()
                state["predecessor"] = predecessor
                if predecessor["status"] != "READY":
                    state["status"] = predecessor["status"]
                    write_state(state_path, state)
                    if predecessor["status"] == "PREDECESSOR_NEEDS_ATTENTION":
                        return state
                    time.sleep(
                        min(poll_seconds, max(0, wall_seconds - (time.monotonic() - started)))
                    )
                    continue
            if not state.get("study_id"):
                admission = model_status(upstream)
                state.update(
                    model_admission=admission,
                    source_identity=source_identity(),
                    elapsed_seconds=time.monotonic() - started,
                )
                if admission["status"] == "MODEL_ACCEPTANCE_FAILED":
                    state["status"] = "MODEL_ACCEPTANCE_FAILED"
                    write_state(state_path, state)
                    return state
                if admission["status"] != "READY":
                    write_state(state_path, state)
                    time.sleep(
                        min(poll_seconds, max(0, wall_seconds - (time.monotonic() - started)))
                    )
                    continue
                spec = prepare_continuous(
                    upstream, python, owner_instruction, design_root=design_root
                )
                if predecessor:
                    from jarvis.iris_handoff import with_predecessor

                    spec = with_predecessor(store, spec, predecessor)
                owner = engine.create(spec)
                state.update(study_id=owner, spec_ref=store.artifacts.put(spec), status="ADMITTED")
                write_state(state_path, state)  # No provider/backtest dispatch before durable ID.
            owner = state["study_id"]
            if predecessor and predecessor.get("status") == "READY":
                from jarvis.iris_handoff import record_handoff

                record_handoff(store, owner, predecessor)
            if time.monotonic() - last_quota_read >= 900 or last_quota_read == 0:
                state["quota_refresh"] = engine.refresh_quota(owner)
                last_quota_read = time.monotonic()
                write_state(state_path, state)
            try:
                progress = engine.advance(owner)
            except JarvisError as exc:
                state.update(status="NEEDS_RECONCILIATION", error_type=type(exc).__name__)
                write_state(state_path, state)
                raise
            state.update(
                status=progress["status"],
                progress=progress,
                elapsed_seconds=time.monotonic() - started,
            )
            write_state(state_path, state)
            if progress["status"] in {
                "CLOSED",
                "NEEDS_RECONCILIATION",
                "TRACK_NEEDS_ATTENTION",
                "PAUSED",
                "CANCELLED",
            }:
                report = continuous_report(store, owner)
                destination = state_path.with_name(state_path.stem + "-report.json")
                write_state(destination, report)
                state["report"] = str(destination)
                write_state(state_path, state)
                return state
            if progress["status"] in {
                "WAITING_FOR_IRIS",
                "WAITING_IRIS_SLOT",
                "WAITING_FOR_QUOTA",
                "QUOTA_REFRESH_REQUIRED",
                "QUOTA_UNAVAILABLE",
            }:
                time.sleep(min(poll_seconds, max(0, wall_seconds - (time.monotonic() - started))))
        state["invocation_status"] = "RUNNER_LIMIT"
        write_state(state_path, state)
        return state
