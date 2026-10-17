import json

import pytest

from jarvis.contracts import JarvisError
from jarvis.discovery import Collector
from jarvis.dream import DreamEngine, development_summary, verify_round
from jarvis.exploration import fixture_proposals, incumbent
from jarvis.fresh import FreshRunner, verify_fresh
from jarvis.pool import DreamProtocol, PoolBuilder, verify_pool
from jarvis.storage import Store
from jarvis.suite import Fixture, SuiteAdapter


@pytest.fixture
def pool(tmp_path):
    store = Store(tmp_path)
    builder = PoolBuilder(store)
    return store, builder.run(builder.create())


def test_full_dream_freeze_heldout_fresh_and_idempotent_resume(pool, monkeypatch):
    store, ref = pool
    engine = DreamEngine(store)
    rid = engine.create(ref)
    with pytest.raises(JarvisError, match="finalist"):
        engine.run(rid, fault="after_finalist")
    assert not any(e["event_type"] == "HELDOUT_REVEALED" for e in store.events(rid))
    report_ref = engine.run(rid)
    report = store.artifacts.get(report_ref)
    assert report["status"] == "APPROVED_FOR_RESEARCH"
    assert report["heldout"]["finalist"]["successes"] == 6
    assert report["heldout"]["incumbent"]["successes"] == 2
    assert report["fresh"]["finalist"]["successes"] == 4
    assert report["fresh"]["incumbent"]["successes"] == 0
    assert verify_round(store, rid)["status"] == "VERIFIED"

    def forbidden(*args, **kwargs):
        raise AssertionError("completed round resubmitted work")

    monkeypatch.setattr(SuiteAdapter, "execute", forbidden)
    assert engine.run(rid) == report_ref


def test_no_improvement_preserves_incumbent(pool):
    store, ref = pool
    current = fixture_proposals()[0]
    engine = DreamEngine(store)
    rid = engine.create(ref, current=current)
    report = store.artifacts.get(engine.run(rid))
    assert report["status"] == "NO_PROMOTION"
    assert report["active_policy_id"] == current.identity
    assert report["heldout"] is None and report["fresh"] is None


def test_real_fresh_regression_preserves_q0(tmp_path):
    store = Store(tmp_path)
    protocol = DreamProtocol(fresh=(Fixture(family="spurious_proxy", variant=0),))
    builder = PoolBuilder(store)
    ref = builder.run(builder.create(protocol))
    engine = DreamEngine(store)
    rid = engine.create(ref)
    report = store.artifacts.get(engine.run(rid))
    assert report["heldout"]["passed"]
    assert report["fresh"]["incumbent"]["successes"] == 1
    assert report["fresh"]["finalist"]["successes"] == 0
    assert report["status"] == "NO_PROMOTION"
    assert report["active_policy_id"] == incumbent().identity


@pytest.mark.parametrize("fault", ["after_receipt", "after_freeze", "formal_after_receipt"])
def test_fresh_recovery_adopts_receipt_once(tmp_path, monkeypatch, fault):
    store = Store(tmp_path)
    cid = Collector(store).create(
        Fixture(family="root_finding", variant=0), policy=fixture_proposals()[0]
    )
    original = SuiteAdapter.execute
    calls = []

    def tracked(self, name, **kwargs):
        calls.append((name, kwargs.get("formal", False)))
        return original(self, name, **kwargs)

    monkeypatch.setattr(SuiteAdapter, "execute", tracked)
    with pytest.raises(JarvisError):
        FreshRunner(store).run(cid, fault=fault)
    ref = FreshRunner(store).run(cid)
    assert verify_fresh(store, ref)["success"]
    assert len(calls) == 4
    assert len(store.jobs(cid)) == 4


def test_policy_developer_only_receives_development_and_actual_usage(pool):
    store, ref = pool

    class Developer:
        def identity(self):
            return {"provider": "test-only-scripted"}

        def complete(self, prompt, schema, directory):
            payload = json.loads(prompt.split("\n", 1)[1])
            text = json.dumps(payload)
            for family in (
                "allocation",
                "robust_location",
                "sufficient",
                "root_finding",
                "matrix_projection",
            ):
                assert '"family": "' + family + '"' not in text
            answer = {
                "kind": "PolicyProposals",
                "development_ref": payload["development_ref"],
                "proposals": [
                    p.model_copy(update={"origin": "agent_proposed"}).model_dump(mode="json")
                    for p in fixture_proposals()[:2]
                ],
            }
            from jarvis.contracts import Cost

            return {
                "answer": answer,
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "cost": Cost(llm_tokens=150).model_dump(mode="json"),
                "error": None,
            }

    engine = DreamEngine(store, Developer())
    rid = engine.create(ref, developer="codex")
    report = store.artifacts.get(engine.run(rid))
    assert report["status"] == "APPROVED_FOR_RESEARCH"
    assert report["dreaming_usage"]["actual"]["llm_tokens"] == 150
    assert not any(report["dreaming_usage"]["pending"].values())
    pool_value = verify_pool(store, ref)
    assert development_summary(store, pool_value, incumbent())["scope"] == "development_only"
