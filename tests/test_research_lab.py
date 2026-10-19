import json
import threading

import pytest
from pydantic import ValidationError

from jarvis.contracts import Cost, IntegrityError, JarvisError
from jarvis.lab_contracts import HumanInput, LabPolicy, LabSpec, ResearchMethod
from jarvis.lab_evolution import (
    LabEvolutionSpec,
    adopt_profile,
    compare_episodes,
    evolution_report,
    replay_lab,
)
from jarvis.lab_fixture import (
    LabFixtureProvider,
    finish_lab,
    fixture_lab_engine,
    fixture_lab_spec,
    run_improvement_fixture,
)
from jarvis.lab_memory import ResearchBoard, capability_definition, retrieve, verify_experience
from jarvis.lab_tools import execute_capability, fidelity_check, run_tool
from jarvis.research_lab import ResearchLab, lab_report, payloads
from jarvis.research_roles import RoleCaller
from jarvis.storage import Store


@pytest.fixture(scope="module")
def full_loop(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("research-self-improvement"))
    result = run_improvement_fixture(store)
    return (store, result)


def test_all_stages_method_rollback_capability_and_policy_transfer(full_loop):
    store, result = full_loop
    assert result["status"] == "VERIFIED"
    assert result["foundation"]["scores"] == [1.0, 4.0, 4.75]
    assert result["foundation"]["memory_comparison"]["strict_gain"]
    assert {k: v["outcome"] for k, v in result["studies"].items()} == {
        "method": "ROLLED_BACK",
        "capability": "APPROVED_FOR_RESEARCH",
        "policy": "APPROVED_FOR_RESEARCH",
    }
    assert result["negative_memory_control"]["accepted"] == []
    assert result["negative_memory_control"]["rejected"]
    assert result["replay"] == {
        "exact": "COMPLETE",
        "unrecorded": "INSUFFICIENT_COVERAGE",
        "external_calls": 0,
    }
    assert result["external_llm_calls"] == result["remote_iris_submissions"] == 0
    for study in result["studies"].values():
        assert not any(study["full_usage"]["pending"].values())
        report = evolution_report(store, study["study"])
        assert report["verification"]["external_calls"] == 0
        assert report["next_cycle"][0]["old_validation_reused"] is False
        assert lab_report(store, study["subsequent_episode"])["status"] == "CLOSED"


def test_new_task_uses_evidence_and_negative_control_refuses_it(full_loop):
    store, result = full_loop
    episode = result["foundation"]["reuse"]
    spec = LabSpec.model_validate(store.manifest(episode)["spec"])
    memory = retrieve(store, spec, before=store.manifest(episode)["created_at"])
    assert len(memory["accepted"]) == 1
    record = verify_experience(store, spec.experience_refs[0])
    assert record.interpretation_status == "PROVISIONAL"
    assert record.proposal.counter_evidence_refs
    support = store.artifacts.get(payloads(store, episode, "LAB_SUPPORT_FROZEN")[0]["support_ref"])
    retrieved = next(o for o in support["observations"] if o["action"] == "retrieve")
    assert retrieved["value"]["accepted"][0]["experience_ref"] == spec.experience_refs[0]
    assert (
        lab_report(store, episode)["program"]["best_development_candidate"]["experiment"]["family"]
        == "exit"
    )


def test_human_board_snapshot_is_append_only_and_episode_is_pinned(tmp_path):
    store = Store(tmp_path)
    board = ResearchBoard(store)
    owner = board.create("Artificial research")
    idea = store.artifacts.put(HumanInput(category="idea", text="Check a competing mechanism"))
    board.add(owner, idea)
    board.add(owner, idea)
    old = board.snapshot(owner)
    assert len(old["items"]) == 1
    engine, _ = fixture_lab_engine(store)
    episode = engine.create(fixture_lab_spec(store).model_copy(update={"human_refs": (idea,)}))
    priority = store.artifacts.put(HumanInput(category="priority", text="Prefer diagnostics next"))
    board.add(owner, priority)
    assert len(board.snapshot(owner)["items"]) == 2 and len(old["items"]) == 1
    assert store.manifest(episode)["spec"]["human_refs"] == [idea]
    report = finish_lab(engine, episode)
    assert report["status"] == "CLOSED"


def test_independent_specialists_actually_overlap_and_then_cross_question(tmp_path):
    barrier = threading.Barrier(2)

    class ParallelControl(LabFixtureProvider):
        def complete(self, prompt, schema, directory):
            context = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
            if schema.get("title") == "ExpertOpinion" and context["phase"] == "independent":
                assert "observations" not in context
                barrier.wait(timeout=10)
            return super().complete(prompt, schema, directory)

    store = Store(tmp_path)
    base, backends = fixture_lab_engine(store)
    engine = ResearchLab(
        store,
        provider_factory=lambda _: ParallelControl(),
        transport_factory=backends,
        search=base.search,
    )
    owner = engine.create(fixture_lab_spec(store))
    report = finish_lab(engine, owner)
    initial = [
        store.artifacts.get(p["answer_ref"])
        for p in report["opinions"]
        if p["key"].startswith("initial:")
    ]
    replies = [p for p in report["opinions"] if p["key"].startswith("discussion:")]
    assert len(initial) == 2 and len(replies) == 2
    assert initial[0]["hypothesis"] != initial[1]["hypothesis"]
    contexts = [
        store.artifacts.get(p["context_ref"])
        for p in payloads(store, owner, "LAB_ROLE_REQUESTED")
        if p["key"].startswith("initial:")
    ]
    assert [p["action"] for p in contexts[0]["preparation"]] == ["retrieve", "search"]
    assert contexts[0]["preparation"] == contexts[1]["preparation"]
    assert contexts[0]["visible_evidence_ids"] == contexts[1]["visible_evidence_ids"]


def test_closed_audit_and_replay_never_invoke_provider_tool_or_backend(full_loop, monkeypatch):
    store, result = full_loop
    before = {
        s["study"]: store.resource_totals_tree(s["study"]) for s in result["studies"].values()
    }

    def forbidden(*args, **kwargs):
        pytest.fail("offline audit invoked a paid/external operation")

    monkeypatch.setattr(RoleCaller, "call", forbidden)
    monkeypatch.setattr("jarvis.research_lab.run_tool", forbidden)
    monkeypatch.setattr("jarvis.research_queue.IrisResearchQueue.tick", forbidden)
    for s in result["studies"].values():
        assert evolution_report(store, s["study"])["verification"]["external_calls"] == 0
        assert store.resource_totals_tree(s["study"]) == before[s["study"]]


@pytest.mark.parametrize("control", ["PAUSED", "CANCELLED"])
def test_owner_stop_before_any_actor(tmp_path, control):
    store = Store(tmp_path)
    engine, backends = fixture_lab_engine(store)
    owner = engine.create(fixture_lab_spec(store))
    store.control(owner, control)
    assert engine.advance(owner)["status"] == control
    assert store.jobs(owner) == [] and backends.values == {}
    with pytest.raises(JarvisError, match="owner stop"):
        engine.propose_capability(owner)
    assert store.jobs(owner) == []


def test_unknown_tool_keeps_reservation_and_never_retries(tmp_path):
    store = Store(tmp_path)
    engine, _ = fixture_lab_engine(store)
    owner = engine.create(fixture_lab_spec(store))
    request = {
        "kind": "LabTool",
        "key": "unknown",
        "request": {"x": 1},
        "world_id": store.manifest(owner)["world_id"],
    }
    job = store.reserve(owner, request, Cost(tool_calls=1, compute_units=1))
    store.start_job(job)
    with pytest.raises(JarvisError, match="no resubmission"):
        run_tool(store, owner, "unknown", {"x": 1}, lambda: pytest.fail("resubmitted unknown tool"))
    assert store.resource_totals_tree(owner)["pending"]["tool_calls"] == 1


def test_unknown_specialist_keeps_token_reservation(tmp_path, monkeypatch):
    store = Store(tmp_path)
    engine, _ = fixture_lab_engine(store)
    owner = engine.create(fixture_lab_spec(store))
    original = RoleCaller.call

    def fault(self, scope, role, context, schema, **kwargs):
        if role == "team_director":
            kwargs["fault"] = "after_start"
        return original(self, scope, role, context, schema, **kwargs)

    monkeypatch.setattr(RoleCaller, "call", fault)
    with pytest.raises(JarvisError, match="injected"):
        for _ in range(8):
            engine.advance(owner)
    assert store.resource_totals_tree(owner)["pending"]["llm_tokens"] == 1000


def test_shared_total_budget_cannot_skip_specialist_or_critic(tmp_path):
    store = Store(tmp_path)
    engine, _ = fixture_lab_engine(store)
    spec = fixture_lab_spec(store)
    owner = engine.create(
        spec.model_copy(update={"budget": spec.budget.model_copy(update={"max_llm_tokens": 500})})
    )
    for _ in range(10):
        result = engine.advance(owner)
        if result["status"] == "BUDGET_EXHAUSTED":
            break
    assert result["status"] == "BUDGET_EXHAUSTED"
    assert not payloads(store, owner, "LAB_PROGRAM_CREATED")


def test_capability_is_closed_recipe_with_fixed_fidelity(full_loop):
    store, result = full_loop
    cap = capability_definition(store, result["foundation"]["capability"])
    assert fidelity_check(cap)["status"] == "FIDELITY_TESTED"
    assert execute_capability(cap, {"b": (1.0, 3.0), "a": (5.0,)})["best_group"] == "a"
    with pytest.raises(JarvisError, match="invalid"):
        execute_capability(cap, {"x": (float("nan"),)})
    raw = cap.model_dump()
    raw["operation"] = "exec"
    with pytest.raises(ValidationError):
        type(cap).model_validate(raw)


def test_method_cannot_mutate_evaluator_or_permissions():
    with pytest.raises(ValidationError):
        ResearchMethod.model_validate({"name": "unsafe", "evaluator": "replacement"})
    with pytest.raises(ValidationError):
        LabPolicy(name="invalid", actions=("discuss",))


def test_comparison_rejects_unequal_resource_cap(full_loop, monkeypatch):
    store, result = full_loop
    study = result["studies"]["method"]["study"]
    phase = store.artifacts.get(payloads(store, study, "EVOLUTION_PHASE")[0]["result_ref"])
    pair = phase["pairs"][0]
    original = store.manifest

    def changed(owner):
        value = json.loads(json.dumps(original(owner)))
        if owner == pair["candidate"]:
            value["spec"]["budget"]["max_llm_tokens"] += 1
        return value

    monkeypatch.setattr(store, "manifest", changed)
    with pytest.raises(IntegrityError, match="second improvement axis"):
        compare_episodes(store, pair["incumbent"], pair["candidate"], "method")


def test_profile_cannot_authorize_observed_backend(full_loop):
    store, result = full_loop
    study = result["studies"]["method"]["study"]
    declaration = LabEvolutionSpec.model_validate(store.manifest(study)["spec"])
    spec = declaration.continuation[0]
    env = spec.program.queue.environment.model_copy(
        update={"result_origin": "OBSERVED_IRIS_BACKTEST"}
    )
    changed = spec.model_copy(
        update={
            "allow_observed_backend": True,
            "program": spec.program.model_copy(
                update={"queue": spec.program.queue.model_copy(update={"environment": env})}
            ),
        }
    )
    with pytest.raises(IntegrityError, match="protocol"):
        adopt_profile(store, changed, study)


def test_profile_cannot_silently_combine_unvalidated_improvement_axes(full_loop):
    store, result = full_loop
    study = result["studies"]["capability"]["study"]
    declaration = LabEvolutionSpec.model_validate(store.manifest(study)["spec"])
    task = declaration.continuation[0]
    task = task.model_copy(update={"method": ResearchMethod(name="Unvalidated-method")})
    with pytest.raises(IntegrityError, match="joint validation"):
        adopt_profile(store, task, study)


def test_replay_incomplete_episode_has_no_invented_future_actions(tmp_path):
    store = Store(tmp_path)
    engine, _ = fixture_lab_engine(store)
    spec = fixture_lab_spec(store)
    episode = engine.create(spec)
    assert replay_lab(store, episode, spec.policy) == {
        "status": "INSUFFICIENT_COVERAGE",
        "unknown_action": "retrieve",
        "trace": [],
        "external_calls": 0,
    }
    assert store.jobs(episode) == []


def test_support_cannot_substitute_preparation_observations(full_loop, monkeypatch):
    store, result = full_loop
    episode = result["foundation"]["episode"]
    ref = payloads(store, episode, "LAB_SUPPORT_FROZEN")[0]["support_ref"]
    original = store.artifacts.get

    def substituted(key):
        value = original(key)
        if key == ref:
            value = {**value, "observations": []}
        return value

    monkeypatch.setattr(store.artifacts, "get", substituted)
    with pytest.raises(IntegrityError, match="support omitted"):
        lab_report(store, episode)


def test_heldout_family_cannot_be_renamed_into_development(full_loop):
    store, result = full_loop
    declaration = LabEvolutionSpec.model_validate(
        store.manifest(result["studies"]["method"]["study"])["spec"]
    )
    raw = declaration.model_dump(mode="json")
    raw["heldout"][0] = raw["development"][0]
    with pytest.raises(ValidationError, match="overlap"):
        LabEvolutionSpec.model_validate(raw)


def test_board_feedback_is_automatically_pinned_for_new_episodes(tmp_path):
    store = Store(tmp_path)
    board = ResearchBoard(store)
    owner = board.create("Synthetic issue")
    first = store.artifacts.put(HumanInput(category="idea", text="A human hypothesis"))
    board.add(owner, first)
    engine, _ = fixture_lab_engine(store)
    spec = fixture_lab_spec(store).model_copy(update={"board_id": owner})
    a = engine.create(spec)
    second = store.artifacts.put(
        HumanInput(category="priority", text="Investigate a competing explanation")
    )
    board.add(owner, second)
    b = engine.create(spec)
    assert store.manifest(a)["spec"]["human_refs"] == [first]
    assert store.manifest(b)["spec"]["human_refs"] == [first, second]
    assert (
        store.manifest(a)["spec"]["board_snapshot_ref"]
        != store.manifest(b)["spec"]["board_snapshot_ref"]
    )
    assert finish_lab(engine, a)["status"] == "CLOSED"


def test_known_role_receipt_resumes_without_second_provider_call(tmp_path, monkeypatch):
    store = Store(tmp_path)
    engine, _ = fixture_lab_engine(store)
    owner = engine.create(fixture_lab_spec(store))
    original = RoleCaller.call
    injected = False

    def fault(self, scope, role, context, schema, **kwargs):
        nonlocal injected
        if role == "team_director" and (not injected):
            injected = True
            kwargs["fault"] = "after_receipt"
        return original(self, scope, role, context, schema, **kwargs)

    monkeypatch.setattr(RoleCaller, "call", fault)
    with pytest.raises(JarvisError, match="receipt interruption"):
        for _ in range(8):
            engine.advance(owner)
    engine.resume_receipts(owner)
    assert finish_lab(engine, owner)["status"] == "CLOSED"
    jobs = [
        j
        for j in store.jobs(owner)
        if store.artifacts.get(j["request_ref"]).get("role") == "team_director"
    ]
    assert len(jobs) == 1 and jobs[0]["finalized"]


def test_unknown_receipt_cannot_be_cleared_by_resume(tmp_path):
    store = Store(tmp_path)
    engine, _ = fixture_lab_engine(store)
    owner = engine.create(fixture_lab_spec(store))
    job = store.reserve(owner, {"unknown": True}, Cost(llm_tokens=100))
    store.start_job(job)
    with pytest.raises(JarvisError, match="reconciliation"):
        engine.resume_receipts(owner)
    assert store.resource_totals_tree(owner)["pending"]["llm_tokens"] == 100


def test_audit_cache_does_not_hide_later_artifact_tampering(full_loop, tmp_path):
    import shutil

    original, result = full_loop
    copied = tmp_path / "copy"
    shutil.copytree(original.root, copied)
    store = Store(copied)
    owner = result["foundation"]["episode"]
    assert lab_report(store, owner)["verification"]["status"] == "VERIFIED"
    ref = payloads(store, owner, "LAB_ROLE_RECORDED")[0]["answer_ref"]
    store.artifacts.path(ref).write_text('{"changed":true}')
    with pytest.raises(IntegrityError):
        lab_report(store, owner)


def test_duplicate_finalist_is_rejected(full_loop, monkeypatch):
    store, result = full_loop
    owner = result["studies"]["method"]["study"]
    original = store.events

    def forged(scope):
        events = original(scope)
        if scope == owner:
            events = [*events, next(e for e in events if e["event_type"] == "EVOLUTION_FINALIST")]
        return events

    monkeypatch.setattr(store, "events", forged)
    with pytest.raises(IntegrityError, match="multiple frozen"):
        evolution_report(store, owner)


def test_profile_adoption_checks_current_provider(full_loop):
    store, result = full_loop
    owner = result["studies"]["policy"]["study"]
    spec = LabEvolutionSpec.model_validate(store.manifest(owner)["spec"]).continuation[0]
    selected = adopt_profile(store, spec, owner)

    class ChangedProvider(LabFixtureProvider):
        def identity(self):
            return {**super().identity(), "model": "different"}

    base, backends = fixture_lab_engine(store)
    engine = ResearchLab(
        store,
        provider_factory=lambda _: ChangedProvider(),
        transport_factory=backends,
        search=base.search,
    )
    with pytest.raises(IntegrityError, match="provider/version"):
        engine.create(selected)


def test_crossref_and_implementation_sources_preserve_read_scope(monkeypatch):
    import urllib.request

    from jarvis.lab_tools import CrossrefSearch, GitHubImplementationSearch

    class Response:
        def __init__(self, value, url):
            self.value, self.url = (value, url)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return json.dumps(self.value).encode()

        def geturl(self):
            return self.url

    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith("https://api.crossref.org/works?")
            return Response(
                {
                    "message": {
                        "items": [
                            {
                                "DOI": "10.example/control",
                                "title": ["Primary metadata"],
                                "abstract": "<p>A bounded method.</p>",
                                "indexed": {"timestamp": 1},
                            }
                        ]
                    }
                },
                request.full_url,
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: Opener())
    paper = CrossrefSearch().search("public method")[0]
    assert paper.read_scope == "abstract" and paper.origin == "external"

    def github(request, timeout):
        assert request.full_url.startswith("https://api.github.com/search/repositories?")
        return Response(
            {
                "items": [
                    {
                        "html_url": "https://github.com/example/repo",
                        "full_name": "example/repo",
                        "description": "Implementation description",
                        "updated_at": "version",
                        "license": {"spdx_id": "MIT"},
                    }
                ]
            },
            request.full_url,
        )

    monkeypatch.setattr(urllib.request, "urlopen", github)
    implementation = GitHubImplementationSearch().search("public implementation")[0]
    assert (
        implementation.read_scope == "metadata"
        and "not reviewed or executed" in implementation.license_note
    )


def test_external_source_error_is_charged_and_not_silently_retried(tmp_path):
    store = Store(tmp_path)
    base, backends = fixture_lab_engine(store)

    class BrokenSearch:
        def identity(self):
            return {"adapter": "broken-control"}

        def search(self, question):
            raise RuntimeError("source unavailable")

    engine = ResearchLab(
        store,
        provider_factory=base.provider_factory,
        transport_factory=backends,
        search=BrokenSearch(),
    )
    spec = fixture_lab_spec(store).model_copy(
        update={
            "allow_external_search": True,
            "public_search_question": "owner-declared public control",
        }
    )
    owner = engine.create(spec)
    engine.advance(owner)
    with pytest.raises(JarvisError, match="charged"):
        engine.advance(owner)
    assert store.status(owner) == "LAB_NEEDS_ATTENTION"
    assert not payloads(store, owner, "LAB_PROGRAM_CREATED")
    assert store.resource_totals_tree(owner)["actual"]["tool_calls"] == 2


def test_reuse_task_has_changed_mathematical_instance(full_loop):
    store, result = full_loop
    a = store.manifest(result["foundation"]["episode"])["spec"]
    b = store.manifest(result["foundation"]["reuse"])["spec"]
    assert (
        a["program"]["queue"]["environment"]["model_lineage"]
        != b["program"]["queue"]["environment"]["model_lineage"]
    )
    report = lab_report(store, result["foundation"]["episode"])
    requests = payloads(store, result["foundation"]["episode"], "LAB_ROLE_REQUESTED")
    assert any(p["role"] == "capability_selector" for p in requests)
    assert (
        report["program"]["best_development_candidate"]["result"]["entry_only_pl"]
        != lab_report(store, result["foundation"]["reuse"])["program"][
            "best_development_candidate"
        ]["result"]["entry_only_pl"]
    )
