import json
import multiprocessing
import sqlite3

import pytest

from jarvis.contracts import Budget, BudgetExceeded, Cost, IntegrityError
from jarvis.storage import Store


def manifest(**budget):
    return {"task": {"budget": Budget(**budget).model_dump()}, "world_id": "world-test"}


def append_worker(root, campaign, count):
    store = Store(root)
    for i in range(count):
        store.append(campaign, "CONCURRENT", {"worker_step": i})


def reserve_worker(root, campaign, queue):
    try:
        Store(root).reserve(
            campaign, {"caller": "race"}, Cost(actions=1, tool_calls=1, compute_units=1)
        )
        queue.put("reserved")
    except BudgetExceeded:
        queue.put("bounded")


def test_sealed_artifact_and_path_traversal(tmp_path):
    store = Store(tmp_path)
    ref = store.artifacts.put({"result": 42})
    assert store.artifacts.get(ref) == {"result": 42}
    store.artifacts.path(ref).write_text('{"result":43}')
    with pytest.raises(IntegrityError):
        store.artifacts.get(ref)
    with pytest.raises(IntegrityError):
        store.artifacts.path("../../secret")


def test_append_only_and_atomic_rollback(tmp_path):
    store = Store(tmp_path)
    campaign = store.create(manifest())
    with pytest.raises(RuntimeError), store.transaction() as conn:
        store._append(conn, campaign, "UNCOMMITTED", {"score": 1})
        raise RuntimeError("crash before commit")
    assert len(store.events(campaign)) == 2
    with pytest.raises(sqlite3.IntegrityError), store.transaction() as conn:
        conn.execute("UPDATE events SET event_json='{}'")
    with pytest.raises(sqlite3.IntegrityError), store.transaction() as conn:
        conn.execute("DELETE FROM campaigns")


def test_two_process_journal_append(tmp_path):
    store = Store(tmp_path)
    campaign = store.create(manifest())
    ctx = multiprocessing.get_context("spawn")
    workers = [ctx.Process(target=append_worker, args=(tmp_path, campaign, 8)) for _ in range(2)]
    for p in workers:
        p.start()
    for p in workers:
        p.join(20)
        assert p.exitcode == 0
    assert [e["sequence"] for e in store.events(campaign)] == list(range(1, 19))


def test_parallel_reservations_do_not_oversubscribe(tmp_path):
    store = Store(tmp_path)
    campaign = store.create(manifest(max_actions=2, max_parallel_jobs=4))
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    workers = [
        ctx.Process(target=reserve_worker, args=(tmp_path, campaign, queue)) for _ in range(4)
    ]
    for p in workers:
        p.start()
    for p in workers:
        p.join(20)
        assert p.exitcode == 0
    assert sorted(queue.get(timeout=1) for _ in workers) == [
        "bounded",
        "bounded",
        "reserved",
        "reserved",
    ]
    assert store.usage(campaign)["actions"] == 2


def test_receipt_conflict_refused(tmp_path):
    store = Store(tmp_path)
    campaign = store.create(manifest())
    job = store.reserve(campaign, {}, Cost())
    store.write_receipt(job, {"score": 1})
    assert store.read_receipt(job) == {"score": 1}
    with pytest.raises(IntegrityError):
        store.write_receipt(job, {"score": 2})


def test_hash_chain_tamper_detected_even_without_trigger(tmp_path):
    store = Store(tmp_path)
    campaign = store.create(manifest())
    with store.transaction() as conn:
        conn.execute("DROP TRIGGER events_no_update")
        raw = json.loads(conn.execute("SELECT event_json FROM events LIMIT 1").fetchone()[0])
        raw["actor"] = "edited"
        conn.execute("UPDATE events SET event_json=? WHERE sequence=1", (json.dumps(raw),))
    with pytest.raises(IntegrityError):
        store.events(campaign)
