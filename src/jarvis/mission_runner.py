"""Single-owner runner with truthful atomic checkpoints and failure finalization."""

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

from jarvis.campaign import source_identity
from jarvis.iris_mission import IrisResearchMission
from jarvis.research_lab import payloads
from jarvis.research_notebook import export_notebook
from jarvis.storage import utcnow


def checkpoint(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def run_mission(
    engine: IrisResearchMission,
    mission: str,
    state: Path,
    *,
    notebook: Path,
    notebook_owner: str | None = None,
    poll_seconds: float = 20,
    max_seconds: float = 14400,
) -> dict[str, Any]:
    store = engine.store
    value: dict[str, Any] = {
        "mission": mission,
        "store": str(store.root),
        "pid": os.getpid(),
        "source_identity": source_identity(),
        "status": "RUNNING",
        "task_complete": False,
        "process_running": True,
        "started_at": utcnow(),
    }
    with store.campaign_lock(mission, scope="mission-runner"):
        deadline = time.monotonic() + max_seconds
        ticks, previous = 0, None
        try:
            while time.monotonic() < deadline:
                waves = payloads(store, mission, "MISSION_SESSION_CREATED")
                if waves and ticks % 6 == 0:
                    session = waves[-1]["session"]
                    engine.sessions.refresh_quota(session, engine.sessions.known_requests(session))
                result = engine.advance(mission)
                current = store.status(mission)
                terminal = current in {"CLOSED", "NEEDS_RECONCILIATION", "BUDGET_EXHAUSTED"}
                status = (
                    current
                    if terminal
                    else (
                        result["status"]
                        if result["status"].startswith(("WAITING", "QUOTA", "PAUSED", "CANCELLED"))
                        else "RUNNING"
                    )
                )
                value.update(
                    status=status,
                    task_complete=current == "CLOSED",
                    last_operation=result,
                    updated_at=utcnow(),
                )
                checkpoint(state, value)
                signature = (status, result.get("wave"), result.get("stage"), result.get("status"))
                if signature != previous:
                    store.append(
                        mission,
                        "MISSION_RUNNER_PROGRESS",
                        {
                            "status": status,
                            "operation": result["status"],
                            "wave": result.get("wave"),
                            "stage": result.get("stage"),
                        },
                    )
                    export_notebook(store, notebook_owner or mission, notebook)
                    previous = signature
                if terminal or status in {"PAUSED", "CANCELLED"}:
                    return value
                ticks += 1
                time.sleep(poll_seconds)
            value.update(status="RUNNER_LIMIT", task_complete=False, updated_at=utcnow())
            store.append(
                mission,
                "MISSION_RUNNER_LIMIT",
                {
                    "resumption": "same mission/store and saved arguments; no new world or duplicate jobs"
                },
            )
            return value
        except Exception as exc:
            error_ref = store.artifacts.put(
                {"type": type(exc).__name__, "traceback": traceback.format_exc()}
            )
            store.set_status(mission, "NEEDS_RECONCILIATION")
            store.append(mission, "MISSION_RUNNER_FAILED", {"error_ref": error_ref})
            value.update(
                status="NEEDS_RECONCILIATION",
                error_ref=error_ref,
                task_complete=False,
                updated_at=utcnow(),
            )
            raise
        finally:
            value.update(process_running=False, stopped_at=utcnow())
            checkpoint(state, value)
            export_notebook(store, notebook_owner or mission, notebook)
