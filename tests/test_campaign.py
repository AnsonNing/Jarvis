import pytest
from pydantic import ValidationError

from jarvis.agents import MockProvider, RoleRequest
from jarvis.campaign import CampaignEngine, InjectedInterruption
from jarvis.contracts import AdmissionError, IntegrityError
from jarvis.history import nodes, verify
from jarvis.policy import FixedPolicy, PolicySpec, admit
from jarvis.replay import replay
from jarvis.reporting import report
from jarvis.storage import Store
from jarvis.synthetic import SyntheticAdapter


def abrupt_worker(root, campaign):
    import os

    try:
        CampaignEngine(Store(root)).run(campaign, fault="after_receipt")
    except InjectedInterruption:
        os._exit(17)


def actions(store, campaign):
    return [
        store.artifacts.get(n["request_ref"])["action"]["action_id"] for n in nodes(store, campaign)
    ]


def test_adaptive_negative_branch_and_fixed_formal_evaluation(tmp_path, task):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    assert engine.run(campaign) == "CLOSED"
    assert actions(store, campaign) == ["inspect", "capacity", "diagnose", "regularize", "refine"]
    value = report(store, campaign)
    assert value["experiments"][1]["evaluation"]["acceptance"] == "FAIL"
    assert value["formal"]["evaluation"]["scope"] == "locked_synthetic"
    assert value["outcome"] == "VALIDATED_IMPROVEMENT_IN_SYNTHETIC_SCOPE"
    assert value["usage"]["tool_calls"] == 6
    assert value["usage"]["llm_tokens"] == 0
    assert verify(store, campaign)["formal_reveals"] == 1
    before = store.events(campaign)
    assert engine.run(campaign) == "CLOSED"
    assert before == store.events(campaign)


def test_observed_positive_result_changes_next_action(tmp_path, task):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task.model_copy(update={"world_ref": "synthetic-capacity-v1"}))
    engine.run(campaign)
    assert actions(store, campaign) == ["inspect", "capacity", "refine"]
    verify(store, campaign)


def test_negative_research_is_not_execution_failure(tmp_path, task):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task.model_copy(update={"world_ref": "synthetic-negative-v1"}))
    assert engine.run(campaign) == "CLOSED"
    value = report(store, campaign)
    assert value["outcome"] == "NO_DEMONSTRATED_IMPROVEMENT"
    assert all(x["status"] == "SUCCEEDED" for x in value["experiments"])


def test_sufficient_baseline_stops_search(tmp_path, task):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    modified = task.model_copy(
        update={
            "world_ref": "synthetic-sufficient-v1",
            "acceptance": task.acceptance.model_copy(update={"target_primary": 0.9}),
        }
    )
    campaign = engine.create(modified)
    engine.run(campaign)
    assert actions(store, campaign) == ["inspect"]


@pytest.mark.parametrize("fault", ["after_reserve", "after_receipt"])
def test_recover_without_duplicate_calls(tmp_path, task, monkeypatch, fault):
    calls = []
    execute = SyntheticAdapter.execute

    def counted(self, action):
        calls.append(action.action_id)
        return execute(self, action)

    monkeypatch.setattr(SyntheticAdapter, "execute", counted)
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    with pytest.raises(InjectedInterruption):
        engine.run(campaign, fault=fault)
    resumed = CampaignEngine(Store(tmp_path))
    assert resumed.run(campaign) == "CLOSED"
    assert calls == ["inspect", "capacity", "diagnose", "regularize", "refine"]
    assert store.usage(campaign)["tool_calls"] == 6
    assert len(store.jobs(campaign)) == 6
    verify(store, campaign)


def test_started_job_without_receipt_requires_reconciliation(tmp_path, task, monkeypatch):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    with pytest.raises(InjectedInterruption):
        engine.run(campaign, fault="after_start")
    monkeypatch.setattr(
        SyntheticAdapter, "execute", lambda *_: pytest.fail("duplicate tool invocation")
    )
    assert engine.run(campaign) == "NEEDS_RECONCILIATION"
    assert engine.run(campaign) == "NEEDS_RECONCILIATION"
    assert len(store.jobs(campaign)) == 1
    assert store.usage(campaign)["tool_calls"] == 1  # pending reservation is retained
    verify(store, campaign)


def test_pause_resume_and_cancel_control(tmp_path, task):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    assert engine.run(campaign, max_steps=2) == "PAUSED"
    assert actions(store, campaign) == ["inspect", "capacity"]
    assert engine.run(campaign) == "CLOSED"
    cancelled = engine.create(task)
    store.control(cancelled, "CANCELLED")
    assert engine.run(cancelled) == "CANCELLED"
    assert store.jobs(cancelled) == []


def test_budget_stops_and_preserves_partial_results(tmp_path, task):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    modified = task.model_copy(update={"budget": task.budget.model_copy(update={"max_actions": 2})})
    campaign = engine.create(modified)
    assert engine.run(campaign) == "EXHAUSTED"
    assert store.usage(campaign)["actions"] == 2
    assert len(report(store, campaign)["experiments"]) == 2


def test_changed_runtime_cannot_resume_same_world(tmp_path, task, monkeypatch):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    engine.run(campaign, max_steps=1)
    monkeypatch.setattr("jarvis.campaign.source_identity", lambda: "changed")
    with pytest.raises(IntegrityError):
        engine.run(campaign)
    assert len(store.jobs(campaign)) == 1


def test_infrastructure_error_keeps_receipt_and_cost(tmp_path, task, monkeypatch):
    def broken(*_):
        raise OSError("fixture worker interrupted")

    monkeypatch.setattr(SyntheticAdapter, "execute", broken)
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    assert engine.run(campaign) == "FAILED"
    value = report(store, campaign)
    assert value["experiments"][0]["status"] == "FAILED"
    assert value["experiments"][0]["evaluation"]["acceptance"] == "INCONCLUSIVE"
    assert store.usage(campaign)["tool_calls"] == 1


def test_decision_validation_and_mock_provider(tmp_path, task):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    state = engine.state(campaign, SyntheticAdapter())
    good = FixedPolicy().choose(state)
    admit(good, state)
    for update, code in [
        ({"state_hash": "old"}, "STALE_DECISION"),
        ({"action_id": "execute_shell"}, "UNKNOWN_ACTION"),
        ({"evidence_refs": ("future-result",)}, "EVIDENCE_NOT_VISIBLE"),
    ]:
        with pytest.raises(AdmissionError, match=code):
            admit(good.model_copy(update=update), state)
    for role in ["director", "scientist", "designer", "critic", "curator"]:
        result = MockProvider().complete(RoleRequest(request_id="request", role=role, state=state))
        admit(result.decision, state)
        assert result.usage.llm_tokens == 0
    assert "locked_baseline" not in state.model_dump_json()


def test_exact_replay_makes_zero_domain_or_evaluator_calls(tmp_path, task, monkeypatch):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    engine.run(campaign)
    monkeypatch.setattr(
        SyntheticAdapter, "execute", lambda *_: pytest.fail("tool called in replay")
    )
    monkeypatch.setattr(
        "jarvis.campaign.evaluate", lambda *_: pytest.fail("evaluator called in replay")
    )
    monkeypatch.setattr(
        MockProvider, "complete", lambda *_: pytest.fail("provider called in replay")
    )
    value = replay(store, campaign, FixedPolicy())
    assert value["status"] == "COMPLETE"
    assert value["external_calls"] == 0
    assert value["cost"]["tool_calls"] == 5
    assert [x["action_id"] for x in value["trace"]] == actions(store, campaign)


def test_replay_unknown_action_never_fabricates_score(tmp_path, task):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    engine.run(campaign)
    value = replay(store, campaign, FixedPolicy(PolicySpec(name="probe", prefer=("unknown",))))
    assert value["status"] == "INSUFFICIENT_COVERAGE"
    assert value["reason"] == "UNKNOWN_ACTION"
    assert value["score"] is None
    assert value["cost"]["tool_calls"] == 1
    assert len(value["trace"]) == 1


def test_replay_stops_before_unpaid_results(tmp_path, task):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    engine.run(campaign)
    value = replay(store, campaign, FixedPolicy(PolicySpec(name="stop", prefer=("stop",))))
    assert value["status"] == "COMPLETE"
    assert value["best_revealed_value"] == 0.4
    assert value["cost"]["tool_calls"] == 1


def test_policy_cannot_contain_executable_fields():
    with pytest.raises(ValidationError):
        PolicySpec.model_validate({"name": "injected", "python": "import os"})


def test_abrupt_process_exit_adopts_receipt_once(tmp_path, task):
    import multiprocessing

    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    process = multiprocessing.get_context("spawn").Process(
        target=abrupt_worker, args=(tmp_path, campaign)
    )
    process.start()
    process.join(20)
    assert process.exitcode == 17
    original_job = store.jobs(campaign)[0]["id"]
    assert Store(tmp_path).read_receipt(original_job) is not None
    assert engine.run(campaign) == "CLOSED"
    assert actions(store, campaign).count("inspect") == 1
    assert nodes(store, campaign)[0]["node_id"] == original_job
    assert store.usage(campaign)["tool_calls"] == 6


def test_negative_findings_and_critic_are_sealed(tmp_path, task):
    store = Store(tmp_path)
    engine = CampaignEngine(store)
    campaign = engine.create(task)
    engine.run(campaign)
    negative = report(store, campaign)["experiments"][1]
    assert negative["critique"]["classification"] == "RESEARCH_NEGATIVE"
    assert negative["finding"]["status"] == "TESTED_NEGATIVE"
    assert negative["finding"]["transferable"] is False
