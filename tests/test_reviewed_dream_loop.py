import json

import pytest

from jarvis.contracts import Budget, BudgetExceeded, Cost, IntegrityError, JarvisError
from jarvis.identity import digest
from jarvis.program_costs import compare_full_programs, full_program_summary
from jarvis.research_fixture import FixtureBackends
from jarvis.research_program import ResearchProgram, program_report
from jarvis.research_roles import RoleCaller
from jarvis.reviewed_dream import (
    ReviewedDreamLoop,
    ReviewedDreamSpec,
    reviewed_dream_report,
    verify_reviewed_dream,
)
from jarvis.reviewed_dream_fixture import (
    LoopCritic,
    LoopDeveloper,
    LoopDirector,
    fixture_spec,
    run_reviewed_dream_fixture,
)
from jarvis.reviewed_registry import ReviewedPolicyRegistry, verify_adoption_proof
from jarvis.storage import Store


@pytest.fixture(scope="module")
def complete_loop(tmp_path_factory):
    root = tmp_path_factory.mktemp("closed-reviewed-dream")
    store = Store(root)
    result = run_reviewed_dream_fixture(store)
    return store, result, reviewed_dream_report(store, result["study_id"])


def engine_for(store, *, director=None, developer=None):
    director = director or LoopDirector()
    critic, policy = LoopCritic(), developer or LoopDeveloper()
    backends = FixtureBackends()
    return ReviewedDreamLoop(
        store,
        provider_factory=lambda _: director,
        critic_provider_factory=lambda _: critic,
        policy_provider_factory=lambda _: policy,
        transport_factory=backends,
    ), backends


def test_complete_feedback_adopts_new_policy_and_rolls_back_on_measured_regression(complete_loop):
    store, acceptance, report = complete_loop
    assert acceptance["outcome"] == "ROLLED_BACK"
    assert acceptance["adopted_policies"] == ["fixture-exit-first", "Hermes-Q0"]
    assert acceptance["verification"]["next_cycles"] == 2
    assert acceptance["artificial_executions"] == 64
    assert acceptance["external_llm_calls"] == acceptance["remote_iris_submissions"] == 0
    assert acceptance["full_usage"]["actual"]["llm_tokens"] == 6220
    assert not any(acceptance["full_usage"]["pending"].values())
    fresh, canary = report["comparisons"]
    assert fresh["accepted"] and not canary["accepted"]
    for comparison in report["comparisons"]:
        arms = comparison["comparisons"][0]["arms"]
        assert arms["incumbent"]["budget"] == arms["candidate"]["budget"]
        for arm in arms.values():
            assert arm["complete"] and arm["within_budget"]
            assert arm["role_calls"] == {"direction": 2, "critic": 4}
            assert arm["usage"]["actual"]["llm_tokens"] == 520
            assert arm["usage"]["actual"]["tool_calls"] == 3
            assert arm["remote_compute_and_queue_seconds"] is None
    assert len(report["pool_expansion"][0]["history_refs"]) == 3
    assert report["next_cycle"][0]["old_validation_reused"] is False


def test_complete_report_and_policy_replay_are_zero_call_and_zero_new_job(
    complete_loop, monkeypatch
):
    store, acceptance, report = complete_loop
    owner = acceptance["study_id"]
    before = store.resource_totals_tree(owner)

    def forbidden(*args, **kwargs):
        pytest.fail("offline verifier executed an external operation")

    monkeypatch.setattr(RoleCaller, "call", forbidden)
    monkeypatch.setattr(LoopDirector, "complete", forbidden)
    monkeypatch.setattr(LoopCritic, "complete", forbidden)
    monkeypatch.setattr(LoopDeveloper, "complete", forbidden)
    monkeypatch.setattr("jarvis.research_queue.IrisResearchQueue.tick", forbidden)
    assert verify_reviewed_dream(store, owner)["external_calls"] == 0
    engine, _ = engine_for(store)
    assert engine.advance(owner)["outcome"] == "ROLLED_BACK"
    assert store.resource_totals_tree(owner) == before
    # New history includes unexecuted alternatives. The second screen reports
    # insufficient coverage, rather than inventing full-program outcomes.
    screens = [
        e["payload"] for e in store.events(owner) if e["event_type"] == "REVIEWED_DREAM_SCREENED"
    ]
    comparisons = store.artifacts.get(screens[1]["comparisons_ref"])
    assert any(not c["comparison"]["complete"] for c in comparisons)


def test_canary_success_retains_policy_and_continues_new_reviewed_programs(tmp_path):
    acceptance = run_reviewed_dream_fixture(Store(tmp_path), shifted_canary=False)
    assert acceptance["outcome"] == "APPROVED_FOR_RESEARCH"
    assert acceptance["adopted_policies"] == ["fixture-exit-first", "fixture-exit-first"]


def test_descendant_budget_is_shared_with_roles_and_keeps_unknown_reservations(tmp_path):
    store = Store(tmp_path)
    budget = Budget(max_actions=3, max_llm_tokens=100, max_parallel_jobs=3)
    root = store.create({"task": {"budget": budget.model_dump()}})
    child_budget = {
        "task": {
            "budget": Budget(max_actions=10, max_llm_tokens=1000, max_parallel_jobs=4).model_dump()
        },
        "budget_owner": root,
    }
    first, second = store.create(child_budget), store.create(child_budget)
    store.reserve(first, {"attempt": 1}, Cost(actions=2, llm_tokens=70))
    with pytest.raises(BudgetExceeded, match="shared program budget"):
        store.reserve(second, {"attempt": 2}, Cost(actions=2))
    with pytest.raises(BudgetExceeded):
        store.reserve(root, {"role": "critic"}, Cost(llm_tokens=40))
    assert store.resource_totals_tree(root)["pending"]["llm_tokens"] == 70
    store.reserve(second, {"attempt": 3}, Cost(actions=1, llm_tokens=30))
    assert store.resource_totals_tree(root)["pending"]["actions"] == 3


def test_full_comparison_rejects_unequal_budget_or_provider(complete_loop, monkeypatch):
    store, _, report = complete_loop
    pair = report["comparisons"][0]["pairs"][0]
    original = store.manifest

    def changed(owner):
        raw = original(owner)
        if owner == pair["candidate"]:
            raw = json.loads(json.dumps(raw))
            raw["spec"]["execution_budget"]["max_llm_tokens"] += 1
        return raw

    monkeypatch.setattr(store, "manifest", changed)
    with pytest.raises(IntegrityError, match="shared budget"):
        compare_full_programs(store, pair["incumbent"], pair["candidate"], strict=False)


def test_synthetic_policy_registry_cannot_be_used_by_observed_scope(complete_loop):
    store, result, report = complete_loop
    registry = report["continuation"]["policy_registry"]
    template = (
        ReviewedDreamSpec.model_validate(store.manifest(result["study_id"])["spec"])
        .fresh[0]
        .program
    )
    observed = template.model_copy(
        update={
            "queue": template.queue.model_copy(
                update={
                    "environment": template.queue.environment.model_copy(
                        update={"result_origin": "OBSERVED_IRIS_BACKTEST"}
                    )
                }
            )
        }
    )
    with pytest.raises(IntegrityError, match="scope"):
        ReviewedPolicyRegistry(store).active(registry, observed)


def test_promotion_proof_cannot_replace_frozen_candidate(complete_loop):
    store, _, report = complete_loop
    registry = report["continuation"]["policy_registry"]
    event = next(
        e for e in store.events(registry) if e["event_type"] == "REVIEWED_POLICY_ACTIVATED"
    )
    proof = store.artifacts.get(event["payload"]["proof_ref"])
    proof["candidate"]["name"] = "forged-winner"
    with pytest.raises(IntegrityError, match="finalist"):
        verify_adoption_proof(store, proof)


@pytest.mark.parametrize("fault", ["after_receipt", "after_start"])
def test_paid_role_recovery_inside_policy_controlled_program_never_retries_unknown(
    tmp_path, monkeypatch, fault
):
    store = Store(tmp_path)
    spec = fixture_spec(store).fresh[0].program
    engine, _ = engine_for(store)
    pid = engine.program.create(spec)
    original = RoleCaller.call
    injected = False

    def interrupted(self, owner, role, context, schema, **kwargs):
        nonlocal injected
        if role == "critic" and not injected:
            injected = True
            return original(self, owner, role, context, schema, fault=fault, **kwargs)
        return original(self, owner, role, context, schema, **kwargs)

    monkeypatch.setattr(RoleCaller, "call", interrupted)
    with pytest.raises(JarvisError, match="injected"):
        for _ in range(8):
            engine.program.advance(pid)
    if fault == "after_start":
        with pytest.raises(JarvisError, match="no provider resubmission"):
            engine.program.advance(pid)
        assert engine.program.advance(pid)["status"] == "NEEDS_RECONCILIATION"
        assert store.resource_totals_tree(pid)["pending"]["llm_tokens"] == 1000
    else:
        for _ in range(35):
            if engine.program.advance(pid)["status"] == "CLOSED":
                break
        summary = full_program_summary(store, pid)
        assert summary["role_calls"] == {"direction": 2, "critic": 4}
        assert summary["usage"]["actual"]["llm_tokens"] == 520


def test_closed_dream_rejects_duplicate_finalist_in_journal(tmp_path, complete_loop):
    # Copy the complete local control store so this adversarial append cannot
    # contaminate other acceptance checks.
    import shutil

    original, acceptance, _ = complete_loop
    copied = tmp_path / "copy"
    shutil.copytree(original.root, copied)
    store = Store(copied)
    owner = acceptance["study_id"]
    row = next(
        e["payload"] for e in store.events(owner) if e["event_type"] == "REVIEWED_FINALIST_FROZEN"
    )
    store.append(owner, "REVIEWED_FINALIST_FROZEN", row)
    with pytest.raises(IntegrityError, match="more than one"):
        verify_reviewed_dream(store, owner)


def test_task_cohorts_reject_renaming_exposed_data_as_heldout(tmp_path):
    store = Store(tmp_path)
    spec = fixture_spec(store)
    raw = spec.model_dump(mode="json")
    raw["heldout"][0]["program"]["queue"]["environment"]["period"]["exposure"] = (
        "development_exposed"
    )
    with pytest.raises(ValueError, match="unopened"):
        ReviewedDreamSpec.model_validate(raw)


def test_policy_controlled_program_budget_failure_cannot_skip_result_review(tmp_path):
    store = Store(tmp_path)
    engine, _ = engine_for(store)
    spec = fixture_spec(store).fresh[0].program
    cap = spec.execution_budget.model_copy(update={"max_llm_tokens": 1100})
    pid = engine.program.create(spec.model_copy(update={"execution_budget": cap}))
    for _ in range(30):
        status = engine.program.advance(pid)["status"]
        if status == "BUDGET_EXHAUSTED":
            break
    assert status == "BUDGET_EXHAUSTED"
    assert not [e for e in store.events(pid) if e["event_type"] == "PROGRAM_WORLD_COLLECTED"]
    assert full_program_summary(store, pid)["complete"] is False


@pytest.mark.parametrize("control", ["PAUSED", "CANCELLED"])
def test_loop_control_stops_before_any_role_or_backend_call(tmp_path, control):
    store = Store(tmp_path)
    engine, backends = engine_for(store)
    owner = engine.create(fixture_spec(store))
    store.control(owner, control)
    assert engine.advance(owner)["status"] == control
    assert backends.values == {} and not store.jobs(owner)


def test_compile_admission_cost_is_part_of_shared_program_total(tmp_path):
    from datetime import UTC, datetime, timedelta

    from jarvis.hermes_policy import q0
    from jarvis.iris_contracts import IrisAuthorization, IrisQueueSpec
    from jarvis.iris_design_fixture import (
        CompiledDesignTransport,
        DesignDirector,
        design_environment,
    )
    from jarvis.research_program import ResearchProgramSpec
    from jarvis.researcher import AgentConfig

    store = Store(tmp_path / "store")
    env = design_environment(tmp_path / "inputs")
    backend = CompiledDesignTransport(env)
    director, critic = DesignDirector(), LoopCritic()
    program = ResearchProgram(
        store,
        provider_factory=lambda _: director,
        critic_provider_factory=lambda _: critic,
        transport_factory=lambda _: backend,
    )
    owner = program.create(
        ResearchProgramSpec(
            queue=IrisQueueSpec(
                goal="Artificial compiled full-cost control",
                environment=env,
                authorization=IrisAuthorization(
                    environment_id=digest(env),
                    max_submissions=4,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    owner_note="Artificial only",
                ),
            ),
            directions=("Test compile accounting",),
            agent=AgentConfig(max_calls=1, reserve_tokens=1000),
            critic=AgentConfig(max_calls=2, reserve_tokens=1000),
            research_policy=q0(),
            execution_budget=Budget(
                max_actions=30,
                max_tool_calls=4,
                max_compute_units=30,
                max_llm_tokens=4000,
                max_wall_seconds=1000,
                max_parallel_jobs=2,
            ),
            max_direction_batches=1,
            experiments_per_epoch=1,
        )
    )
    for _ in range(20):
        if program.advance(owner)["status"] == "CLOSED":
            break
    report = program_report(store, owner)
    jobs = [j for scope in store.resource_members(owner) for j in store.jobs(scope)]
    compiles = [
        j
        for j in jobs
        if store.artifacts.get(j["request_ref"]).get("kind") == "IrisCompileAdmission"
    ]
    assert len(compiles) == 4 and all(j["finalized"] for j in compiles)
    assert report["full_usage"]["actual"]["compute_units"] == 6
    assert report["full_usage"]["actual"]["tool_calls"] == 2


def test_finite_loop_runner_resumes_same_study_and_honors_cancel(tmp_path, monkeypatch):
    store = Store(tmp_path)
    engine, _ = engine_for(store)
    owner = engine.create(fixture_spec(store))
    monkeypatch.setattr(engine, "advance", lambda _: {"status": "READY"})
    result = engine.run(owner, max_steps=1, wall_seconds=1, poll_seconds=1)
    assert result["status"] == "RUNNER_LIMIT" and result["resumable"]
    monkeypatch.undo()
    store.control(owner, "CANCELLED")
    assert engine.run(owner, max_steps=2)["status"] == "CANCELLED"


def shared_reservation_worker(root, owner, barrier, output):
    store = Store(root)
    barrier.wait(timeout=15)
    try:
        store.reserve(owner, {"sibling": owner}, Cost(actions=1, llm_tokens=50))
        output.put("reserved")
    except BudgetExceeded:
        output.put("bounded")


def test_sibling_processes_cannot_race_past_shared_budget(tmp_path):
    import multiprocessing

    store = Store(tmp_path)
    parent = store.create(
        {"task": {"budget": Budget(max_actions=1, max_llm_tokens=50).model_dump()}}
    )
    children = [
        store.create(
            {
                "task": {"budget": Budget(max_llm_tokens=100).model_dump()},
                "budget_owner": parent,
            }
        )
        for _ in range(2)
    ]
    ctx = multiprocessing.get_context("spawn")
    barrier, output = ctx.Barrier(2), ctx.Queue()
    workers = [
        ctx.Process(target=shared_reservation_worker, args=(tmp_path, child, barrier, output))
        for child in children
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert sorted(output.get(timeout=1) for _ in workers) == ["bounded", "reserved"]
    assert store.resource_totals_tree(parent)["pending"]["llm_tokens"] == 50


def test_policy_stopping_decision_is_verified(complete_loop, monkeypatch):
    from jarvis.reviewed_policy import verify_policy_decisions

    store, _, report = complete_loop
    owner = report["comparisons"][0]["pairs"][0]["candidate"]
    original = store.events

    def altered(campaign):
        rows = json.loads(json.dumps(original(campaign)))
        if campaign == owner:
            event = next(e for e in rows if e["event_type"] == "RESEARCH_POLICY_DECISION")
            event["payload"]["stop_program"] = not event["payload"]["stop_program"]
        return rows

    monkeypatch.setattr(store, "events", altered)
    with pytest.raises(IntegrityError, match="pinned policy"):
        verify_policy_decisions(store, owner)
