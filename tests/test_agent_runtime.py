import json

import pytest

from jarvis.agent_runtime import AgentInterrupted
from jarvis.campaign import CampaignEngine
from jarvis.contracts import Cost, Hypothesis
from jarvis.history import nodes, verify
from jarvis.replay import replay
from jarvis.reporting import report
from jarvis.researcher import AgentConfig, ExperimentDesign, ExperimentParameters, ResearchTurn
from jarvis.storage import Store


class ScriptedProvider:
    """Deterministic provider fixture, never labeled as a real model acceptance."""

    def __init__(self, mode="valid"):
        self.calls = []
        self.mode = mode

    def identity(self):
        return {"provider": "scripted-test-only", "version": 1}

    def complete(self, prompt, schema, directory):
        context = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        self.calls.append(context)
        available = context["available_actions"]
        template = available[0]
        parameters = ExperimentParameters()
        if context["domain"] == "hermes" and "baseline" in {
            o["action_id"] for o in context["observations"]
        }:
            prior = context["prior_research"]
            experimental = [
                p for p in prior if p["experiment"]["template_id"] == "threshold_experiment"
            ]
            chosen = "threshold_experiment" if len(experimental) < 2 else "stop"
            template = next(a for a in available if a["action_id"] == chosen)
            if chosen != "stop":
                # The second intervention responds to the first measured comparison.
                measured = [
                    o for o in context["observations"] if o["action_id"] == "threshold_experiment"
                ]
                parameters = (
                    ExperimentParameters(cancel_mode="disabled")
                    if measured
                    else ExperimentParameters(trading_cutoff_delta=-0.0001)
                )
        hypothesis = Hypothesis.model_validate(template["hypothesis"]).model_copy(
            update={
                "origin": "agent_proposed",
                "claim": "A controlled comparison discriminates the current hypothesis",
                "evidence_refs": tuple(context["visible_evidence_ids"]),
            }
        )
        turn = ResearchTurn(
            request_id=context["request_id"],
            state_hash=context["state_hash"],
            analysis_summary=f"Replanned after {len(context['observations'])} observed results",
            evidence_refs=tuple(context["visible_evidence_ids"]),
            hypothesis=hypothesis,
            experiment=ExperimentDesign(
                template_id=template["action_id"],
                intervention="One controlled intervention",
                comparator="frozen_baseline",
                controlled_variables=("data", "models", "evaluator"),
                parameters=parameters,
                expected_observation="A measured comparison",
                success_criterion="Compare declared primary metric without changing its definition",
            ),
            next_direction="STOP"
            if template["action_id"] == "stop"
            else "BRANCH"
            if len(context["observations"]) > 1
            else "CONTINUE",
            limitations=("Synthetic provider fixture",),
        )
        answer = turn.model_dump(mode="json")
        if self.mode == "stale_once" and len(self.calls) == 1:
            answer["state_hash"] = "stale"
        if self.mode == "invalid_always":
            answer["invented_metric"] = 999
        if self.mode == "missing_usage":
            return {"answer": answer, "cost": None, "usage": None, "error": "USAGE_UNAVAILABLE"}
        return {
            "answer": answer,
            "usage": {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 80},
            "cost": Cost(llm_tokens=120, wall_seconds=0.01).model_dump(),
            "error": None,
        }


def setup_engine(tmp_path, provider):
    store = Store(tmp_path / "evidence")
    return store, CampaignEngine(store, provider_factory=lambda _: provider)


def test_provider_drives_research_and_actual_tokens_are_charged(tmp_path, task):
    provider = ScriptedProvider()
    store, engine = setup_engine(tmp_path, provider)
    campaign = engine.create(task, researcher=AgentConfig(reserve_tokens=1000))
    assert engine.run(campaign) == "CLOSED"
    value = report(store, campaign)
    assert value["controller"] == "LLM research agent / codex-cli"
    assert len(provider.calls) >= 3
    assert store.usage(campaign)["llm_tokens"] == 120 * len(provider.calls)
    assert all(x["hypothesis"]["origin"] == "agent_proposed" for x in value["experiments"])
    assert value["agent_attempts"][-1]["turn"]["next_direction"] == "STOP"
    assert verify(store, campaign)["agent_completions"] == len(provider.calls)


def test_rejected_proposal_is_paid_and_one_repair_is_bounded(tmp_path, task):
    provider = ScriptedProvider("stale_once")
    store, engine = setup_engine(tmp_path, provider)
    campaign = engine.create(task, researcher=AgentConfig(reserve_tokens=1000))
    assert engine.run(campaign) == "CLOSED"
    attempts = report(store, campaign)["agent_attempts"]
    assert attempts[0]["rejection"].startswith("INVALID_PROPOSAL")
    assert attempts[1]["turn"] is not None
    assert provider.calls[1]["repair_reason"]
    assert store.usage(campaign)["llm_tokens"] == 120 * len(provider.calls)


def test_invalid_proposals_cannot_loop_or_execute_tools(tmp_path, task):
    provider = ScriptedProvider("invalid_always")
    store, engine = setup_engine(tmp_path, provider)
    campaign = engine.create(task, researcher=AgentConfig(reserve_tokens=1000))
    assert engine.run(campaign) == "FAILED"
    assert len(provider.calls) == 2
    assert nodes(store, campaign) == []
    assert store.usage(campaign)["llm_tokens"] == 240
    verify(store, campaign)


@pytest.mark.parametrize("fault", ["agent_after_reserve", "agent_after_receipt"])
def test_provider_recovery_never_duplicates_paid_completion(tmp_path, task, fault):
    provider = ScriptedProvider()
    store, engine = setup_engine(tmp_path, provider)
    campaign = engine.create(task, researcher=AgentConfig(reserve_tokens=1000))
    with pytest.raises(AgentInterrupted):
        engine.run(campaign, fault=fault)
    assert engine.run(campaign) == "CLOSED"
    assert sum(c["observations"] == [] for c in provider.calls) == 1
    assert store.usage(campaign)["llm_tokens"] == 120 * len(provider.calls)
    verify(store, campaign)


@pytest.mark.parametrize("kind", ["started", "missing_usage"])
def test_uncertain_provider_stops_and_preserves_reservation(tmp_path, task, kind):
    provider = ScriptedProvider("missing_usage" if kind == "missing_usage" else "valid")
    store, engine = setup_engine(tmp_path, provider)
    campaign = engine.create(task, researcher=AgentConfig(reserve_tokens=1000))
    if kind == "started":
        with pytest.raises(AgentInterrupted):
            engine.run(campaign, fault="agent_after_start")
    else:
        assert engine.run(campaign) == "NEEDS_RECONCILIATION"
    before = len(provider.calls)
    assert engine.run(campaign) == "NEEDS_RECONCILIATION"
    assert engine.run(campaign) == "NEEDS_RECONCILIATION"
    assert len(provider.calls) == before
    assert store.usage(campaign)["llm_tokens"] == 1000  # retained reservation, not measured zero
    assert nodes(store, campaign) == []
    verify(store, campaign)


def test_call_cap_and_token_overrun_stop_further_dispatch(tmp_path, task):
    provider = ScriptedProvider()
    store, engine = setup_engine(tmp_path, provider)
    campaign = engine.create(task, researcher=AgentConfig(reserve_tokens=1000, max_calls=1))
    assert engine.run(campaign) == "EXHAUSTED"
    assert len(provider.calls) == 1 and len(nodes(store, campaign)) == 1
    other = engine.create(task, researcher=AgentConfig(reserve_tokens=100))
    assert engine.run(other) == "EXHAUSTED"
    assert store.usage(other)["llm_tokens"] == 120
    assert len(nodes(store, other)) == 0


def test_budget_reservation_precedes_provider_dispatch(tmp_path, task):
    provider = ScriptedProvider()
    store, engine = setup_engine(tmp_path, provider)
    bounded = task.model_copy(
        update={"budget": task.budget.model_copy(update={"max_llm_tokens": 1000})}
    )
    campaign = engine.create(bounded, researcher=AgentConfig(reserve_tokens=1000))
    assert engine.run(campaign) == "EXHAUSTED"
    assert len(provider.calls) == 1
    assert store.resource_totals(campaign)["actual"]["llm_tokens"] == 120
    assert store.resource_totals(campaign)["pending"]["llm_tokens"] == 0


def test_recorded_agent_replay_uses_no_provider(tmp_path, task, monkeypatch):
    provider = ScriptedProvider()
    store, engine = setup_engine(tmp_path, provider)
    campaign = engine.create(task, researcher=AgentConfig(reserve_tokens=1000))
    engine.run(campaign)
    monkeypatch.setattr(provider, "complete", lambda *_: pytest.fail("LLM invoked during replay"))
    value = replay(store, campaign)
    assert value["status"] == "COMPLETE"
    assert value["external_calls"] == 0
    assert value["policy_mode"] == "recorded_llm_decisions"
    assert value["cost"]["llm_tokens"] == store.usage(campaign)["llm_tokens"]
