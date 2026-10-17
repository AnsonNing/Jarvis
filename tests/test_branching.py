import pytest
from pydantic import ValidationError

from jarvis.branch_replay import replay_tree
from jarvis.contracts import IntegrityError, JarvisError
from jarvis.discovery import Collector, verify_tree
from jarvis.exploration import ExplorationPolicy, Rule, fixture_proposals, incumbent
from jarvis.pool import DreamProtocol, PoolBuilder, verify_pool
from jarvis.storage import Store
from jarvis.suite import Fixture, SuiteAdapter


def test_closed_dsl_rejects_code_and_unsafe_predicates():
    with pytest.raises(ValidationError):
        Rule(when="__import__('os')", prefer=("correct",))
    with pytest.raises(ValidationError):
        ExplorationPolicy(
            name="bad", rules=(Rule(when="improved", prefer=("stop",)),), rationale="bad"
        )
    with pytest.raises(ValidationError):
        Rule(when="always", prefer=("shell",))


def test_real_sibling_edges_no_future_evidence_and_replay_no_execution(tmp_path, monkeypatch):
    store = Store(tmp_path)
    cid = Collector(store).create(Fixture(family="linear_bias", variant=0))
    ref = Collector(store).run(cid)
    tree = verify_tree(store, ref)
    siblings = {}
    for edge in tree.edges:
        siblings.setdefault(edge.before, []).append(edge)
    assert any(len(edges) > 1 for edges in siblings.values())
    assert len(store.jobs(cid)) == len(tree.edges)

    def forbidden(*args, **kwargs):
        raise AssertionError("replay executed a tool/evaluator/provider")

    monkeypatch.setattr(SuiteAdapter, "execute", forbidden)
    monkeypatch.setattr("jarvis.discovery.evaluate", forbidden)
    monkeypatch.setattr("jarvis.providers.CodexProvider.complete", forbidden)
    base = replay_tree(store, ref, incumbent())
    candidate = replay_tree(store, ref, fixture_proposals()[0])
    assert base["success"] is False
    assert candidate["success"] is True
    assert candidate["external_calls"] == 0
    assert candidate["cost"]["tool_calls"] == 3
    assert [x["action_id"] for x in candidate["trace"]] == ["inspect", "diagnose", "correct"]


def test_unknown_branch_is_not_scored(tmp_path):
    store = Store(tmp_path)
    cid = Collector(store).create(Fixture(family="linear_bias", variant=0), omit=("diagnose",))
    ref = Collector(store).run(cid)
    value = replay_tree(store, ref, fixture_proposals()[0])
    assert value["status"] == "INSUFFICIENT_COVERAGE"
    assert value["score"] is None and value["success"] is None


@pytest.mark.parametrize("fault", ["after_reserve", "after_receipt"])
def test_branch_recovery_never_duplicates_paid_invocation(tmp_path, monkeypatch, fault):
    store = Store(tmp_path)
    collector = Collector(store)
    cid = collector.create(Fixture(family="linear_bias", variant=0))
    seen = []
    original = SuiteAdapter.execute

    def counted(self, name, **kwargs):
        seen.append(name)
        return original(self, name, **kwargs)

    monkeypatch.setattr(SuiteAdapter, "execute", counted)
    with pytest.raises(JarvisError):
        collector.run(cid, fault=fault)
    ref = collector.run(cid)
    assert len(seen) == len(verify_tree(store, ref).edges)
    before = len(seen)
    assert collector.run(cid) == ref
    assert len(seen) == before


def test_unknown_started_collection_stops_without_retry(tmp_path):
    store = Store(tmp_path)
    collector = Collector(store)
    cid = collector.create(Fixture(family="linear_bias", variant=0))
    with pytest.raises(JarvisError):
        collector.run(cid, fault="after_start")
    with pytest.raises(JarvisError, match="reconciliation"):
        collector.run(cid)
    assert len(store.jobs(cid)) == 1


def test_family_overlap_rejected_before_collection():
    protocol = DreamProtocol()
    with pytest.raises(ValidationError, match="family overlap"):
        DreamProtocol(heldout=(*protocol.heldout, protocol.development[0]))


def test_frozen_pool_identity_cost_and_tamper(tmp_path):
    store = Store(tmp_path)
    builder = PoolBuilder(store)
    cid = builder.create()
    ref = builder.run(cid)
    pool = verify_pool(store, ref)
    assert len(pool.members) == 12
    assert len({m.fixture.family for m in pool.members}) == 6
    assert builder.run(cid) == ref
    artifact = store.artifacts.path(pool.members[0].tree_ref)
    artifact.write_text(artifact.read_text() + " ")
    with pytest.raises(IntegrityError):
        verify_pool(store, ref)
