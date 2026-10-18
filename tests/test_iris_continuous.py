import json
from datetime import UTC, datetime, timedelta

import pytest
from test_iris_queue import iris_environment as iris_environment

from jarvis.contracts import AdmissionError, Cost, IntegrityError, JarvisError
from jarvis.identity import digest
from jarvis.iris_continuous import (
    IrisContinuousSpec,
    IrisContinuousStudy,
    IrisResearchTrack,
    continuous_report,
)
from jarvis.iris_contracts import IrisQueueSpec
from jarvis.iris_fixture import FixtureIrisTransport
from jarvis.iris_quota import IrisQuotaPolicy, IrisQuotaSnapshot, QuotaLedger
from jarvis.storage import Store


def snapshot(used=0, limit=100, rid="00000000", age=0):
    return IrisQuotaSnapshot(
        used=used,
        limit=limit,
        request_id=rid,
        ack_created_at=datetime.now(UTC) - timedelta(seconds=age),
        source="SIMULATED_FIXTURE",
    )


def test_quota_conservatively_shares_pending_and_never_resets_from_local_date(tmp_path):
    store = Store(tmp_path)
    owner = store.create({"kind": "quota-owner"})
    policy = IrisQuotaPolicy(scope="shared", reserve=1, max_pending=1)
    a = QuotaLedger(store, owner, policy)
    b = QuotaLedger(store, store.create({"kind": "other-owner"}), policy)
    assert a.reserve("a", "11111111", "cpp1")["status"] == "WAITING_FOR_QUOTA"
    a.observe(snapshot(used=7, limit=10))
    assert a.reserve("a", "11111111", "cpp1")["status"] == "RESERVED"
    assert b.reserve("b", "22222222", "cpp2")["status"] == "WAITING_IRIS_SLOT"
    assert a.status()["remaining_conservative"] == 1
    assert a.reserve("a", "11111111", "cpp1")["status"] == "RESERVED"
    a.completed("11111111")
    assert b.reserve("b", "22222222", "cpp2")["status"] == "RESERVED"
    b.completed("22222222")
    assert a.reserve("c", "33333333", "cpp3")["status"] == "WAITING_FOR_QUOTA"
    a.observe(snapshot(used=9, limit=10, rid="22222222"))
    assert a.status()["uncovered_attempts"] == 0
    assert a.status()["remaining_conservative"] == 0
    with pytest.raises(AdmissionError, match="identical"):
        a.reserve("other", "44444444", "cpp1")
    a.observe(snapshot(used=0, limit=10, age=90000))  # Older snapshot cannot reset quota.
    assert a.status()["remaining_conservative"] == 0
    a.observe(
        snapshot(used=0, limit=10, rid="55555555")
    )  # Actual newer service acknowledgement can.
    assert a.status()["remaining_conservative"] == 9
    assert a.reserve("c", "33333333", "cpp3")["status"] == "RESERVED"


def test_stale_snapshot_and_explicit_quota_refusal_need_new_evidence(tmp_path):
    store = Store(tmp_path)
    owner = store.create({"kind": "quota-owner"})
    ledger = QuotaLedger(store, owner, IrisQuotaPolicy(scope="account"))
    ledger.observe(snapshot(age=90000))
    assert ledger.status()["status"] == "QUOTA_REFRESH_REQUIRED"
    ledger.observe(snapshot())
    ledger.reserve("a", "11111111", "one")
    ledger.completed("11111111", quota_refused=True)
    assert ledger.status()["status"] == "QUOTA_REFRESH_REQUIRED"
    ledger.observe(snapshot(used=1, rid="11111111"))
    assert ledger.status()["status"] == "AVAILABLE"


class RollingDirector:
    def __init__(self):
        self.calls = 0

    def identity(self):
        return {"provider": "rolling-fixture-not-real-LLM"}

    def complete(self, prompt, schema, directory):
        context = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        self.calls += 1
        settings = {"take_profit_bps": 20 + self.calls}
        return {
            "answer": {
                "schema_version": 1,
                "kind": "ResearchDirections",
                "context_id": context["context_id"],
                "analysis_summary": "Declared fixture with distinct interventions and an explicit terminal reason",
                "experiments": [
                    {
                        "schema_version": 1,
                        "kind": "IrisExperiment",
                        "label": f"exit{self.calls}",
                        "family": "exit",
                        "hypothesis": "Artificial controlled hypothesis",
                        "falsification": "No measured objective improvement",
                        "settings": settings,
                        "evidence_refs": context["visible_evidence_ids"],
                        "origin": "agent_proposed",
                    }
                ],
                "capability_requests": [],
                "stop_reason": "Declared finite control complete" if self.calls >= 12 else None,
            },
            "error": None,
            "cost": Cost(llm_tokens=100).model_dump(),
            "usage": {"input_tokens": 80, "output_tokens": 20},
        }


def create_study(tmp_path, env):
    store = Store(tmp_path)
    provider = RollingDirector()
    backend = FixtureIrisTransport(env)
    engine = IrisContinuousStudy(
        store, provider_factory=lambda _: provider, transport_factory=lambda _: backend
    )
    q = IrisQueueSpec(goal="Maximize artificial entry-only PL and retain all risk", environment=env)
    spec = IrisContinuousSpec(
        tracks=(IrisResearchTrack(label="long", smoke=q, development=q),),
        quota_policy=IrisQuotaPolicy(scope="toy-account"),
        owner_instruction="Human authorizes rolling artificial research; no fixed eight-run cap",
        directions=("Explore exits with causal evidence",),
    )
    owner = engine.create(spec)
    return store, engine, owner, backend, provider


def test_rolling_research_exceeds_eight_then_stops_from_recorded_research_reason(
    tmp_path, iris_environment, monkeypatch
):
    store, engine, owner, backend, provider = create_study(tmp_path, iris_environment)
    assert engine.refresh_quota(owner, [])["status"] == "AVAILABLE"
    for _ in range(130):
        value = engine.advance(owner)
        if value["status"] == "CLOSED":
            break
    assert value["status"] == "CLOSED"
    assert len(backend.submissions) == 13  # One baseline plus twelve novel candidates.
    assert provider.calls == 12
    report = continuous_report(store, owner)
    assert len(report["programs"]) == 5
    assert report["verification"]["external_calls"] == 0
    assert all(
        p["report"]["program_verification"]["status"] == "VERIFIED" for p in report["programs"]
    )
    assert report["quota"]["active"] == 0
    monkeypatch.setattr(backend, "submit", lambda *_: pytest.fail("audit submitted"))
    monkeypatch.setattr(provider, "complete", lambda *_: pytest.fail("audit called LLM"))
    assert continuous_report(store, owner)["verification"]["status"] == "VERIFIED"
    assert engine.advance(owner)["status"] == "CLOSED"
    with store.transaction() as conn:
        conn.execute(
            "UPDATE iris_quota_attempts SET status='ACTIVE' WHERE request_id=?",
            (backend.submissions[0],),
        )
    with pytest.raises(IntegrityError, match="quota accounting"):
        continuous_report(store, owner)


def test_unknown_remote_work_holds_shared_slot_and_does_not_resubmit(tmp_path, iris_environment):
    store, engine, owner, backend, provider = create_study(tmp_path, iris_environment)
    engine.refresh_quota(owner, [])
    engine.advance(owner)  # baseline upload
    backend.collect = lambda request, directory: {
        "status": "UNKNOWN",
        "request_id": request["request_id"],
    }
    assert engine.advance(owner)["status"] == "NEEDS_RECONCILIATION"
    assert engine.advance(owner)["status"] == "NEEDS_RECONCILIATION"
    assert len(backend.submissions) == 1 and provider.calls == 0
    assert continuous_report(store, owner)["quota"]["active"] == 1


def test_continuous_coordinator_has_one_global_lease(tmp_path, iris_environment):
    store, engine, owner, backend, provider = create_study(tmp_path, iris_environment)
    with store.campaign_lock(owner, scope="continuous-run"):
        with pytest.raises(JarvisError, match="active coordinator"):
            engine.advance(owner)
    assert not backend.submissions and provider.calls == 0


def test_rejected_hypothesis_is_feedback_for_a_new_epoch_instead_of_stalling(
    tmp_path, iris_environment
):
    from jarvis.iris_contracts import IrisAuthorization
    from jarvis.research_program import ResearchProgram, ResearchProgramSpec, program_report

    class RecoveringDirector(RollingDirector):
        def complete(self, prompt, schema, directory):
            result = super().complete(prompt, schema, directory)
            if self.calls == 1:
                result["answer"]["experiments"][0]["settings"] = {"stop_loss_bps": 30}
            else:
                context = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
                assert context["admission_feedback"][0]["rejected"]
            return result

    store = Store(tmp_path)
    backend = FixtureIrisTransport(iris_environment)
    provider = RecoveringDirector()
    engine = ResearchProgram(
        store, provider_factory=lambda _: provider, transport_factory=lambda _: backend
    )
    q = IrisQueueSpec(
        goal="Maximize artificial PL",
        environment=iris_environment,
        authorization=IrisAuthorization(
            environment_id=digest(iris_environment),
            max_submissions=4,
            expires_at=None,
            owner_note="Artificial bounded control",
        ),
    )
    pid = engine.create(
        ResearchProgramSpec(
            queue=q,
            directions=("Explore supported exit settings",),
            max_direction_batches=2,
            experiments_per_batch=1,
        )
    )
    for _ in range(20):
        if engine.advance(pid)["status"] == "CLOSED":
            break
    assert store.status(pid) == "CLOSED" and provider.calls == 2
    assert len(backend.submissions) == 2
    report = program_report(store, pid)
    assert report["direction_batches"][0]["rejected"]
    assert report["direction_batches"][1]["accepted"]


def test_model_watcher_waits_without_creating_or_submitting(
    tmp_path, iris_environment, monkeypatch
):
    from jarvis.iris_watch import watch_research

    store, engine, owner, backend, provider = create_study(tmp_path / "store", iris_environment)
    monkeypatch.setattr(
        "jarvis.iris_watch.model_status", lambda _: {"status": "WAITING_FOR_MODEL_ACCEPTANCE"}
    )
    monkeypatch.setattr("jarvis.iris_watch.time.sleep", lambda _: None)
    state = watch_research(
        store,
        tmp_path,
        tmp_path / "python",
        "standing instruction",
        tmp_path / "state.json",
        engine=engine,
        max_steps=2,
    )
    assert state["status"] == "WAITING_FOR_MODEL_ACCEPTANCE" and "study_id" not in state
    assert not backend.submissions and provider.calls == 0


def test_model_watcher_admits_then_resumes_completed_work_without_external_calls(
    tmp_path, iris_environment, monkeypatch
):
    from jarvis.iris_watch import watch_research

    store, engine, unused, backend, provider = create_study(tmp_path / "store", iris_environment)
    spec = IrisContinuousSpec.model_validate(store.manifest(unused)["spec"])
    monkeypatch.setattr("jarvis.iris_watch.model_status", lambda _: {"status": "READY"})
    monkeypatch.setattr("jarvis.iris_watch.prepare_continuous", lambda *_: spec)
    monkeypatch.setattr("jarvis.iris_watch.time.sleep", lambda _: None)
    args = (store, tmp_path, tmp_path / "python", spec.owner_instruction, tmp_path / "state.json")
    state = watch_research(*args, engine=engine, max_steps=150)
    assert state["status"] == "CLOSED" and len(backend.submissions) == 13
    monkeypatch.setattr(backend, "quota", lambda *_: pytest.fail("closed watcher read quota"))
    monkeypatch.setattr(provider, "complete", lambda *_: pytest.fail("closed watcher called LLM"))
    assert watch_research(*args, engine=engine, max_steps=2) == state
