import json

import pytest

from jarvis.contracts import AdmissionError, IntegrityError, JarvisError
from jarvis.critic_fixture import CriticFixture, ReviewedDirectorFixture, run_critic_fixture
from jarvis.identity import digest
from jarvis.research_critic import CriticReview, validate_review
from jarvis.research_fixture import FixtureBackends, fixture_environment, fixture_plan
from jarvis.research_program import (
    ResearchProgram,
    ResearchProgramSpec,
    program_report,
    verify_program,
)
from jarvis.research_roles import RoleCaller, verify_roles
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


def setup_program(tmp_path, *, critic=None, rounds=2, budget=300000):
    store = Store(tmp_path / "store")
    env = fixture_environment(tmp_path / "inputs")
    director, reviewer = ReviewedDirectorFixture(), critic or CriticFixture()
    backends = FixtureBackends()
    engine = ResearchProgram(
        store,
        provider_factory=lambda _: director,
        critic_provider_factory=lambda _: reviewer,
        transport_factory=backends,
    )
    pid = engine.create(
        ResearchProgramSpec(
            queue=fixture_plan(env, "quadratic").queue,
            directions=("Improve artificial PL and retain risks",),
            agent=AgentConfig(max_calls=rounds, reserve_tokens=65536),
            critic=AgentConfig(max_calls=2 * rounds, reserve_tokens=65536),
            max_direction_batches=rounds,
            llm_token_budget=budget,
        )
    )
    return store, engine, pid, director, reviewer, backends


def close(engine, pid):
    for _ in range(40):
        value = engine.advance(pid)
        if value["status"] == "CLOSED":
            return
    pytest.fail(f"reviewed program did not close: {value}")


def ready_for_review(engine, pid):
    for _ in range(12):
        if engine.advance(pid)["status"] == "AWAITING_PROPOSAL_CRITIQUE":
            return
    pytest.fail("baseline and direction did not finish")


def test_independent_review_filters_confounds_retains_negative_and_informs_next_round(
    tmp_path, monkeypatch
):
    store, engine, pid, director, critic, backends = setup_program(tmp_path)
    ready_for_review(engine, pid)
    # Until the independent Critic approves, only the frozen baseline has run.
    assert sum(len(b.submissions) for b in backends.values.values()) == 1
    assert critic.calls == []
    close(engine, pid)
    report = program_report(store, pid)
    assert report["program_verification"]["critic_reviews"] == 4
    assert len(director.calls) == 2 and len(critic.calls) == 4
    assert director.calls[0]["critic_feedback"] == []
    assert len(director.calls[1]["critic_feedback"]) == 2
    assert len(critic.calls[0]["observations"]) == 1
    assert any(
        a["verdict"] == "negative"
        for a in director.calls[1]["critic_feedback"][1]["review"]["assessments"]
    )
    rejected = report["direction_batches"][0]["rejected"]
    assert len(rejected) == 1 and rejected[0]["experiment"]["label"] == "confounded"
    assert sum(len(b.submissions) for b in backends.values.values()) == 4
    results = {
        r["experiment"]["label"]: r["result"]["entry_only_pl"] for r in report["experiments"]
    }
    assert results["negative-heuristic"] < results["baseline"] < results["exit"]
    # Critic has no field for editing numeric verdicts or granting execution rights.
    assert "entry_only_pl" not in CriticReview.model_fields
    monkeypatch.setattr(
        director, "complete", lambda *_: pytest.fail("offline audit called director")
    )
    monkeypatch.setattr(critic, "complete", lambda *_: pytest.fail("offline audit called critic"))
    monkeypatch.setattr(engine.queue, "tick", lambda *_: pytest.fail("offline audit ran backend"))
    assert verify_program(store, pid)["external_calls"] == 0
    assert verify_roles(store, pid)["calls"] == 6
    assert engine.advance(pid)["status"] == "CLOSED"


@pytest.mark.parametrize("fault", ["after_receipt", "after_start"])
def test_critic_recovery_never_resends_unknown_or_completed_paid_call(tmp_path, monkeypatch, fault):
    store, engine, pid, director, critic, backends = setup_program(tmp_path, rounds=1)
    ready_for_review(engine, pid)
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
        engine.advance(pid)
    frozen = [
        e["payload"]["context_ref"]
        for e in store.events(pid)
        if e["event_type"] == "PROGRAM_ROLE_REQUESTED"
    ]
    if fault == "after_receipt":
        close(engine, pid)
        assert len(director.calls) == 1 and len(critic.calls) == 2
        assert store.resource_totals(pid)["pending"]["llm_tokens"] == 0
        assert verify_program(store, pid)["status"] == "VERIFIED"
    else:
        with pytest.raises(JarvisError, match="no provider resubmission"):
            engine.advance(pid)
        assert engine.advance(pid)["status"] == "NEEDS_RECONCILIATION"
        assert len(critic.calls) == 0
        assert store.resource_totals(pid)["pending"]["llm_tokens"] == 65536
        assert sum(len(b.submissions) for b in backends.values.values()) == 1
    assert [
        e["payload"]["context_ref"]
        for e in store.events(pid)
        if e["event_type"] == "PROGRAM_ROLE_REQUESTED"
    ][:2] == frozen


class AlteredCritic(CriticFixture):
    def __init__(self, alteration):
        super().__init__()
        self.alteration = alteration

    def complete(self, *args):
        result = super().complete(*args)
        self.alteration(result["answer"])
        return result


@pytest.mark.parametrize(
    "alter",
    [
        lambda r: r.update(context_id="stale"),
        lambda r: r["assessments"][0].update(evidence_refs=["future-result"]),
        lambda r: r["assessments"].pop(),
        lambda r: r["assessments"].__setitem__(1, r["assessments"][0]),
        lambda r: r.update(entry_only_pl=99999),
        lambda r: r["assessments"][0].update(verdict="supported"),
    ],
)
def test_invalid_critique_fails_closed_before_any_candidate_dispatch(tmp_path, alter):
    reviewer = AlteredCritic(alter)
    store, engine, pid, _, critic, backends = setup_program(tmp_path, critic=reviewer, rounds=1)
    ready_for_review(engine, pid)
    with pytest.raises((AdmissionError, ValueError)):
        engine.advance(pid)
    assert engine.advance(pid)["status"] == "CRITIC_NEEDS_ATTENTION"
    assert len(critic.calls) == 1
    assert sum(len(b.submissions) for b in backends.values.values()) == 1
    assert len(store.jobs(pid)) == 2  # Invalid observed response is still cost-accounted.


def test_critic_stop_is_recorded_without_erasing_rejected_proposals(tmp_path):
    reviewer = AlteredCritic(lambda r: r.update(stop_recommended=True))
    store, engine, pid, _, critic, backends = setup_program(tmp_path, critic=reviewer)
    close(engine, pid)
    report = program_report(store, pid)
    assert len(report["direction_batches"][0]["rejected"]) == 3
    assert all(
        a["verdict"] == "not_executed" for a in report["critic_reviews"][1]["review"]["assessments"]
    )
    assert len(critic.calls) == 2
    assert sum(len(b.submissions) for b in backends.values.values()) == 1


def test_critic_budget_stop_preserves_baseline_and_does_not_silently_bypass_review(tmp_path):
    class ExpensiveDirector(ReviewedDirectorFixture):
        def complete(self, *args):
            response = super().complete(*args)
            response["cost"]["llm_tokens"] = 60000
            return response

    store, engine, pid, _, critic, backends = setup_program(tmp_path, rounds=1, budget=65536)
    expensive = ExpensiveDirector()
    engine.provider_factory = lambda _: expensive
    ready_for_review(engine, pid)
    assert engine.advance(pid)["status"] == "BUDGET_EXHAUSTED"
    assert engine.advance(pid)["status"] == "BUDGET_EXHAUSTED"
    assert critic.calls == []
    assert sum(len(b.submissions) for b in backends.values.values()) == 1
    assert program_report(store, pid)["status"] == "BUDGET_EXHAUSTED"


def test_independent_provider_identity_is_pinned(tmp_path, monkeypatch):
    store, engine, pid, _, critic, _ = setup_program(tmp_path)
    ready_for_review(engine, pid)
    monkeypatch.setattr(critic, "identity", lambda: {"provider": "different-reviewer"})
    with pytest.raises(IntegrityError, match="provider changed"):
        engine.advance(pid)
    assert len(store.jobs(pid)) == 1


def test_result_review_must_distinguish_rejected_from_observed_and_cite_results(tmp_path):
    store, engine, pid, _, critic, _ = setup_program(tmp_path, rounds=1)
    close(engine, pid)
    context = {k: v for k, v in critic.calls[1].items() if k != "context_id"}
    review = program_report(store, pid)["critic_reviews"][1]["review"]
    assert digest(context) == review["context_id"]
    review["assessments"][0]["evidence_refs"] = []
    with pytest.raises(AdmissionError, match="actual result"):
        validate_review(CriticReview.model_validate(review), context)


def test_public_fixture_command_records_distinct_role_calls_and_zero_remote_work(tmp_path):
    acceptance = run_critic_fixture(Store(tmp_path / "fixture"))
    assert acceptance["status"] == "CLOSED"
    assert acceptance["role_calls"] == {"direction": 2, "critic": 4}
    assert acceptance["remote_iris_submissions"] == 0
    assert acceptance["hermes_research"] == "NOT_STARTED"
    report = json.loads(open(acceptance["report"]).read())
    assert len(report["critic_reviews"]) == 4


def test_legacy_program_spec_keeps_critic_opt_in_for_persisted_history(tmp_path):
    store, engine, pid, _, _, _ = setup_program(tmp_path)
    raw = store.manifest(pid)["spec"]
    raw.pop("critic")
    assert ResearchProgramSpec.model_validate(raw).critic is None


def test_empty_direction_stops_with_empty_reviews_and_no_candidate_execution(tmp_path):
    store, engine, pid, _, critic, backends = setup_program(tmp_path)

    class EmptyDirector(ReviewedDirectorFixture):
        def complete(self, *args):
            response = super().complete(*args)
            response["answer"].update(experiments=[], stop_reason="No discriminating experiment")
            return response

    engine.provider_factory = lambda _: EmptyDirector()
    close(engine, pid)
    assert len(critic.calls) == 2
    assert program_report(store, pid)["program_verification"]["status"] == "VERIFIED"
    assert sum(len(b.submissions) for b in backends.values.values()) == 1


def test_continuous_program_inherits_critic_and_respects_its_stop(tmp_path):
    from jarvis.iris_continuous import (
        IrisContinuousSpec,
        IrisContinuousStudy,
        IrisResearchTrack,
        continuous_report,
    )
    from jarvis.iris_quota import IrisQuotaPolicy

    env = fixture_environment(tmp_path / "inputs")
    store = Store(tmp_path / "store")
    director = ReviewedDirectorFixture()
    critic = AlteredCritic(lambda r: r.update(stop_recommended=True))
    backends = FixtureBackends()
    queue = fixture_plan(env, "quadratic").queue
    engine = IrisContinuousStudy(
        store,
        provider_factory=lambda cfg: critic if cfg.model == "critic-fixture" else director,
        transport_factory=backends,
    )
    owner = engine.create(
        IrisContinuousSpec(
            tracks=(IrisResearchTrack(label="long", smoke=queue, development=queue),),
            quota_policy=IrisQuotaPolicy(scope="critic-artificial-account"),
            owner_instruction="Artificial research control only",
            directions=("Test independent stop",),
            critic=AgentConfig(model="critic-fixture", max_calls=6, reserve_tokens=65536),
        )
    )
    engine.refresh_quota(owner, [])
    for _ in range(40):
        if engine.advance(owner)["status"] == "CLOSED":
            break
    report = continuous_report(store, owner)
    assert report["status"] == "CLOSED"
    assert len(report["programs"]) == 1
    assert len(critic.calls) == 2
    assert sum(len(b.submissions) for b in backends.values.values()) == 1


@pytest.mark.parametrize("status", ["BUDGET_EXHAUSTED", "CRITIC_NEEDS_ATTENTION"])
def test_runner_stops_immediately_at_unfinished_review_gate(tmp_path, status):
    from jarvis.research_runner import run_program

    store, engine, pid, _, _, _ = setup_program(tmp_path)
    store.set_status(pid, status)
    result = run_program(store, engine, pid, max_steps=5)
    assert result["status"] == status
    assert len([e for e in store.events(pid) if e["event_type"] == "RESEARCH_RUNNER_STEP"]) == 1


def test_audit_rejects_an_extra_forged_critic_review(tmp_path):
    store, engine, pid, _, _, _ = setup_program(tmp_path, rounds=1)
    close(engine, pid)
    original = next(
        e["payload"] for e in store.events(pid) if e["event_type"] == "CRITIC_REVIEW_RECORDED"
    )
    store.append(pid, "CRITIC_REVIEW_RECORDED", original)
    with pytest.raises(IntegrityError, match="independent receipt"):
        verify_program(store, pid)
