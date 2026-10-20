import json

import pytest

from jarvis.contracts import AdmissionError, Budget, Cost, JarvisError
from jarvis.iris_lab_session import IrisLabSession, IrisLabSessionSpec
from jarvis.iris_mission import IrisMissionSpec, IrisResearchMission
from jarvis.iris_quota import IrisQuotaPolicy
from jarvis.lab_contracts import ExperienceProposal
from jarvis.lab_fixture import LabFixtureProvider, finish_lab, fixture_lab_engine, fixture_lab_spec
from jarvis.lab_memory import verify_experience
from jarvis.lab_recovery import ExperienceRecovery
from jarvis.mission_runner import run_mission
from jarvis.research_lab import ResearchLab, payloads
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


class BrokenCurator(LabFixtureProvider):
    def __init__(self, count=1):
        self.remaining = count
        self.calls = 0

    def complete(self, prompt, schema, directory):
        self.calls += 1
        response = super().complete(prompt, schema, directory)
        if schema["title"] == "ExperienceProposal" and self.remaining:
            self.remaining -= 1
            response["answer"]["evidence_refs"] = ["sha256:invalid-source-not-result"]
        return response


def test_known_invalid_curator_has_bounded_receipted_correction(tmp_path):
    store = Store(tmp_path)
    base, backends = fixture_lab_engine(store)
    provider = BrokenCurator()
    engine = ResearchLab(
        store, provider_factory=lambda _: provider, transport_factory=backends, search=base.search
    )
    owner = engine.create(fixture_lab_spec(store))
    report = finish_lab(engine, owner)
    rejected = payloads(store, owner, "LAB_ROLE_REJECTED")
    assert report["status"] == "CLOSED" and len(rejected) == 1
    assert store.artifacts.get(rejected[0]["answer_ref"])["evidence_refs"] == [
        "sha256:invalid-source-not-result"
    ]
    assert any(p["key"] == "curate:repair:1" for p in payloads(store, owner, "LAB_ROLE_RECORDED"))
    request = next(p for p in payloads(store, owner, "LAB_ROLE_REQUESTED") if p["key"] == "curate")
    context = store.artifacts.get(request["context_ref"])
    schema = store.artifacts.get(request["schema_ref"])
    assert "evidence" not in context
    assert schema["properties"]["evidence_refs"]["items"]["enum"] == context["visible_evidence_ids"]
    old_calls = provider.calls
    finish_lab(engine, owner)
    assert provider.calls == old_calls
    assert not any(store.resource_totals_tree(owner)["pending"].values())


def test_critic_sees_proposed_diff_and_only_decision_time_execution(tmp_path):
    from jarvis.execution_evidence import execution_evidence

    store = Store(tmp_path)
    engine, _ = fixture_lab_engine(store)
    owner = engine.create(fixture_lab_spec(store))
    finish_lab(engine, owner)
    program = payloads(store, owner, "LAB_PROGRAM_CREATED")[0]["program_id"]
    requests = [
        e
        for e in store.events(program)
        if e["event_type"] == "PROGRAM_ROLE_REQUESTED" and e["payload"]["role"] == "critic"
    ]
    pre = store.artifacts.get(requests[0]["payload"]["context_ref"])
    post = store.artifacts.get(requests[-1]["payload"]["context_ref"])
    assert pre["trusted_proposal_controls"][0]["local_diff"]
    assert pre["trusted_proposal_controls"][0]["status"] == "RENDERED_NOT_YET_EXECUTED"
    q = pre["queue_id"]
    assert (
        execution_evidence(store, [q], before=requests[0]["recorded_at_utc"])
        == pre["trusted_execution_evidence"]
    )
    assert len(pre["trusted_execution_evidence"]) == 1
    assert len(post["trusted_execution_evidence"]) == 2
    assert all(p["status"] == "SUCCEEDED" for p in post["trusted_execution_evidence"])


def test_completed_history_recovered_without_rewriting_or_backend_reexecution(tmp_path):
    store = Store(tmp_path)
    base, backends = fixture_lab_engine(store)
    broken = BrokenCurator(100)
    engine = ResearchLab(
        store, provider_factory=lambda _: broken, transport_factory=backends, search=base.search
    )
    episode = engine.create(fixture_lab_spec(store))
    with pytest.raises(AdmissionError, match="corrective attempts"):
        finish_lab(engine, episode)
    old_manifest = store.manifest(episode)
    old_events = store.events(episode)
    completed = sum(len(store.jobs(m)) for m in store.resource_members(episode))
    recovery = ExperienceRecovery(store, lambda _: LabFixtureProvider())
    owner = recovery.create(episode, "Fixture explicit successor recovery")
    result = recovery.advance(owner)
    assert result["status"] == "CLOSED" and result["backend_calls"] == 0
    assert verify_experience(store, result["experience_ref"]).producer == owner
    assert store.manifest(episode) == old_manifest and store.events(episode) == old_events
    assert sum(len(store.jobs(m)) for m in store.resource_members(episode)) == completed
    assert recovery.advance(owner)["experience_ref"] == result["experience_ref"]


def test_unknown_curator_is_not_retried(tmp_path):
    class UnknownProvider(LabFixtureProvider):
        calls = 0

        def complete(self, prompt, schema, directory):
            self.calls += 1
            return {"answer": None, "error": "unknown", "cost": None}

    store = Store(tmp_path)
    provider = UnknownProvider()
    engine = ResearchLab(store, provider_factory=lambda _: provider)
    owner = engine.create(fixture_lab_spec(store))
    context = {"visible_evidence_ids": ["known"], "experiments": []}
    for _ in range(2):
        with pytest.raises(JarvisError, match="unknown|reservation"):
            engine.call(owner, "curate", "curator", context, ExperienceProposal)
    assert provider.calls == 1 and not payloads(store, owner, "LAB_ROLE_REJECTED")
    assert store.resource_totals(owner)["pending"]["llm_tokens"] > 0


def mission_fixture(store):
    class Controller(LabFixtureProvider):
        def complete(self, prompt, schema, directory):
            if schema["title"] != "MissionDecision":
                return super().complete(prompt, schema, directory)
            c = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
            answer = {
                "schema_version": 1,
                "context_id": c["context_id"],
                "action": "continue" if c["wave"] == 1 else "stop",
                "template_index": 0,
                "directions": ["Probe another controlled hypothesis"] if c["wave"] == 1 else [],
                "reason": "Declared synthetic continuation then measured duplicate-candidate plateau",
                "evidence_refs": c["visible_evidence_ids"][:2],
            }
            return {
                "answer": answer,
                "cost": Cost(llm_tokens=100).model_dump(),
                "usage": {"origin": "SIMULATED_TOKEN_UNITS"},
            }

    base, backends = fixture_lab_engine(store)
    lab = ResearchLab(
        store,
        provider_factory=lambda _: Controller(),
        transport_factory=backends,
        search=base.search,
    )
    config = AgentConfig(
        model="gpt-6.1-sol", reasoning_effort="high", reserve_tokens=1000, max_calls=32
    )
    task = fixture_lab_spec(store)
    task = task.model_copy(
        update={
            "role_config": config,
            "program": task.program.model_copy(update={"agent": config, "critic": config}),
        }
    )
    initial = IrisLabSessionSpec(
        stages=(task,),
        phases=("development",),
        quota_policy=IrisQuotaPolicy(scope="mission-fixture"),
        owner_instruction="Declared artificial mission",
        model=config.model,
        reasoning_effort="high",
        budget=Budget(
            max_actions=1000,
            max_tool_calls=1000,
            max_llm_tokens=100000,
            max_compute_units=1000,
            max_wall_seconds=10000,
            max_parallel_jobs=2,
        ),
    )
    sessions = IrisLabSession(store, lab)
    mission = IrisResearchMission(store, sessions)
    owner = mission.create(
        IrisMissionSpec(
            initial=initial,
            continuation_templates=(task,),
            controller=config,
            owner_instruction="Fixture continuation: retain fixed evaluator and do not repeat completed candidates",
            budget=initial.budget,
        )
    )
    return mission, owner


def test_mission_continues_beyond_one_session_and_records_reasoned_stop(tmp_path):
    store = Store(tmp_path)
    engine, owner = mission_fixture(store)
    for _ in range(100):
        result = engine.advance(owner)
        for link in payloads(store, owner, "MISSION_SESSION_CREATED"):
            if not payloads(store, link["session"], "SESSION_QUOTA_FINISHED"):
                engine.sessions.refresh_quota(link["session"], [])
        if result["status"] == "CLOSED":
            break
    assert result["status"] == "CLOSED"
    assert len(payloads(store, owner, "MISSION_SESSION_CREATED")) == 2
    assert len(payloads(store, owner, "MISSION_DECISION_RECORDED")) == 2
    assert payloads(store, owner, "MISSION_COMPLETED")[0]["reason"]
    assert not any(store.resource_totals_tree(owner)["pending"].values())


def test_runner_failure_finalizes_checkpoint_and_notebook(tmp_path):
    store = Store(tmp_path / "store")
    engine, owner = mission_fixture(store)

    def fail(_):
        raise RuntimeError("injected known local orchestration failure")

    engine.advance = fail
    state = tmp_path / "state.json"
    with pytest.raises(RuntimeError, match="injected"):
        run_mission(
            engine, owner, state, notebook=tmp_path / "notes", poll_seconds=0, max_seconds=10
        )
    actual = json.loads(state.read_text())
    assert (
        actual["status"] == "NEEDS_RECONCILIATION"
        and not actual["process_running"]
        and not actual["task_complete"]
    )
    assert store.artifacts.get(actual["error_ref"])["type"] == "RuntimeError"
    assert (tmp_path / "notes/audit.json").exists()


def test_child_success_is_not_task_completion_and_limit_is_resumable(tmp_path):
    store = Store(tmp_path / "store")
    engine, owner = mission_fixture(store)
    engine.advance = lambda _: {"status": "SUCCEEDED"}
    state = tmp_path / "state.json"
    result = run_mission(
        engine, owner, state, notebook=tmp_path / "notes", poll_seconds=0.01, max_seconds=0.03
    )
    assert result["status"] == "RUNNER_LIMIT" and not result["task_complete"]
    assert store.status(owner) == "READY"
    progress = payloads(store, owner, "MISSION_RUNNER_PROGRESS")
    assert progress[0]["status"] == "RUNNING"
