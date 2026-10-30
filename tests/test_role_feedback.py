"""Recorded failure shapes reproduced with scripted roles, never real research."""

import copy
import json
from pathlib import Path

import pytest

from jarvis.autonomous_research import AUTO_RECOVERY_ROUNDS, AutonomousResearch
from jarvis.autonomy_contracts import AutonomyContract, AutonomyDecision, AutonomySessionSpec
from jarvis.autonomy_report import replay_session, verify_session
from jarvis.contracts import Budget
from jarvis.identity import digest
from jarvis.planning_fixture import PlanningFixtureProvider, declared_world, hypothesis
from jarvis.role_feedback import DecisionAdmissionError, correction_context
from jarvis.runtime_controls import RuntimeControls, encoded_size
from jarvis.runtime_fixture import RuntimeFixtureProvider
from jarvis.runtime_fixture import declared_world as native_world
from jarvis.storage import Store


def test_critic_context_limit_is_not_counted_as_an_invalid_judgment(tmp_path):
    from jarvis.researcher import AgentConfig

    s = Store(tmp_path / "store")
    world = declared_world(s, {})
    contract = AutonomyContract.model_validate(s.artifacts.get(world["contract_ref"]))
    oversized = contract.model_copy(update={"instruction": "fixed owner constraint " * 400})
    e = AutonomousResearch(s, world["executors"], lambda cfg, r: PlanningFixtureProvider(r))
    o = e.create(
        AutonomySessionSpec(
            contract_ref=s.artifacts.put(oversized),
            role_config={"critic": AgentConfig(max_context_bytes=8000).model_dump()},
        )
    )
    try:
        result = e.run(o, wall_seconds=20)
        assert result["status"] == "NEEDS_ATTENTION", result
        assert "CONTEXT_ENVELOPE" in result["reason"]
        assert not any(v["event_type"] == "CRITIC_FAILURE" for v in s.events(o))
        assert not any(
            s.artifacts.get(j["request_ref"]).get("role") == "autonomy_critic" for j in s.jobs(o)
        )
    finally:
        e.runner.shutdown()


@pytest.mark.parametrize("fault", ["invalid", "ended_timeout", "charged_timeout", "unknown"])
def test_persistent_role_fault_stops_once_and_delivers_truthfully(tmp_path, fault):
    from jarvis.contracts import Cost

    class BrokenProvider(PlanningFixtureProvider):
        calls = 0

        def complete(self, prompt, schema, directory):
            self.calls += 1
            if fault == "invalid":
                return {"answer": {}, "cost": Cost(llm_tokens=1).model_dump()}
            return {
                "answer": None,
                "error": "PROVIDER_TIMEOUT" if fault != "unknown" else "UNKNOWN_COMPLETION",
                "cost": Cost(llm_tokens=1).model_dump() if fault == "charged_timeout" else None,
            }

    s = Store(tmp_path / "store")
    world = declared_world(s, {})
    provider = BrokenProvider()
    e = AutonomousResearch(s, world["executors"], lambda cfg, r: provider)
    o = e.create(
        AutonomySessionSpec(
            contract_ref=world["contract_ref"],
            runtime=RuntimeControls(provider_backoff_seconds=0),
            budget=Budget(max_llm_tokens=4000000, max_wall_seconds=60000),
        )
    )
    result = e.run(o, wall_seconds=20)
    expected = "NEEDS_RECONCILIATION" if fault == "unknown" else "NEEDS_ATTENTION"
    assert result["status"] == expected, result
    report = json.loads((Path(result["delivery"]["directory"]) / "deliverable.json").read_text())
    exit_view = report["runtime_exit"]
    assert exit_view["workflow_complete"] is False
    if fault == "unknown":
        assert provider.calls == 1 and len(exit_view["pending_jobs"]) == 1
        assert not exit_view["usage_accounting"]["reservation_settlements"]
        assert s.resource_totals(o)["pending"]["llm_tokens"] > 0
    else:
        assert exit_view["pending_jobs"] == []
        stop = exit_view["stop_details"]
        category = "invalid_decision" if fault == "invalid" else "provider_ended"
        assert stop["code"] == (
            "DECISION_REPAIR_EXHAUSTED" if fault == "invalid" else "PROVIDER_RETRY_EXHAUSTED"
        )
        assert stop["failure_counts"][category] == 3
        assert (
            stop["failure_counts"]["provider_ended" if fault == "invalid" else "invalid_decision"]
            == 0
        )
        # The run first recovered in place, then stopped resumably.
        assert stop["recoverable"] is True
        assert "Recoverable role stop" in exit_view["resume_point"]
        rounds = [x["payload"] for x in s.events(o) if x["event_type"] == "ROLE_RECOVERY"]
        assert [r["mode"] for r in rounds] == ["automatic"] * AUTO_RECOVERY_ROUNDS
        # Identical rejected corrections may adopt a receipt rather than pay again.
        assert 3 <= provider.calls <= 9 * (1 + AUTO_RECOVERY_ROUNDS)
        if fault == "ended_timeout":
            assert provider.calls == 3 * (1 + AUTO_RECOVERY_ROUNDS)
    calls, jobs = provider.calls, len(s.jobs(o))
    # A stopped session does not spend again until the owner resumes it.
    assert e.run(o)["status"] == expected
    assert provider.calls == calls and len(s.jobs(o)) == jobs
    if fault != "unknown":
        assert e.recover(o)["status"] == "RUNNING"
        assert e.run(o, wall_seconds=20)["status"] == expected
        assert provider.calls > calls


class FaultedRuntimeProvider(RuntimeFixtureProvider):
    """Two ended timeouts, one bad-answer wave, then targeted identity/control repairs."""

    def __init__(self, role):
        super().__init__(role)
        self.b_calls = 0
        self.repairs = []

    def complete(self, prompt, schema, directory):
        c = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        branches = {b["branch_id"]: b for b in c.get("research_plan", {}).get("branches", [])}
        pending_b = (
            schema["title"] == "AutonomyDecision"
            and branches.get("A", {}).get("status") == "closed"
            and "B" not in branches
        )
        if pending_b:
            self.b_calls += 1
            if self.b_calls <= 2:
                return {"answer": None, "error": "PROVIDER_TIMEOUT", "cost": None, "usage": None}
        return super().complete(prompt, schema, directory)

    def director(self, c):
        branches = {b["branch_id"]: b for b in c["research_plan"]["branches"]}
        pending_b = branches.get("A", {}).get("status") == "closed" and "B" not in branches
        feedback = c.get("correction") or c.get("recovery_feedback")
        if pending_b and self.b_calls > 5:
            assert feedback and feedback["rejected_answer_complete"]
            raw = copy.deepcopy(feedback["rejected_answer"])
            code = feedback["validation"]["code"]
            self.repairs.append(code)
            if code == "EXISTING_RESEARCH_IDENTITY":
                assert "q_A" in feedback["validation"]["details"]["existing_questions"]
                raw["question"] = None
                raw["action"]["question_id"] = "q_A"
            elif code == "RECORDED_BACKTEST_CONFIGURATION":
                detail = feedback["validation"]["details"]
                assert detail["existing_result_refs"]
                raw["action"]["experiments"] = [
                    e for e in raw["action"]["experiments"] if e["label"] != detail["label"]
                ]
                raw["action"]["evidence_refs"] = list(
                    dict.fromkeys(
                        [*raw["action"]["evidence_refs"], *detail["existing_result_refs"]]
                    )
                )
            else:
                raise AssertionError(code)
            raw["context_id"] = c["context_id"]
            return raw
        raw = super().director(c)
        if raw.get("branch") and raw["branch"]["branch_id"] == "A":
            # Both directions are already framed in this one existing question.
            raw["question"]["hypotheses"] += [hypothesis("B"), hypothesis("null_B")]
        if pending_b:
            # Recreates existing hypotheses in a new question and asks for an old baseline.
            # Three bad drafts deliberately exhaust one semantic wave after the two timeouts.
            base = c["research_contract"]["baseline_candidate_ref"]
            raw["action"]["experiments"].append(
                {
                    "label": "baseline_reuse",
                    "candidate_ref": base,
                    "settings": [{"name": "variant", "value": "baseline"}],
                    "period": "dev",
                    "hypothesis": "Matched control",
                    "falsification": "Unavailable",
                }
            )
        elif raw["action"]["branch_id"] == "B":
            raw["question"] = None
            raw["action"]["question_id"] = "q_A"
        return raw


def test_unattended_native_campaign_repairs_production_failure_shapes(tmp_path):
    s = Store(tmp_path / "store")
    world = native_world(s, {})
    providers = {
        r: FaultedRuntimeProvider(r) for r in ("director", "critic", "worker", "consultant")
    }
    e = AutonomousResearch(s, world["executors"], lambda cfg, role: providers[role])
    o = e.create(
        AutonomySessionSpec(
            contract_ref=world["contract_ref"],
            input_refs=world["input_refs"],
            max_concurrent_actions=1,
            max_decisions=16,
            runtime=RuntimeControls(provider_backoff_seconds=0),
            budget=Budget(
                max_actions=100,
                max_tool_calls=100,
                max_llm_tokens=1000000,
                max_wall_seconds=20000,
                max_compute_units=100,
                max_parallel_jobs=4,
            ),
        )
    )
    try:
        result = e.run(o, wall_seconds=90)
        assert result["status"] == "CLOSED", result
        assert providers["director"].repairs == [
            "EXISTING_RESEARCH_IDENTITY",
            "RECORDED_BACKTEST_CONFIGURATION",
        ]
        report = json.loads(
            (Path(result["delivery"]["directory"]) / "deliverable.json").read_text()
        )
        assert report["stage_gate"] == "PASS"
        assert report["runtime_exit"]["workflow_complete"] is True
        assert report["runtime_exit"]["pending_jobs"] == []
        accounting = report["runtime_exit"]["usage_accounting"]
        assert accounting["includes_unobserved_reservation_charges"] is True
        assert len(accounting["reservation_settlements"]) == 2
        assert all(
            v["basis"] == "RESERVATION_NOT_OBSERVED_USAGE"
            for v in accounting["reservation_settlements"]
        )
        failures = [v["payload"] for v in s.events(o) if v["event_type"] == "DIRECTOR_FAILURE"]
        assert [f["category"] for f in failures] == [
            "provider_ended",
            "provider_ended",
            "invalid_decision",
        ]
        assert verify_session(s, o)["validation"] == "VERIFIED"
        assert replay_session(s, o)["external_calls"] == 0
        # The old baseline control was neither resubmitted nor silently re-evaluated.
        state = e.state(o)
        observed = [s.artifacts.get(r) for r in state["observations"]]
        assert len([r for r in observed if r["period"] == "dev" and not r["changes"]]) == 1
        assert any(
            b["closure"] and b["closure"]["conclusion"] == "negative"
            for b in report["research_plan"]["branches"]
        )
        native = [
            s.artifacts.get(v["payload"]["observation_ref"])
            for child in state["launched"].values()
            for v in s.events(child)
            if v["event_type"] == "WORKER_STEP"
            and s.artifacts.get(v["payload"]["step_ref"])["tool"] == "run_native"
        ]
        assert len(native) == 1 and native[0]["status"] == "PASS"
    finally:
        e.runner.shutdown()


def test_rejected_large_code_is_bounded_and_not_relabelled_as_observed_evidence(tmp_path):
    s = Store(tmp_path)
    raw = {
        "context_id": digest("old"),
        "tool": "write_file",
        "path": "work/script.py",
        "content": "x" * 200000,
    }
    ref = s.artifacts.put(raw)
    feedback = correction_context(s, {"answer_ref": ref, "reason": "schema mismatch"})
    assert encoded_size(feedback) < 18000
    assert feedback["rejected_answer_complete"] is False
    assert feedback["rejected_answer_ref"] == ref
    assert s.artifacts.get(ref) == raw
    assert "NOT observed evidence" in feedback["instruction"]


@pytest.mark.parametrize("role", ["director", "critic"])
def test_transport_and_semantic_failure_allowances_are_separate_and_durable(tmp_path, role):
    s = Store(tmp_path)
    world = declared_world(s, {})
    e = AutonomousResearch(s, world["executors"], lambda cfg, r: PlanningFixtureProvider(r))
    o = e.create(
        AutonomySessionSpec(
            contract_ref=world["contract_ref"],
            runtime=RuntimeControls(provider_backoff_seconds=0),
        )
    )
    failures = [{"reason": "provider ended without usage; legacy record"}] * 2 + [
        {"category": "invalid_decision", "reason": "bad draft"}
    ]
    assert e._role_failure_stop(o, role, 1, failures) is None
    # Each exhausted round first recovers in the same run with a fresh allowance.
    for _ in range(AUTO_RECOVERY_ROUNDS):
        failures = [*failures, *[{"category": "provider_ended"}] * (3 - (_ == 0))]
        assert e._role_failure_stop(o, role, 1, failures) is None
    failures = [*failures, *[{"category": "provider_ended"}] * 3]
    end = e._role_failure_stop(o, role, 1, failures)
    assert end["code"] == "PROVIDER_RETRY_EXHAUSTED" and end["recoverable"]
    assert s.status(o) == "NEEDS_ATTENTION"
    before = len(s.jobs(o))
    assert e.run(o)["status"] == "NEEDS_ATTENTION"
    assert len(s.jobs(o)) == before
    stopped = [x["payload"] for x in s.events(o) if x["event_type"] == "SESSION_PARTIAL_STOP"][-1]
    assert stopped["failure_counts"] == {"invalid_decision": 0, "provider_ended": 3}
    assert stopped["scientific_conclusion"] == "NOT_REACHED"


def test_cross_dataset_observation_cannot_block_a_new_matched_experiment(tmp_path):
    s = Store(tmp_path)
    world = declared_world(s, {})
    e = AutonomousResearch(s, world["executors"], lambda cfg, r: PlanningFixtureProvider(r))
    o = e.create(AutonomySessionSpec(contract_ref=world["contract_ref"]))
    contract = AutonomyContract.model_validate(s.artifacts.get(world["contract_ref"]))
    state = e.state(o)
    context = e.director_context(o, state)
    raw = PlanningFixtureProvider().director({**context, "context_id": digest(context)})
    action = AutonomyDecision.model_validate(raw).action
    defaults = {v.name: v.baseline for v in contract.settings}
    different = s.artifacts.put(
        {
            "kind": "BacktestObservation",
            "candidate_ref": contract.baseline_candidate_ref,
            "settings": defaults,
            "period": "dev",
            "status": "SUCCEEDED",
            "evaluator_id": contract.evaluator_id,
            "dataset_id": "OTHER_DATASET",
        }
    )
    state["observations"] = [different]
    e._check_backtest(contract, state, action, visible=set(context["visible_evidence_ids"]))
    matching = s.artifacts.put({**s.artifacts.get(different), "dataset_id": contract.dataset_id})
    state["observations"] = [matching]
    with pytest.raises(DecisionAdmissionError) as caught:
        e._check_backtest(contract, state, action, visible=set())
    assert caught.value.details["existing_result_refs"] == []  # no hidden-reference leak
    with pytest.raises(DecisionAdmissionError) as caught:
        e._check_backtest(contract, state, action, visible={matching})
    assert caught.value.details["existing_result_refs"] == [matching]
