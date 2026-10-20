import importlib.util
from pathlib import Path

from test_research_recovery import mission_fixture

from jarvis.contracts import Cost
from jarvis.research_lab import payloads
from jarvis.research_queue import queue_tasks
from jarvis.storage import Store

spec = importlib.util.spec_from_file_location(
    "iris_read_supervisor", Path(__file__).parents[1] / "scripts/iris_mission_supervisor.py"
)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


def failed_read(store):
    engine, mission = mission_fixture(store)
    for _ in range(40):
        engine.advance(mission)
        waves = payloads(store, mission, "MISSION_SESSION_CREATED")
        session = waves[-1]["session"]
        if not payloads(store, session, "SESSION_QUOTA_FINISHED"):
            engine.sessions.refresh_quota(session, [])
        stages = payloads(store, session, "SESSION_STAGE_CREATED")
        if not stages:
            continue
        episode = stages[-1]["episode"]
        programs = payloads(store, episode, "LAB_PROGRAM_CREATED")
        if not programs:
            continue
        program = programs[-1]["program_id"]
        queues = payloads(store, program, "RESEARCH_QUEUE_ATTACHED")
        if not queues:
            continue
        queue = queues[-1]["queue_id"]
        active = [t for t in queue_tasks(store, queue) if t["status"] == "RUNNING"]
        if active:
            break
    assert len(active) == 1
    row = active[0]
    store.append(
        queue,
        "IRIS_COLLECTED",
        {
            "task_id": row["task_id"],
            "local_seconds": 1,
            "result_ref": store.artifacts.put(
                {"status": "UNKNOWN", "error_type": "SlackError", "request_id": row["request_id"]}
            ),
        },
    )
    for owner, status in [
        (queue, "NEEDS_RECONCILIATION"),
        (episode, "LAB_NEEDS_ATTENTION"),
        (session, "NEEDS_RECONCILIATION"),
        (mission, "NEEDS_RECONCILIATION"),
    ]:
        store.set_status(owner, status)
    error_ref = store.artifacts.put(
        {
            "type": "JarvisError",
            "traceback": "child research requires attention: NEEDS_RECONCILIATION",
        }
    )
    store.append(mission, "MISSION_RUNNER_FAILED", {"error_ref": error_ref})
    return engine, mission, queue, row


def test_known_read_recovers_same_request_without_any_submission(tmp_path):
    store = Store(tmp_path)
    engine, mission, queue, row = failed_read(store)
    dispatches = len(payloads(store, queue, "IRIS_DISPATCH_RESERVED"))
    before_calls = sum(
        len(payloads(store, m, "ROLE_COMPLETED")) for m in store.resource_members(mission)
    )
    result = supervisor.reconcile_known_read(store, mission, engine.sessions.lab.program.queue)
    assert result["status"] == "RECOVERED" and result["request_id"] == row["request_id"]
    assert len(payloads(store, queue, "IRIS_DISPATCH_RESERVED")) == dispatches
    assert (
        sum(len(payloads(store, m, "ROLE_COMPLETED")) for m in store.resource_members(mission))
        == before_calls
    )
    assert store.status(mission) == "READY"
    assert payloads(store, mission, "KNOWN_IRIS_READ_RECONCILED")


def test_unknown_provider_reservation_blocks_read_recovery(tmp_path):
    store = Store(tmp_path)
    engine, mission, queue, _ = failed_read(store)
    job = store.reserve(
        mission, {"role": "critic", "config": {}, "unknown": "fixture"}, Cost(llm_tokens=100)
    )
    store.start_job(job)
    before = store.events(queue)
    result = supervisor.reconcile_known_read(store, mission, engine.sessions.lab.program.queue)
    assert result["status"] == "NEEDS_ATTENTION" and "provider" in result["reason"]
    assert store.events(queue) == before and store.status(mission) == "NEEDS_RECONCILIATION"
    assert store.resource_totals_tree(mission)["pending"]["llm_tokens"] == 100


def test_owner_pause_cannot_be_cleared_by_supervisor(tmp_path):
    store = Store(tmp_path)
    engine, mission, queue, _ = failed_read(store)
    store.control(mission, "PAUSED")
    before = store.events(queue)
    result = supervisor.reconcile_known_read(store, mission, engine.sessions.lab.program.queue)
    assert result["status"] == "NEEDS_ATTENTION"
    assert store.get_control(mission) == "PAUSED" and store.events(queue) == before
