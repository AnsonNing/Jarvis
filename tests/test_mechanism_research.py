import pytest
import yaml

from jarvis.contracts import AdmissionError, IntegrityError, JarvisError
from jarvis.identity import digest, file_digest
from jarvis.lab_tools import verify_tools
from jarvis.mechanism_cli import preflight
from jarvis.mechanism_fixture import (
    WorkflowFixtureProvider,
    fixture_workflow,
    run_mechanism_fixture,
)
from jarvis.mechanism_research import MechanismResearch, replay_workflow, workflow_report
from jarvis.research_contract import ResearchContract
from jarvis.research_lab import payloads
from jarvis.research_skills import SkillRegistry, diagnostic_case
from jarvis.skill_sandbox import SkillSandbox
from jarvis.storage import Store
from jarvis.trading_diagnostics import DiagnosticInput, attribution, import_snapshot_csv


def test_inspection_before_backtest_preserves_complete_intent_and_questions(tmp_path):
    store = Store(tmp_path)
    engine, owner = fixture_workflow(store)
    assert engine.advance(owner)["action"] == "INSPECT"
    assert engine.advance(owner)["action"] == "INSPECT"
    assert engine.advance(owner)["status"] == "REVIEWED"
    assert not payloads(store, owner, "WORKFLOW_PROGRAM_CREATED")
    contract = store.artifacts.get(store.manifest(owner)["spec"]["contract_ref"])
    for p in payloads(store, owner, "LAB_ROLE_REQUESTED"):
        context = store.artifacts.get(p["context_ref"])
        assert context["research_contract"] == contract
        assert context["baseline_evidence"][0]["value"]["origin"] == "declared_fixture"
        assert context["input_manifests"][0]["available_rows"] == 3
        assert "rows" not in context["input_manifests"][0]
        assert (
            context["experiment_envelope"]["environment_id"]
            == contract["experiment_environment_id"]
        )
    report = workflow_report(store, owner)
    assert len(report["questions"]) == 1 and len(report["questions"][0]["hypotheses"]) == 2
    assert report["usage"]["pending"]["llm_tokens"] == 0


def test_strict_decomposition_retains_unknown_rows_and_reconciles():
    result = attribution(diagnostic_case())
    f = result.facts
    assert f["common"] == 1 and f["changed_paths"] == 0
    assert f["shared_pl_delta"] == "-2" and f["total_pl_delta"] == "5"
    assert f["unmatched_residual"] == "2" and f["unmatched_baseline"] == 1
    assert "added" not in f and "removed" not in f
    assert result.evidence_level == "descriptive_decomposition"
    assert attribution(diagnostic_case(1)).status == "INSUFFICIENT_DATA"
    pair = diagnostic_case()
    missing = pair.model_copy(
        update={"baseline": pair.baseline.model_copy(update={"stable_keyspace": None})}
    )
    assert attribution(missing).status == "INSUFFICIENT_DATA"
    with pytest.raises(ValueError, match="identity mismatch"):
        DiagnosticInput(
            baseline=pair.baseline,
            candidate=pair.candidate.model_copy(update={"model_id": "other"}),
        )


def test_csv_import_requires_explicit_columns_and_preserves_checksum(tmp_path):
    data = diagnostic_case().baseline
    metadata = data.model_dump(exclude={"rows", "source_checksum"}, mode="json")
    path = tmp_path / "supplied.csv"
    path.write_text("key,result,model_score\na,1.25,0.3\n,-0.5,\n")
    snapshot = import_snapshot_csv(
        path, metadata, {"entity_id": "key", "pl": "result", "score": "model_score"}
    )
    assert snapshot.source_checksum == file_digest(path)
    assert snapshot.rows[1].entity_id is None and str(snapshot.rows[0].pl) == "1.25"
    with pytest.raises(AdmissionError, match="missing declared"):
        import_snapshot_csv(path, metadata, {"pl": "not_a_column"})


def test_skill_registry_pins_source_fidelity_scope_and_role(tmp_path):
    store = Store(tmp_path)
    registry = SkillRegistry.create(store)
    ref = registry.define_builtin("trade_attribution", "paired-attribution-v1")
    with pytest.raises(AdmissionError, match="unavailable"):
        registry.dispatch(registry.registry, ref, diagnostic_case())
    proof = registry.validate(registry.registry, ref)
    registry.register(ref, proof)
    registry.register(ref, proof)
    assert registry.snapshot()["skills"] == [ref]
    result = registry.dispatch(registry.registry, ref, diagnostic_case())
    assert result["result"]["facts"]["total_pl_delta"] == "5" and result["utility"] == "UNVERIFIED"
    with pytest.raises(AdmissionError, match="role"):
        registry.dispatch(registry.registry, ref, diagnostic_case(), role="builder")
    changed = store.artifacts.put({**store.artifacts.get(ref), "applicability": "Changed in place"})
    with pytest.raises(IntegrityError, match="immutable"):
        registry.register(changed, registry.validate(registry.registry, changed))
    verify_tools(store, registry.registry)


class BrokenDecision(WorkflowFixtureProvider):
    calls = 0

    def complete(self, *args):
        self.calls += 1
        value = super().complete(*args)
        if self.calls == 1:
            value["answer"]["action"]["question_id"] = "not-declared"
        return value


def test_known_invalid_decision_repairs_with_frozen_receipts_and_audits(tmp_path):
    store = Store(tmp_path)
    original, owner = fixture_workflow(store)
    provider = BrokenDecision()
    engine = MechanismResearch(store, provider_factory=lambda _: provider, program=original.program)
    assert engine.advance(owner)["status"] == "ACTION_SELECTED"
    assert provider.calls == 2 and len(payloads(store, owner, "LAB_ROLE_REJECTED")) == 1
    assert store.resource_totals(owner)["actual"]["llm_tokens"] == 200
    assert workflow_report(store, owner)["validation"] == "VERIFIED"


def test_crash_after_question_record_does_not_duplicate_question_or_provider(tmp_path, monkeypatch):
    store = Store(tmp_path)
    engine, owner = fixture_workflow(store)
    append = store.append
    broken = False

    def fail_once(target, kind, value):
        nonlocal broken
        if kind == "RESEARCH_ACTION_SELECTED" and not broken:
            broken = True
            raise RuntimeError("injected after question")
        return append(target, kind, value)

    monkeypatch.setattr(store, "append", fail_once)
    with pytest.raises(RuntimeError, match="injected"):
        engine.advance(owner)
    before = len(payloads(store, owner, "ROLE_COMPLETED"))
    assert engine.advance(owner)["status"] == "ACTION_SELECTED"
    assert len(payloads(store, owner, "ROLE_COMPLETED")) == before
    assert len(payloads(store, owner, "RESEARCH_QUESTION_OPENED")) == 1
    workflow_report(store, owner)


def test_unknown_provider_keeps_reservation_and_is_never_retried(tmp_path):
    class Unknown(WorkflowFixtureProvider):
        calls = 0

        def complete(self, *args):
            self.calls += 1
            return {"answer": None, "cost": None, "error": "unknown"}

    store = Store(tmp_path)
    base, owner = fixture_workflow(store)
    provider = Unknown()
    engine = MechanismResearch(store, provider_factory=lambda _: provider, program=base.program)
    with pytest.raises(JarvisError, match="unknown"):
        engine.advance(owner)
    assert engine.advance(owner)["status"] == "NEEDS_RECONCILIATION"
    assert provider.calls == 1 and not payloads(store, owner, "LAB_ROLE_REJECTED")
    assert store.resource_totals(owner)["pending"]["llm_tokens"] == 65536


def test_owner_pause_propagates_to_successor_without_new_roles(tmp_path):
    store = Store(tmp_path)
    engine, owner = fixture_workflow(store)
    store.set_status(owner, "CLOSED")
    child = engine.successor(owner)
    store.control(owner, "PAUSED")
    assert engine.advance(child)["status"] == "PAUSED"
    assert not store.jobs(child)
    with pytest.raises(AdmissionError, match="owner stop"):
        engine.successor(owner)


def test_audit_rejects_future_evidence_even_if_artifacts_and_events_are_valid(tmp_path):
    store = Store(tmp_path)
    engine, owner = fixture_workflow(store)
    context = engine.context(owner)
    future = store.artifacts.put({"kind": "ResearchActionObservation", "not_observed_yet": True})
    store.append(
        owner,
        "LAB_ROLE_REQUESTED",
        {
            "key": "forged-future",
            "role": "workflow_director",
            "context_ref": store.artifacts.put(
                {**context, "visible_evidence_ids": [*context["visible_evidence_ids"], future]}
            ),
            "schema_ref": digest({}),
        },
    )
    with pytest.raises(IntegrityError, match="future evidence"):
        workflow_report(store, owner)


def sandbox_or_skip(tmp_path):
    sandbox = SkillSandbox()
    if not sandbox.identity()["available"]:
        pytest.skip("No supported OS sandbox installed")
    code = tmp_path / "sandbox-probe" / "implementation.py"
    code.parent.mkdir()
    code.write_text("def run(payload):\n    return {'ok':True}\n")
    probe = sandbox.run(code, {}, tmp_path / "probe-run")
    if probe["status"] != "PASS":
        pytest.skip(
            "Installed sandbox cannot execute in this environment: " + probe["stderr"][:200]
        )
    return sandbox


def test_os_sandbox_denies_host_reads_writes_and_network(tmp_path):
    sandbox = sandbox_or_skip(tmp_path)
    result = sandbox.probe(tmp_path / "denial-probe")
    assert result["status"] == "PASS" and result["value"] == {
        "host_read": True,
        "source_write": True,
        "network": True,
    }
    assert (
        tmp_path / "denial-probe" / "host-sentinel"
    ).read_text() == "synthetic isolation sentinel"


def test_full_builder_successor_existing_experiment_and_zero_call_replay(tmp_path, monkeypatch):
    sandbox_or_skip(tmp_path)
    store = Store(tmp_path / "store")
    report = run_mechanism_fixture(store)
    assert report["status"] == "VERIFIED"
    assert report["external_llm_calls"] == report["remote_iris_submissions"] == 0
    initial, child = report["initial"], report["successor"]
    assert initial["agent_version_ref"] != child["agent_version_ref"]
    assert all(v == 0 for v in initial["usage"]["pending"].values())
    kinds = [store.artifacts.get(p["result_ref"])["action"] for p in child["outcomes"]]
    assert kinds == ["DIAGNOSE", "EXPERIMENT", "CONSULT", "STOP"]
    builds = payloads(store, initial["owner"], "SKILL_BUILD_COMPLETED")
    assert builds[0]["status"] == "SKILL_READY_FOR_SUCCESSOR" and builds[0]["git"]["commit"]
    assert child["questions"][0]["assessments"][0]["verdict"] == "refuted"
    monkeypatch.setattr(
        WorkflowFixtureProvider, "complete", lambda *_: pytest.fail("replay called LLM")
    )
    monkeypatch.setattr(SkillRegistry, "dispatch", lambda *_: pytest.fail("replay executed skill"))
    assert replay_workflow(store, child["owner"])["status"] == "COMPLETE"
    assert (
        replay_workflow(store, child["owner"], [digest({"unknown": 1})])["status"]
        == "INSUFFICIENT_COVERAGE"
    )
    program = payloads(store, child["owner"], "WORKFLOW_PROGRAM_CREATED")[0]["program"]
    for p in payloads(store, program, "PROGRAM_ROLE_REQUESTED"):
        assert store.artifacts.get(p["context_ref"])["research_contract"] == store.artifacts.get(
            initial["contract_ref"]
        )


def test_store_free_preflight_and_lineage_rejection(tmp_path, monkeypatch):
    pair = diagnostic_case()
    inputs = []
    for name, value in (("baseline", pair.baseline), ("candidate", pair.candidate)):
        f = tmp_path / (name + ".json")
        f.write_text(value.model_dump_json())
        inputs.append({"path": f.name, "sha256": file_digest(f)})
    contract = ResearchContract(
        task_id="preflight",
        instruction="Study selection and execution",
        objective="Explain PL",
        focus="Mechanisms",
        baseline_refs=(digest({"baseline": "declared"}),),
        evaluator_id=pair.baseline.evaluator_id,
        dataset_id=pair.baseline.dataset_id,
        exposure="synthetic",
        candidate_freedom=("diagnose",),
        acceptance={},
        constraints=("No evaluator changes",),
        origin="declared_fixture",
    )
    declaration = {
        "contract": contract.model_dump(mode="json"),
        "baseline_evidence": [{"baseline": "declared"}],
        "inputs": inputs,
    }
    path = tmp_path / "declaration.yaml"
    path.write_text(yaml.safe_dump(declaration))
    monkeypatch.setattr(Store, "__init__", lambda *_: pytest.fail("preflight created store"))
    assert preflight(path)["execution_started"] is False
    declaration["contract"]["dataset_id"] = "other"
    path.write_text(yaml.safe_dump(declaration))
    with pytest.raises(AdmissionError, match="lineage"):
        preflight(path)


def test_replay_never_claims_selected_but_unexecuted_actions_are_observed(tmp_path):
    store = Store(tmp_path)
    engine, owner = fixture_workflow(store)
    engine.advance(owner)
    assert replay_workflow(store, owner)["status"] == "INSUFFICIENT_COVERAGE"
    engine.advance(owner)
    assert replay_workflow(store, owner)["status"] == "COMPLETE"


def test_registry_historical_prefix_and_revocation(tmp_path):
    store = Store(tmp_path)
    registry = SkillRegistry.create(store)
    ref = registry.define_builtin("inventory", "inventory-v1")
    registry.register(ref, registry.validate(registry.registry, ref))
    prior = registry.snapshot()
    registry.verify_snapshot(prior)
    store.append(registry.registry, "SKILL_DISABLED", {"skill_ref": ref, "reason": "revoked"})
    registry.verify_snapshot(prior)
    assert registry.snapshot()["skills"] == []
    with pytest.raises(AdmissionError, match="disabled"):
        registry.dispatch(registry.registry, ref, diagnostic_case())
    with pytest.raises(IntegrityError, match="historical prefix"):
        registry.verify_snapshot({**prior, "skills": []})


def test_unusable_sandbox_does_not_call_builder_provider(tmp_path, monkeypatch):
    store = Store(tmp_path)
    engine, owner = fixture_workflow(store)
    for _ in range(7):
        engine.advance(owner)
    original = WorkflowFixtureProvider.complete

    def deny_builder(self, prompt, schema, directory):
        if schema["title"] == "BuilderSource":
            pytest.fail("paid builder requested before isolation was available")
        return original(self, prompt, schema, directory)

    monkeypatch.setattr(WorkflowFixtureProvider, "complete", deny_builder)
    monkeypatch.setattr(SkillSandbox, "identity", lambda _: {"available": True})
    monkeypatch.setattr(SkillSandbox, "probe", lambda *_: {"status": "UNAVAILABLE"})
    assert engine.advance(owner)["status"] == "ACTION_COMPLETED"
    observed = payloads(store, owner, "RESEARCH_ACTION_COMPLETED")[-1]
    assert store.artifacts.get(observed["result_ref"])["value"]["status"] == "UNAVAILABLE"
    assert not payloads(store, owner, "SKILL_BUILD_COMPLETED")
    verify_tools(store, owner)


def test_builder_self_test_cannot_replace_independent_fidelity(tmp_path, monkeypatch):
    sandbox_or_skip(tmp_path)
    store = Store(tmp_path / "store")
    engine, owner = fixture_workflow(store, include_experiment=False)
    original = WorkflowFixtureProvider.complete

    def bad_candidate(self, prompt, schema, directory):
        result = original(self, prompt, schema, directory)
        if schema["title"] == "BuilderSource":
            result["answer"]["implementation"] = "def run(payload):\n    return {}\n"
            result["answer"]["self_test"] = "def self_test():\n    assert True\n"
        return result

    monkeypatch.setattr(WorkflowFixtureProvider, "complete", bad_candidate)
    for _ in range(8):
        engine.advance(owner)
    outcome = payloads(store, owner, "SKILL_BUILD_COMPLETED")[0]
    assert outcome["status"] == "BUILD_FAILED" and "independent" in outcome["reason"]
    assert not store.artifacts.get(outcome["validation_ref"])["passed"]
    registry = SkillRegistry(store, store.manifest(owner)["spec"]["registry"])
    assert outcome["skill_ref"] not in registry.snapshot()["skills"]
    assert all(v == 0 for v in store.resource_totals(owner)["pending"].values())
    verify_tools(store, owner)


def test_successor_creation_recovers_interruption_before_link_event(tmp_path, monkeypatch):
    store = Store(tmp_path)
    engine, owner = fixture_workflow(store)
    store.set_status(owner, "CLOSED")
    append = store.append

    def crash(target, kind, value):
        if kind == "RESEARCH_SUCCESSOR_CREATED":
            raise RuntimeError("injected before successor link")
        return append(target, kind, value)

    monkeypatch.setattr(store, "append", crash)
    with pytest.raises(RuntimeError, match="injected"):
        engine.successor(owner)
    before = store.resource_members(owner)
    monkeypatch.setattr(store, "append", append)
    child = engine.successor(owner)
    assert store.resource_members(owner) == before and child in before
    assert engine.successor(owner) == child


@pytest.mark.parametrize("name", ["inventory", "trade_attribution", "score_distribution"])
def test_public_skill_packages_install_with_checksums_and_fidelity(tmp_path, name):
    from pathlib import Path

    store = Store(tmp_path)
    registry = SkillRegistry.create(store)
    directory = Path(__file__).resolve().parents[1] / "skills" / name
    ref = registry.install_package(directory)
    result = registry.dispatch(registry.registry, ref, diagnostic_case())
    assert result["result"]["status"] == "OBSERVED"
    assert registry.manifest(ref).origin == "human_provided"
    verify_tools(store, registry.registry)


def test_preflight_rejects_experiment_contract_before_any_execution(tmp_path):
    from pathlib import Path

    store = Store(tmp_path / "private")
    _, owner = fixture_workflow(store)
    spec = store.manifest(owner)["spec"]
    declaration = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1] / "examples/mechanism-research/declaration.yaml"
        ).read_text()
    )
    declaration["program"] = spec["program"]
    path = tmp_path / "mismatched.yaml"
    path.write_text(yaml.safe_dump(declaration))
    with pytest.raises(AdmissionError, match="evaluator mismatch"):
        preflight(path)


def test_fixture_reports_incomplete_when_builder_isolation_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(SkillSandbox, "identity", lambda _: {"available": True})
    monkeypatch.setattr(SkillSandbox, "probe", lambda *_: {"status": "UNAVAILABLE"})
    report = run_mechanism_fixture(Store(tmp_path))
    assert report["status"] == "INCOMPLETE" and "successor" not in report
    assert report["external_llm_calls"] == report["remote_iris_submissions"] == 0


def test_disabled_skill_name_version_cannot_be_overwritten(tmp_path):
    store = Store(tmp_path)
    registry = SkillRegistry.create(store)
    ref = registry.define_builtin("inventory", "inventory-v1")
    registry.register(ref, registry.validate(registry.registry, ref))
    store.append(registry.registry, "SKILL_DISABLED", {"skill_ref": ref})
    changed = store.artifacts.put(
        {**store.artifacts.get(ref), "applicability": "different meaning"}
    )
    with pytest.raises(IntegrityError, match="immutable"):
        registry.register(changed, registry.validate(registry.registry, changed))


def test_owner_contract_pins_complete_experiment_environment(tmp_path):
    from jarvis.mechanism_research import MechanismResearchSpec, validate_experiment_contract

    store = Store(tmp_path)
    _, owner = fixture_workflow(store)
    spec = MechanismResearchSpec.model_validate(store.manifest(owner)["spec"])
    contract = ResearchContract.model_validate(store.artifacts.get(spec.contract_ref))
    validate_experiment_contract(contract, spec.program)
    with pytest.raises(AdmissionError, match="explicitly pinned"):
        validate_experiment_contract(
            contract.model_copy(update={"experiment_environment_id": None}), spec.program
        )


def test_notebook_includes_actual_role_contexts_answers_and_registry(tmp_path, monkeypatch):
    import json

    from jarvis.mechanism_cli import notebook

    store = Store(tmp_path / "store")
    engine, owner = fixture_workflow(store)
    for _ in range(3):
        engine.advance(owner)
    monkeypatch.setattr(
        WorkflowFixtureProvider, "complete", lambda *_: pytest.fail("export called LLM")
    )
    destination = tmp_path / "notebook"
    assert notebook(store, owner, destination)["external_calls"] == 0
    audit = json.loads((destination / "audit.json").read_text())
    assert len(audit["roles"]) == 2
    assert audit["roles"][0]["model"] == "gpt-6.1-sol"
    assert audit["roles"][0]["context"]["research_contract"]["instruction"]
    assert audit["roles"][0]["answer"] and audit["skill_registry"]["events"]
    assert audit["registry_setup_usage"]["actual"]["tool_calls"] > 0


def test_builder_repairs_known_syntax_error_and_registers_explicit_new_version(
    tmp_path, monkeypatch
):
    sandbox_or_skip(tmp_path)
    store = Store(tmp_path / "store")
    engine, owner = fixture_workflow(store, include_experiment=False)
    original = WorkflowFixtureProvider.complete
    attempts = 0

    def proposed_revision(self, prompt, schema, directory):
        nonlocal attempts
        response = original(self, prompt, schema, directory)
        if (
            schema["title"] == "ResearchDecision"
            and response["answer"]["action"]["kind"] == "BUILD_SKILL"
        ):
            response["answer"]["action"]["version"] = "0.2.0"
        if schema["title"] == "BuilderSource":
            attempts += 1
            if attempts == 1:
                response["answer"]["implementation"] = "def run(:\n"
        return response

    monkeypatch.setattr(WorkflowFixtureProvider, "complete", proposed_revision)
    for _ in range(8):
        engine.advance(owner)
    outcome = payloads(store, owner, "SKILL_BUILD_COMPLETED")[0]
    assert outcome["status"] == "SKILL_READY_FOR_SUCCESSOR"
    assert store.artifacts.get(outcome["skill_ref"])["version"] == "0.2.0"
    assert attempts == 2 and len(payloads(store, owner, "LAB_ROLE_REJECTED")) == 1
    assert store.resource_totals(owner)["actual"]["llm_tokens"] == 700
    assert store.resource_totals(owner)["pending"]["llm_tokens"] == 0
    workflow_report(store, owner)


def test_csv_snapshot_hashes_the_captured_bytes_even_if_source_changes(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path

    from jarvis.identity import raw_digest

    path = tmp_path / "snapshot.csv"
    raw = b"key,pl\na,1.25\n"
    path.write_bytes(raw)
    original_open = Path.open

    @contextmanager
    def replaced_after_read(self, mode="r", *args, **kwargs):
        with original_open(self, mode, *args, **kwargs) as stream:
            yield stream
        if self == path and mode in {"r", "rb"}:
            with original_open(path, "w") as stream:
                stream.write("key,pl\na,999\n")

    metadata = diagnostic_case().baseline.model_dump(
        exclude={"rows", "source_checksum"}, mode="json"
    )
    monkeypatch.setattr(Path, "open", replaced_after_read)
    snapshot = import_snapshot_csv(path, metadata, {"entity_id": "key", "pl": "pl"})
    assert snapshot.source_checksum == raw_digest(raw)
    assert str(snapshot.rows[0].pl) == "1.25"
    assert "999" in path.read_text()


def test_macos_framework_uses_real_interpreter_without_allowing_process_fork(tmp_path, monkeypatch):
    import sys

    prefix = tmp_path / "Python.framework" / "Versions" / "3.12"
    binary = prefix / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    launcher = prefix / "bin" / "python3.12"
    binary.parent.mkdir(parents=True)
    launcher.parent.mkdir()
    binary.write_text("declared test interpreter")
    launcher.write_text("declared test launcher")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(launcher))
    monkeypatch.setattr(sys, "base_prefix", str(prefix))
    monkeypatch.setattr("jarvis.skill_sandbox.shutil.which", lambda _: "/usr/bin/sandbox-exec")
    sandbox = SkillSandbox()
    assert sandbox.interpreter() == binary
    command = sandbox.command(
        tmp_path / "implementation.py", tmp_path / "input.json", tmp_path / "output", "run"
    )
    assert command[3] == str(binary)
    assert "allow process-fork" not in command[2]
    assert "(allow process-exec (literal " in command[2]
