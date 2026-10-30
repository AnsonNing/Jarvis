"""Collaborative research planning against a fixed, explicitly synthetic objective."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.autonomous_research import AutonomousResearch
from jarvis.autonomy_cli import status
from jarvis.autonomy_contracts import (
    AutonomyContract,
    AutonomyDecision,
    AutonomySessionSpec,
    StopAction,
)
from jarvis.autonomy_report import deliverable, replay_session, verify_session
from jarvis.contracts import IntegrityError
from jarvis.identity import digest
from jarvis.planning_fixture import PlanningFixtureProvider, declared_world
from jarvis.research_memory import KnowledgeBase
from jarvis.storage import Store


def session(
    tmp_path: Path, mode: str = "alternative", *, consolidate: bool = False, max_decisions: int = 12
):
    store = Store(tmp_path / "store")
    world = declared_world(store, {})
    engine = AutonomousResearch(
        store,
        world["executors"],
        lambda cfg, role: PlanningFixtureProvider(role, mode=mode),
        consolidate=consolidate,
    )
    owner = engine.create(
        AutonomySessionSpec(
            contract_ref=world["contract_ref"],
            max_decisions=max_decisions,
            max_concurrent_actions=1,
        )
    )
    return store, engine, owner


def after_first_review(engine, owner):
    for _ in range(20):
        state = engine.state(owner)
        if 0 in state["reviews"]:
            return state
        engine.advance(owner)
        state = engine.state(owner)
        engine.runner.wait_any([state["launched"][i] for i in state["running"]], timeout=1)
    raise AssertionError("fixture did not finish the first review")


@pytest.mark.parametrize("mode", ["alternative", "premature_once", "single", "blocked"])
def test_fixed_synthetic_workflow_and_honest_stop(tmp_path, mode):
    store, engine, owner = session(tmp_path, mode)
    try:
        result = engine.run(owner, wall_seconds=30)
        assert result["status"] == "CLOSED"
        state = engine.state(owner)
        report = deliverable(store, owner)
        assert verify_session(store, owner)["validation"] == "VERIFIED"
        replay = replay_session(store, owner)
        assert replay["status"] == "COMPLETE" and replay["external_calls"] == 0
        assert report["next_research_directions"] == []
        assert all(
            store.artifacts.get(r)["result_origin"] == "SIMULATED_FIXTURE"
            for r in state["observations"]
        )
        board = report["planning"]["latest_review"]["review"]
        if mode == "blocked":
            assert board["stop_basis"] == "no_justified_next_step"
            assert board["directions"][1]["disposition"] == "blocked"
            assert "B" not in state["branches"]  # considered, never executed
            assert len(state["observations"]) == 2
            assert report["finalist"]["status"] == "NO_PASSING_CANDIDATE"
        else:
            assert board["stop_basis"] == "goal_met"
            assert report["finalist"]["status"] == "SELECTED"
            assert len(state["branches"]) == (1 if mode == "single" else 2)
            assert len(state["observations"]) == (2 if mode == "single" else 3)
        if mode == "premature_once":
            rejected = [
                store.artifacts.get(e["payload"]["answer_ref"])
                for e in store.events(owner)
                if e["event_type"] == "LAB_ROLE_REJECTED"
            ]
            assert len(rejected) == 1
            assert rejected[0]["action"]["scope"] == "session"
            assert any(
                d["disposition"] == "ready" for d in rejected[0]["planning_review"]["directions"]
            )
        facts = KnowledgeBase(store).facts(owner)
        assert facts["planning"]["latest_review"] == report["planning"]["latest_review"]
        assert "proposals/estimates" in report["planning"]["semantics"]
        assert "未執行方向沒有觀測績效" in report["markdown"]
    finally:
        engine.runner.shutdown()


def test_branch_failure_does_not_end_mission_or_silently_drop_alternatives(tmp_path):
    store, engine, owner = session(tmp_path)
    try:
        state = after_first_review(engine, owner)
        context = engine.director_context(owner, state)
        raw = PlanningFixtureProvider().director({**context, "context_id": digest(context)})
        assert raw["action"]["scope"] == "branch"
        engine.validate_decision(owner, AutonomyDecision.model_validate(raw), context)
        omitted = json.loads(json.dumps(raw))
        omitted["planning_review"]["directions"] = omitted["planning_review"]["directions"][:1]
        with pytest.raises(ValueError, match="silently drop"):
            engine.validate_decision(owner, AutonomyDecision.model_validate(omitted), context)
        premature = json.loads(json.dumps(raw))
        premature["action"]["scope"] = "session"
        premature["planning_review"]["stop_basis"] = "no_justified_next_step"
        with pytest.raises(ValueError, match="branch failure is not mission exhaustion"):
            engine.validate_decision(owner, AutonomyDecision.model_validate(premature), context)
        assert store.status(owner) != "CLOSED"
    finally:
        engine.runner.shutdown()


@pytest.mark.parametrize(
    "bad",
    ["missing", "future", "wrong_direction", "changed_hypothesis", "unavailable", "duplicate"],
)
def test_planning_visibility_capability_and_action_consistency(tmp_path, bad):
    store, engine, owner = session(tmp_path)
    try:
        context = engine.director_context(owner, engine.state(owner))
        raw = PlanningFixtureProvider().director({**context, "context_id": digest(context)})
        if bad == "missing":
            raw.pop("planning_review")
        elif bad == "future":
            raw["planning_review"]["directions"][0]["hypothesis"]["evidence_refs"] = [
                "sha256:" + "f" * 64
            ]
        elif bad == "wrong_direction":
            raw["planning_review"]["selected_direction_id"] = "B"
        elif bad == "changed_hypothesis":
            raw["planning_review"]["directions"][0]["hypothesis"]["prediction"] = "Changed meaning"
        elif bad == "unavailable":
            raw["planning_review"]["directions"][1]["next_action"] = "TRAIN_MODEL"
        else:
            raw["planning_review"]["directions"][1]["direction_id"] = "A"
        with pytest.raises((ValueError, ValidationError)):
            engine.validate_decision(owner, AutonomyDecision.model_validate(raw), context)
    finally:
        engine.runner.shutdown()


def test_owner_checkpoint_resumes_same_session_without_early_curated_snapshot(tmp_path):
    store, engine, owner = session(tmp_path, "checkpoint", consolidate=True)
    try:
        paused = engine.run(owner, wall_seconds=30)
        assert paused["status"] == "PAUSED" and paused["owner_question"]
        state = engine.state(owner)
        assert state["branches"]["A"]["status"] == "open"
        assert not any(e["event_type"] == "SESSION_STOPPED" for e in store.events(owner))
        assert KnowledgeBase(store).find("synthetic-planning") is None
        assert status(store, owner)["owner_checkpoint"]["has_owner_reply"] is False
        jobs = store.resource_totals_tree(owner)
        store.control(owner, "RUNNING")
        assert (
            engine.advance(owner)["status"] == "PAUSED"
        )  # resume alone does not fabricate a reply
        assert store.resource_totals_tree(owner) == jobs
        guidance = engine.advise(
            owner, "Continue B, preserving objective and evaluation", mode="redirect"
        )
        store.control(owner, "RUNNING")
        assert engine.run(owner, wall_seconds=30)["status"] == "CLOSED"
        assert engine.state(owner)["responses"][guidance]["adopted"] is True
        assert engine.state(owner)["owner_checkpoint"] is None
        kb = KnowledgeBase(store)
        assert kb.snapshot("synthetic-planning")["sessions"][0]["status"] == "CLOSED"
        assert verify_session(store, owner)["validation"] == "VERIFIED"
    finally:
        engine.runner.shutdown()


@pytest.mark.parametrize(
    "event_type,mode", [("BRANCH_CLOSED", "alternative"), ("CONTROL", "checkpoint")]
)
def test_interrupted_stop_finalization_recovers_without_repeating_tools(
    tmp_path, monkeypatch, event_type, mode
):
    store, engine, owner = session(tmp_path, mode)
    after_first_review(engine, owner)
    original_append, original_control = store.append, store.control
    failed = False

    def interrupted_append(cid, kind, payload):
        nonlocal failed
        if cid == owner and kind == event_type and not failed:
            failed = True
            raise RuntimeError("synthetic interruption after ACTION_COMPLETED")
        return original_append(cid, kind, payload)

    def interrupted_control(cid, command):
        nonlocal failed
        if cid == owner and event_type == "CONTROL" and not failed:
            failed = True
            raise RuntimeError("synthetic interruption before pause")
        return original_control(cid, command)

    monkeypatch.setattr(store, "append", interrupted_append)
    monkeypatch.setattr(store, "control", interrupted_control)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        engine.advance(owner)
    assert failed
    monkeypatch.setattr(store, "append", original_append)
    monkeypatch.setattr(store, "control", original_control)
    before = len(engine.state(owner)["observations"])
    recovered = engine.advance(owner)
    assert recovered["status"] == ("PAUSED" if mode == "checkpoint" else "ACTION_COMPLETED")
    assert len(engine.state(owner)["observations"]) == before == 2
    if mode == "checkpoint":
        engine.advise(owner, "Continue B")
        store.control(owner, "RUNNING")
    assert engine.run(owner, wall_seconds=30)["status"] == "CLOSED"
    assert len(engine.state(owner)["observations"]) == 3
    assert verify_session(store, owner)["validation"] == "VERIFIED"
    engine.runner.shutdown()


def test_audit_rejects_planning_context_from_future_state(tmp_path):
    store, engine, owner = session(tmp_path)
    try:
        context = engine.director_context(owner, engine.state(owner))
        raw = PlanningFixtureProvider().director({**context, "context_id": digest(context)})
        # Admission rejects unrecorded planning state even if cited table refs are unchanged.
        context["research_plan"]["planning"]["latest_review"] = {
            "index": 9,
            "review": raw["planning_review"],
        }
        with pytest.raises(ValueError, match="decision-time"):
            engine.validate_decision(owner, AutonomyDecision.model_validate(raw), context)
    finally:
        engine.runner.shutdown()


def test_legacy_shapes_and_checkpoint_opt_in(tmp_path):
    store, engine, owner = session(tmp_path)
    try:
        contract = store.artifacts.get(engine.spec(owner).contract_ref)
        contract.pop("planning")
        legacy = AutonomyContract.model_validate(contract)
        assert legacy.model_dump(mode="json") == contract
        context = engine.director_context(owner, engine.state(owner))
        raw = PlanningFixtureProvider().director({**context, "context_id": digest(context)})
        raw.pop("planning_review")
        decision = AutonomyDecision.model_validate(raw)
        assert "planning_review" not in decision.model_dump()
        serialized = decision.model_dump(mode="json")
        assert digest(AutonomyDecision.model_validate(serialized)) == digest(serialized)
        with pytest.raises(ValidationError, match="closures"):
            StopAction.model_validate(
                {
                    **{
                        k: v
                        for k, v in raw["action"].items()
                        if k not in {"kind", "design", "experiments"}
                    },
                    "kind": "STOP",
                    "scope": "session",
                    "conclusion": "negative",
                    "closures": [],
                }
            )
    finally:
        engine.runner.shutdown()


def test_offline_audit_rechecks_recorded_planning(tmp_path, monkeypatch):
    from jarvis import autonomy_planning

    store, engine, owner = session(tmp_path)
    try:
        assert engine.run(owner, wall_seconds=30)["status"] == "CLOSED"
        original = autonomy_planning.validate_planning
        seen = []

        def checked(s, contract, state, answer, context):
            seen.append(len(state["decisions"]))
            return original(s, contract, state, answer, context)

        monkeypatch.setattr(autonomy_planning, "validate_planning", checked)
        assert verify_session(store, owner)["validation"] == "VERIFIED"
        assert seen == list(range(len(engine.state(owner)["decisions"])))

        def invalid(*args):
            raise ValueError("future planning evidence")

        monkeypatch.setattr(autonomy_planning, "validate_planning", invalid)
        with pytest.raises(IntegrityError, match="decision-time planning"):
            verify_session(store, owner)
    finally:
        engine.runner.shutdown()


def test_scoped_stop_can_defer_an_untried_direction_without_forcing_search(tmp_path):
    store, engine, owner = session(tmp_path)
    try:
        context = engine.director_context(owner, after_first_review(engine, owner))
        raw = PlanningFixtureProvider().director({**context, "context_id": digest(context)})
        raw["action"]["scope"] = "session"
        raw["planning_review"]["stop_basis"] = "no_justified_next_step"
        card = raw["planning_review"]["directions"][1]
        card["disposition"] = "deferred"
        card["reason"] = "B is untested; its additional cost is not justified in this bounded study"
        engine.validate_decision(owner, AutonomyDecision.model_validate(raw), context)
        assert len(engine.state(owner)["observations"]) == 2  # deferral is not an experiment
        raw["planning_review"]["stop_basis"] = "goal_met"
        with pytest.raises(ValueError, match="unsuccessful mission stop"):
            engine.validate_decision(owner, AutonomyDecision.model_validate(raw), context)
    finally:
        engine.runner.shutdown()


def test_budget_halt_retains_ready_directions_as_unfinished(tmp_path):
    store, engine, owner = session(tmp_path, max_decisions=1)
    try:
        assert engine.run(owner, wall_seconds=30)["status"] == "BUDGET_EXHAUSTED"
        report = deliverable(store, owner)
        assert report["director_stop"] is None
        assert (
            report["planning"]["latest_review"]["review"]["directions"][1]["disposition"] == "ready"
        )
        assert report["planning"]["latest_review"]["review"]["stop_basis"] is None
        assert verify_session(store, owner)["validation"] == "VERIFIED"
    finally:
        engine.runner.shutdown()


def test_method_quotas_do_not_block_an_owner_checkpoint(tmp_path):
    from jarvis.autonomy_policy import BranchShare, ResearchPolicy, enforce

    store, engine, owner = session(tmp_path, "checkpoint")
    try:
        context = engine.director_context(owner, after_first_review(engine, owner))
        raw = PlanningFixtureProvider(mode="checkpoint").director(
            {**context, "context_id": digest(context)}
        )
        decision = AutonomyDecision.model_validate(raw)
        engine.validate_decision(owner, decision, context)
        policy = ResearchPolicy(
            name="method",
            branch_shares=(BranchShare(kind="parameter", max_share=0.1),),
            min_analyses_before_stop=10,
            rationale="fixture",
            origin="declared_fixture",
        )
        enforce(
            policy, {"history": [{"kind": "BACKTEST", "branch_kind": "parameter"}] * 6}, decision
        )
    finally:
        engine.runner.shutdown()


def test_unattended_session_rejects_owner_checkpoints(tmp_path):
    store = Store(tmp_path / "store")
    world = declared_world(store, {})
    engine = AutonomousResearch(
        store,
        world["executors"],
        lambda cfg, role: PlanningFixtureProvider(role, mode="checkpoint"),
    )
    owner = engine.create(
        AutonomySessionSpec(
            contract_ref=world["contract_ref"],
            max_decisions=12,
            max_concurrent_actions=1,
            owner_checkpoints=False,
        )
    )
    try:
        result = engine.run(owner, wall_seconds=30)
        assert not result.get("owner_question")
        assert any(
            "owner is unattended" in json.dumps(e["payload"])
            for e in store.events(owner)
            if e["event_type"] == "LAB_ROLE_REJECTED"
        )
    finally:
        engine.runner.shutdown()


def test_a_new_session_remembers_a_predecessor_paused_at_a_checkpoint(tmp_path):
    from jarvis.autonomy_cli import memory_and_policy

    store, engine, owner = session(tmp_path, "checkpoint", consolidate=True)
    try:
        assert engine.run(owner, wall_seconds=30)["status"] == "PAUSED"
        assert KnowledgeBase(store).find("synthetic-planning") is None
        contract_ref = engine.spec(owner).contract_ref
        memory_ref, _ = memory_and_policy(store, contract_ref)
        sessions = KnowledgeBase(store).snapshot("synthetic-planning")["sessions"]
        assert [s["session"] for s in sessions] == [owner] and memory_ref
    finally:
        engine.runner.shutdown()
