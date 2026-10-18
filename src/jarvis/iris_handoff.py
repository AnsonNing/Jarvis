"""Quiescent successor admission: never interrupts or upgrades an active runtime."""

import fcntl
import json
from pathlib import Path
from typing import Any

from jarvis.contracts import IntegrityError
from jarvis.iris_contracts import IrisEnvironment
from jarvis.research_program import program_report
from jarvis.storage import Store


def predecessor_status(
    store: Store, path: Path, *, lease_held: bool = False, successor: str | None = None
) -> dict[str, Any]:
    if not path.is_file():
        raise IntegrityError("predecessor state is missing; do not create a parallel study")
    with path.with_suffix(".lock").open("a") as lease:
        if not lease_held:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {
                    "status": "WAITING_FOR_PREDECESSOR",
                    "reason": "original watcher retains its lease",
                }
        state = json.loads(path.read_text())
        if state["status"] in {
            "NEEDS_RECONCILIATION",
            "TRACK_NEEDS_ATTENTION",
            "MODEL_ACCEPTANCE_FAILED",
            "PAUSED",
            "CANCELLED",
        }:
            return {"status": "PREDECESSOR_NEEDS_ATTENTION", "predecessor_status": state["status"]}
        owner = state.get("study_id")
        if owner is None:
            return {"status": "READY", "predecessor_state": str(path), "queues": []}
        if store.status(owner) == "NEEDS_RECONCILIATION":
            return {"status": "PREDECESSOR_NEEDS_ATTENTION", "predecessor_study": owner}
        linked = successor and any(
            e["event_type"] == "IRIS_PREDECESSOR_ADMITTED"
            and e["payload"].get("predecessor_study") == owner
            for e in store.events(successor)
        )
        control = store.get_control(owner)
        if control == "CANCELLED" or (control == "PAUSED" and not linked):
            return {"status": "PREDECESSOR_NEEDS_ATTENTION", "predecessor_control": control}
        programs = [
            e["payload"]["program_id"]
            for e in store.events(owner)
            if e["event_type"] == "IRIS_PROGRAM_REGISTERED"
        ]
        queues = [
            e["payload"]["queue_id"]
            for pid in programs
            for e in store.events(pid)
            if e["event_type"] == "RESEARCH_QUEUE_ATTACHED"
        ]
        identifiers = [owner, *programs, *queues]
        if any(not j["finalized"] for cid in identifiers for j in store.jobs(cid)):
            return {"status": "WAITING_FOR_PREDECESSOR_SETTLEMENT", "predecessor_study": owner}
        for pid in programs:
            program_report(store, pid)
        return {
            "status": "READY",
            "predecessor_study": owner,
            "predecessor_state": str(path),
            "queues": queues,
        }


def compatible_history(a: IrisEnvironment, b: IrisEnvironment) -> bool:
    """Capability expansion may retain evidence; source/model/period stay fixed."""
    return all(
        getattr(a, k) == getattr(b, k)
        for k in [
            "branch",
            "template_sha256",
            "model_lineage",
            "side",
            "period",
            "evaluator_sha256",
            "result_origin",
        ]
    )


def with_predecessor(store: Store, spec: Any, predecessor: dict[str, Any]) -> Any:
    tracks = []
    for track in spec.tracks:
        queues = {}
        for phase in ["smoke", "development"]:
            q = getattr(track, phase)
            prior = [
                qid
                for qid in predecessor["queues"]
                if compatible_history(
                    IrisEnvironment.model_validate(store.manifest(qid)["spec"]["environment"]),
                    q.environment,
                )
            ]
            queues[phase] = q.model_copy(
                update={"prior_queues": tuple(dict.fromkeys([*q.prior_queues, *prior]))[-16:]}
            )
        tracks.append(track.model_copy(update=queues))
    return spec.model_copy(update={"tracks": tuple(tracks)})


def record_handoff(store: Store, successor: str, predecessor: dict[str, Any]) -> None:
    prior = predecessor.get("predecessor_study")
    if not any(e["event_type"] == "IRIS_PREDECESSOR_ADMITTED" for e in store.events(successor)):
        with store.transaction() as conn:
            if prior:
                conn.execute(
                    "INSERT INTO controls VALUES(?,?) ON CONFLICT(campaign_id) DO UPDATE SET command=?",
                    (prior, "PAUSED", "PAUSED"),
                )
                store._append(conn, prior, "CONTROL", {"command": "PAUSED", "successor": successor})
            store._append(
                conn,
                successor,
                "IRIS_PREDECESSOR_ADMITTED",
                {
                    **predecessor,
                    "scope": "new capability scope; prior evidence retained; no active job migrated",
                },
            )
