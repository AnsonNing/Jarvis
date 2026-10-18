import fcntl
import json

import pytest
from test_iris_continuous import create_study
from test_iris_queue import iris_environment as iris_environment

from jarvis.contracts import IntegrityError
from jarvis.iris_continuous import IrisContinuousSpec
from jarvis.iris_handoff import predecessor_status, record_handoff, with_predecessor
from jarvis.iris_watch import watch_research


def test_active_predecessor_blocks_successor_without_any_external_operation(
    tmp_path, iris_environment, monkeypatch
):
    store, engine, owner, backend, provider = create_study(tmp_path / "store", iris_environment)
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"status": "WAITING_FOR_MODEL_ACCEPTANCE"}))
    monkeypatch.setattr("jarvis.iris_watch.time.sleep", lambda _: None)
    with prior.with_suffix(".lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert predecessor_status(store, prior)["status"] == "WAITING_FOR_PREDECESSOR"
        result = watch_research(
            store,
            tmp_path,
            tmp_path / "python",
            "Fixture owner scope",
            tmp_path / "new.json",
            engine=engine,
            predecessor_state=prior,
            max_steps=1,
        )
    assert result["status"] == "WAITING_FOR_PREDECESSOR"
    assert not backend.submissions and provider.calls == 0


def test_predecessor_unknown_pending_and_recorded_stop_are_not_bypassed(tmp_path, iris_environment):
    store, engine, owner, backend, provider = create_study(tmp_path / "store", iris_environment)
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"study_id": owner, "status": "WAITING_FOR_IRIS"}))
    engine.refresh_quota(owner, [])
    engine.advance(owner)
    assert predecessor_status(store, prior)["status"] == "WAITING_FOR_PREDECESSOR_SETTLEMENT"
    store.set_status(owner, "NEEDS_RECONCILIATION")
    assert predecessor_status(store, prior)["status"] == "PREDECESSOR_NEEDS_ATTENTION"
    prior.write_text(json.dumps({"status": "CANCELLED"}))
    assert predecessor_status(store, prior)["status"] == "PREDECESSOR_NEEDS_ATTENTION"
    with pytest.raises(IntegrityError):
        predecessor_status(store, tmp_path / "absent.json")


def test_quiescent_handoff_retains_prior_histories_and_same_shared_quota(
    tmp_path, iris_environment
):
    store, engine, owner, backend, provider = create_study(tmp_path / "store", iris_environment)
    engine.refresh_quota(owner, [])
    for _ in range(130):
        if engine.advance(owner)["status"] == "CLOSED":
            break
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"study_id": owner, "status": "CLOSED"}))
    admitted = predecessor_status(store, prior)
    assert admitted["status"] == "READY" and admitted["queues"]
    spec = IrisContinuousSpec.model_validate(store.manifest(owner)["spec"])
    expanded = with_predecessor(store, spec, admitted)
    assert expanded.tracks[0].development.prior_queues
    assert expanded.quota_policy == spec.quota_policy
    new = engine.create(expanded)
    store.control(owner, "PAUSED")
    assert predecessor_status(store, prior)["status"] == "PREDECESSOR_NEEDS_ATTENTION"
    store.control(owner, "RUN")
    record_handoff(store, new, admitted)
    record_handoff(store, new, admitted)
    assert predecessor_status(store, prior)["status"] == "PREDECESSOR_NEEDS_ATTENTION"
    assert predecessor_status(store, prior, successor=new)["status"] == "READY"
    assert store.get_control(owner) == "PAUSED"
    assert (
        len([e for e in store.events(new) if e["event_type"] == "IRIS_PREDECESSOR_ADMITTED"]) == 1
    )


def test_successor_persists_identity_and_holds_predecessor_lease_before_dispatch(
    tmp_path, iris_environment, monkeypatch
):
    store, engine, unused, backend, provider = create_study(tmp_path / "store", iris_environment)
    spec = IrisContinuousSpec.model_validate(store.manifest(unused)["spec"])
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"status": "WAITING_FOR_MODEL_ACCEPTANCE"}))
    new = tmp_path / "new.json"
    monkeypatch.setattr("jarvis.iris_watch.model_status", lambda _: {"status": "READY"})
    monkeypatch.setattr("jarvis.iris_watch.prepare_continuous", lambda *_, **__: spec)
    monkeypatch.setattr("jarvis.iris_watch.time.sleep", lambda _: None)
    submit = backend.submit

    def checked_submit(*args):
        assert json.loads(new.read_text())["study_id"]
        with old.with_suffix(".lock").open("a") as lease:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return submit(*args)

    monkeypatch.setattr(backend, "submit", checked_submit)
    result = watch_research(
        store,
        tmp_path,
        tmp_path / "python",
        spec.owner_instruction,
        new,
        engine=engine,
        predecessor_state=old,
        max_steps=150,
    )
    assert result["status"] == "CLOSED" and len(backend.submissions) == 13
