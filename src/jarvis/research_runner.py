"""Finite CLI coordinator; unknown outcomes stop instead of resubmitting."""

import time
from typing import Any

from jarvis.contracts import AdmissionError
from jarvis.research_program import ResearchProgram
from jarvis.storage import Store


def run_program(
    store: Store,
    engine: ResearchProgram,
    program: str,
    *,
    max_steps: int = 100,
    wall_seconds: int = 1800,
    poll_seconds: int = 45,
) -> dict[str, Any]:
    if not 1 <= max_steps <= 1000 or not 1 <= wall_seconds <= 14400 or not 1 <= poll_seconds <= 60:
        raise AdmissionError("runner needs finite steps/wall time and 1..60 second poll interval")
    started = time.monotonic()
    value: dict[str, Any] = {"status": "TIME_LIMIT"}
    for step in range(max_steps):
        if time.monotonic() - started >= wall_seconds:
            break
        value = engine.advance(program)
        store.append(
            program,
            "RESEARCH_RUNNER_STEP",
            {
                "status": value["status"],
                "step": step + 1,
                "elapsed_seconds": time.monotonic() - started,
            },
        )
        if value["status"] in {
            "CLOSED",
            "PAUSED",
            "CANCELLED",
            "AWAITING_SUBMISSION_AUTHORIZATION",
            "SUBMISSION_LIMIT",
            "NEEDS_RECONCILIATION",
            "CRITIC_NEEDS_ATTENTION",
            "BUDGET_EXHAUSTED",
            "BASELINE_NOT_VERIFIED",
            "INVALID_RESULT",
            "FAILED",
            "REJECTED",
        }:
            return value
        if value["status"] in {"WAITING_FOR_IRIS", "ADOPTED"}:
            time.sleep(min(poll_seconds, max(0, wall_seconds - (time.monotonic() - started))))
    return {
        "program_id": program,
        "status": "RUNNER_LIMIT",
        "last_progress": value,
        "resumable": True,
    }
