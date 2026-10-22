import contextlib
import io
import json
from pathlib import Path

import pytest

from jarvis.autonomous_research import AutonomousResearch
from jarvis.autonomy_contracts import AutonomySessionSpec
from jarvis.autonomy_dream import AutonomyDream, AutonomyDreamSpec, DreamTask, PolicyRegistry
from jarvis.autonomy_policy import ResearchPolicy
from jarvis.autonomy_report import replay_session, verify_session
from jarvis.contracts import AdmissionError
from jarvis.quant_fixture import QuantFixtureProvider
from jarvis.quant_world import (
    QuantWorldFactory,
    analysis_environment,
    build_world,
    world_executors,
)
from jarvis.research_memory import KnowledgeBase, session_domain
from jarvis.storage import Store
from jarvis.workbench import Workbench

EXPLAIN = ResearchPolicy(
    name="explain",
    explain_gains_before_more_backtests=True,
    require_model_branch_before_stop=True,
    min_analyses_before_stop=1,
    max_values_per_setting=4,
    rationale="test",
    origin="declared_fixture",
)


@pytest.fixture(autouse=True)
def isolation(tmp_path):
    if Workbench(analysis_environment()).probe(tmp_path / "probe")["status"] != "PASS":
        pytest.skip("OS workbench isolation unavailable")


def provider(cfg, role):
    return QuantFixtureProvider(role)


def research(store, root, *, memory=True, policy_ref=None, seed=11, family="balanced"):
    """One finished session that starts from (and writes back to) the domain memory."""
    world = build_world(store, root, seed=seed, family=family, per_day=40)
    engine = AutonomousResearch(store, world_executors(world), provider, consolidate=True)
    domain = store.artifacts.get(world["contract_ref"])["domain"]
    owner = engine.create(
        AutonomySessionSpec(
            contract_ref=world["contract_ref"],
            memory_ref=KnowledgeBase(store).pin(domain) if memory else None,
            policy_ref=policy_ref or store.artifacts.put(EXPLAIN),
            max_decisions=30,
            max_concurrent_actions=1,
        )
    )
    result = engine.run(owner, wall_seconds=600)
    return owner, result, domain


def decisions(store, owner):
    return sum(e["event_type"] == "AUTONOMY_DECISION" for e in store.events(owner))


def test_memory_lessons_and_skills_carry_into_the_next_session(tmp_path):
    store = Store(tmp_path / "store")
    first, result, domain = research(store, tmp_path / "w1")
    assert result["status"] == "CLOSED"
    # Every ended run is written back: trusted facts plus curated lessons and promoted skills.
    assert result["memory"]["lessons"] >= 1 and result["memory"]["skills"] == 1
    kb = KnowledgeBase(store)
    snapshot = kb.snapshot(domain)
    assert [s["session"] for s in snapshot["sessions"]] == [first]
    assert snapshot["experiments"] and snapshot["candidates"]
    assert [s["name"] for s in snapshot["skills"]] == ["regime_threshold_scan"]
    skill = store.artifacts.get(snapshot["skills"][0]["ref"])
    assert skill["kind"] == "ResearchSkill" and skill["origin"]["session"] == first
    assert all(store.blobs.path(ref).exists() for ref in skill["files"].values())
    # Consolidation is idempotent and only admits ended sessions.
    assert (
        kb.consolidate(first)["facts_ref"] == kb.consolidated(kb.find(domain))[first]["facts_ref"]
    )

    second, result, _ = research(store, tmp_path / "w2", seed=12)
    assert result["status"] == "CLOSED"
    spec = AutonomySessionSpec.model_validate(store.manifest(second)["spec"])
    pinned = store.artifacts.get(spec.memory_ref)
    assert pinned["lessons"] and pinned["skills"][0]["name"] == "regime_threshold_scan"
    # Memory shortens the work: earlier experiments are reused, the skill is mounted and used.
    assert decisions(store, second) < decisions(store, first)
    used = {
        name
        for a in kb.facts(second)["analyses"]
        for name in store.artifacts.get(a["result_ref"])["skills_used"]
    }
    assert used == {"regime_threshold_scan"}
    # A session that started from memory still audits and replays on its own evidence log.
    assert verify_session(store, second)["validation"] == "VERIFIED"
    assert replay_session(store, second)["status"] == "COMPLETE"
    assert len(kb.snapshot(domain)["sessions"]) == 2


def test_running_session_is_not_consolidated(tmp_path):
    store = Store(tmp_path / "store")
    world = build_world(store, tmp_path / "w", per_day=40)
    engine = AutonomousResearch(store, world_executors(world), provider)
    owner = engine.create(AutonomySessionSpec(contract_ref=world["contract_ref"]))
    with pytest.raises(AdmissionError, match="consolidate after it ends"):
        KnowledgeBase(store).consolidate(owner)
    store.control(owner, "PAUSED")
    facts_only = KnowledgeBase(store).consolidate(owner)
    assert facts_only["lessons"] == [] and facts_only["curation_ref"] is None
    assert session_domain(store, owner) == store.artifacts.get(world["contract_ref"])["domain"]


def test_export_import_moves_knowledge_between_stores(tmp_path):
    source = Store(tmp_path / "a")
    _, _, domain = research(source, tmp_path / "w1")
    bundle = tmp_path / "bundle"
    exported = KnowledgeBase(source).export(domain, bundle)
    assert exported["sessions"] == 1 and exported["blobs"] >= 1
    target = Store(tmp_path / "b")
    kb = KnowledgeBase(target)
    assert kb.import_bundle(bundle) == {"domain": domain, "imported_sessions": 1}
    assert kb.import_bundle(bundle)["imported_sessions"] == 0
    original, copied = KnowledgeBase(source).snapshot(domain), kb.snapshot(domain)
    assert copied["lessons"] == original["lessons"] and copied["skills"] == original["skills"]
    # A new session in the target store mounts the imported skill files.
    pinned = target.artifacts.get(kb.pin(domain))
    skill = target.artifacts.get(pinned["skills"][0]["ref"])
    assert all(target.blobs.path(ref).exists() for ref in skill["files"].values())


def test_dream_learns_from_remembered_sessions_and_later_sessions_use_the_adoption(tmp_path):
    store = Store(tmp_path / "store")
    kb = KnowledgeBase(store)
    q0 = ResearchPolicy.model_validate(
        store.artifacts.get(PolicyRegistry(store).active(kb.registry("synthetic-quant-world")))
    )
    histories = []
    for i, (seed, family) in enumerate([(11, "balanced"), (12, "calm")]):
        owner, result, domain = research(
            store,
            tmp_path / f"w{i}",
            policy_ref=PolicyRegistry(store).active(kb.registry("synthetic-quant-world")),
            seed=seed,
            family=family,
        )
        assert result["status"] in {"CLOSED", "NEEDS_ATTENTION"}
        histories.append(owner)
    registry = kb.registry(domain)
    dream = AutonomyDream(store, QuantWorldFactory(per_day=40), provider)
    round_id = dream.create(
        AutonomyDreamSpec(
            registry=registry,
            development_sessions=tuple(histories),
            validation=(
                DreamTask(task_id="val1", family="balanced", seed=21),
                DreamTask(task_id="val2", family="calm", seed=22),
            ),
            canary=(DreamTask(task_id="can1", family="volatile", seed=31),),
            session_max_decisions=30,
        )
    )
    outcome = dream.run(round_id)
    assert outcome["outcome"] == "ADOPTED"
    active = PolicyRegistry(store).active(registry)
    assert active == outcome["policy_ref"]
    assert ResearchPolicy.model_validate(store.artifacts.get(active)) != q0
    # The next session in this domain starts from the adopted policy (as the CLI does).
    nxt, result, _ = research(store, tmp_path / "next", policy_ref=active, seed=13)
    assert result["status"] == "CLOSED"
    assert AutonomySessionSpec.model_validate(store.manifest(nxt)["spec"]).policy_ref == active


def test_dream_spec_needs_development_histories():
    with pytest.raises(ValueError):
        AutonomyDreamSpec(
            registry="r",
            validation=(DreamTask(task_id="b", family="balanced", seed=1),),
            canary=(DreamTask(task_id="c", family="calm", seed=2),),
        )


def test_cli_sessions_start_from_memory_and_adopted_policy(tmp_path):
    from jarvis.cli import parser, run

    store = tmp_path / "store"

    def call(*args):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            run(parser().parse_args(["--store", str(store), "autonomy", *args]))
        return json.loads(buffer.getvalue())

    def declaration(name, seed):
        path = tmp_path / f"{name}.yaml"
        path.write_text(
            "adapter: jarvis.quant_world:declared_world\n"
            f"adapter_args: {{root: {json.dumps(str(tmp_path / name))}, seed: {seed}}}\n"
            "agent: fixture\nmax_decisions: 30\nmax_concurrent_actions: 1\n"
        )
        return str(path)

    first = call("start", declaration("w1", 11))
    # The registry starts at Q0 (no analyses, so no skills); the run is still remembered.
    remembered = call("run", first["owner"], "--wall-seconds", "600")["memory"]
    assert remembered["session"] == first["owner"] and remembered["curation_ref"]
    shown = call("memory", "show", "synthetic-quant-world")
    assert shown["sessions"] == 1 and shown["experiments"] >= 1
    second = call("start", declaration("w2", 12))
    pinned = Store(store).artifacts.get(second["memory_ref"])
    assert pinned["sessions"][0]["session"] == first["owner"]
    assert second["policy_ref"] == PolicyRegistry(Store(store)).active(shown["registry"])
    # Facts-only consolidation without an agent is a no-op for already-remembered sessions.
    results = call("memory", "consolidate", "--agent", "none")["results"]
    assert {r["session"] for r in results} >= {first["owner"]}
    out = tmp_path / "export"
    assert call("memory", "export", "synthetic-quant-world", str(out))["sessions"] == 1
    assert Path(out / "knowledge.json").is_file()
