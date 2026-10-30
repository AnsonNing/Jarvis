import json
from pathlib import Path

import pytest

from jarvis.autonomy_adapters import (
    CommandPatchValidator,
    CommandSpec,
    CommandTrainer,
    IrisRegionBacktester,
    RegionPatchValidator,
    apply_regions,
    declared_domain,
)
from jarvis.autonomy_contracts import Candidate, CodePatch, ImplementTarget, ModelArtifact
from jarvis.contracts import AdmissionError, IntegrityError
from jarvis.identity import file_digest
from jarvis.iris_contracts import IrisEnvironment
from jarvis.quant_world import analysis_environment
from jarvis.research_executors import resolve_candidate
from jarvis.storage import Store
from jarvis.workbench import Workbench

TEMPLATE = (
    "// protected header\n"
    "// @start:config\nconstexpr double toy_heuristic = 40 * 0.0001;\n"
    "constexpr double THRESHOLD_HANG = 0.0005;\n#define BYPASS_MODEL (false)\n"
    "constexpr int SAMPLING_MS = 100;\n// @end:config\n"
    "// @start:cancelmodel\nold model\n// @end:cancelmodel\n"
    "// @start:cancelconditions\nreturn false;\n// @end:cancelconditions\n"
    "// protected footer\n"
)


def environment(tmp_path: Path) -> IrisEnvironment:
    root = tmp_path / "upstream"
    root.mkdir(exist_ok=True)
    template = root / "toy.txt"
    template.write_text(TEMPLATE)
    for name in ("iris_agent.py", "evaluator.py"):
        (root / name).write_text("# inert fixture\n")
    return IrisEnvironment(
        root=str(root),
        python="unused",
        branch="toy",
        template=str(template),
        template_sha256=file_digest(template),
        evaluator=str(root / "evaluator.py"),
        evaluator_sha256=file_digest(root / "evaluator.py"),
        agent_script_sha256=file_digest(root / "iris_agent.py"),
        accepted_inputs={str(template): file_digest(template)},
        side="long",
        model_lineage="toy",
        result_origin="SIMULATED_FIXTURE",
        bindings=(
            {
                "setting": "heuristic_cancel_bps",
                "symbol": "toy_heuristic",
                "region": "config",
                "syntax": "double",
                "scale": 0.0001,
            },
        ),
        period={
            "start": "2026-01-05",
            "end": "2026-01-06",
            "trading_dates": ["2026-01-05", "2026-01-06"],
            "group_id": "toy-dev",
        },
    )


def test_region_replacement_keeps_protected_text():
    out = apply_regions(TEMPLATE, {"cancelmodel": "new model\nline 2"})
    assert "new model\nline 2\n" in out and "old model" not in out
    assert out.startswith("// protected header") and out.endswith("// protected footer\n")
    with pytest.raises(AdmissionError, match="unknown region"):
        apply_regions(TEMPLATE, {"nothere": "x"})


def candidate_with(store: Store, tmp_path: Path, model_region: str, rule_region: str | None) -> str:
    folder = tmp_path / "files"
    folder.mkdir(exist_ok=True)
    (folder / "cancelmodel.region").write_text(model_region)
    (folder / "constants.json").write_text('{"THRESHOLD_HANG": 0.00071}')
    model = ModelArtifact(
        model_id="m1",
        files={
            "cancelmodel.region": store.blobs.put_file(folder / "cancelmodel.region"),
            "constants.json": store.blobs.put_file(folder / "constants.json"),
        },
        training_config={},
        training_data_ref=None,
        data_policy="d",
        validation={},
        origin="agent_trained",
        trainer={},
    )
    patches = ()
    if rule_region is not None:
        patch = CodePatch(
            target="cancelconditions",
            target_kind="strategy_patch",
            files={"cancelconditions.region": store.blobs.put(rule_region.encode())},
            diff_ref=store.blobs.put(b"diff"),
            validation={},
        )
        patches = (store.artifacts.put(patch),)
    return store.artifacts.put(
        Candidate(
            label="c",
            parent_ref=None,
            model_ref=store.artifacts.put(model),
            patch_refs=patches,
            settings={},
            origin="agent_trained",
            data_policy="d",
        )
    )


class FakeTransport:
    calls: dict[str, int] = {"submit": 0, "collect": 0}

    def __init__(self, environment):
        self.environment = environment

    def identity(self):
        return {"kind": "fake-iris"}

    def submit(self, request, directory):
        FakeTransport.calls["submit"] += 1
        FakeTransport.submitted = Path(request["candidate_path"]).read_text()
        return {"status": "SUBMITTED", "request_id": request["request_id"]}

    def collect(self, request, directory):
        FakeTransport.calls["collect"] += 1
        if FakeTransport.calls["collect"] == 1:
            return {"status": "PENDING"}
        raw = directory / "results" / "trades.csv"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text("order_id,pl\n1,2.5\n")
        return {
            "status": "SUCCEEDED",
            "result_origin": "SIMULATED_FIXTURE",
            "entry_only_pl": 2.5,
            "count": 1,
            "mdd": 0.0,
            "worst_day": 2.5,
            "daily_pl": {"2026-01-05": 2.5},
            "risk": {"mean_pr": 0.001, "entry_win_rate": 1.0},
            "provenance": {"raw_sha256": file_digest(raw)},
            "commit": "abc",
        }


def test_iris_backtester_renders_candidate_and_never_resubmits(tmp_path):
    from jarvis.autonomy_adapters import RegionSetting, SubmissionLedger

    store = Store(tmp_path / "store")
    ref = candidate_with(store, tmp_path, "trained model v2\n", "return order.age > 5;\n")
    resolved = resolve_candidate(store, ref, tmp_path / "resolved")
    FakeTransport.calls = {"submit": 0, "collect": 0}
    bindings = (
        RegionSetting(
            name="heuristic_cancel_bps",
            region="config",
            symbol="toy_heuristic",
            syntax="double",
            scale=0.0001,
        ),
        RegionSetting(
            name="bypass_model", region="config", symbol="BYPASS_MODEL", syntax="macro_bool"
        ),
        RegionSetting(name="sampling_ms", region="config", symbol="SAMPLING_MS", syntax="int"),
    )
    tester = IrisRegionBacktester(
        {"dev": environment(tmp_path)},
        settings=bindings,
        setting_defaults={"heuristic_cancel_bps": 40.0, "bypass_model": False, "sampling_ms": 100},
        ledger=SubmissionLedger(tmp_path / "ledger.jsonl", daily_limit=2),
        transport_factory=FakeTransport,
        poll_seconds=0,
    )
    settings = {"heuristic_cancel_bps": 30.0, "bypass_model": True, "sampling_ms": 200}
    out = tester.run(resolved, settings, "dev", tmp_path / "work")
    assert (
        out["status"] == "SUCCEEDED"
        and out["metrics"]["pl"] == 2.5
        and out["metrics"]["mdd"] == 0.0
    )
    assert [t["name"] for t in out["tables"]] == ["raw_source", "trades"]
    source = json.loads(Path(out["tables"][0]["path"]).read_text())
    assert source["settings"] == settings and source["raw_csv_sha256"] == file_digest(
        Path(out["tables"][1]["path"])
    )
    sent = FakeTransport.submitted
    assert "trained model v2" in sent and "return order.age > 5;" in sent
    assert "toy_heuristic = 0.003;" in sent
    assert "THRESHOLD_HANG = 0.00071;" in sent and "#define BYPASS_MODEL (true)" in sent
    assert "SAMPLING_MS = 200;" in sent
    assert "old model" not in sent and sent.startswith("// protected header")
    # A crashed process resumes by collecting the recorded request only.
    FakeTransport.calls["collect"] = 1
    again = tester.run(resolved, settings, "dev", tmp_path / "work")
    assert again["status"] == "SUCCEEDED" and FakeTransport.calls["submit"] == 1
    with pytest.raises(AdmissionError, match="REQUIRES_CAPABILITY"):
        tester.render(resolved, {"unbound_setting": 1}, tmp_path / "work2", "dev")
    FakeTransport.calls["collect"] = 0
    tester.run(resolved, {**settings, "sampling_ms": 300}, "dev", tmp_path / "work3")
    with pytest.raises(AdmissionError, match="QUOTA_GUARD"):
        tester.run(resolved, {**settings, "sampling_ms": 400}, "dev", tmp_path / "work4")
    assert FakeTransport.calls["submit"] == 2


def test_captured_source_evidence_matches_upload_and_retains_exact_diff(tmp_path):
    from jarvis.autonomous_research import table_record
    from jarvis.autonomy_adapters import RegionSetting, SubmissionLedger
    from jarvis.autonomy_evidence import table_evidence
    from jarvis.autonomy_source import source_evidence
    from jarvis.identity import raw_digest

    store = Store(tmp_path / "store")
    ref = candidate_with(store, tmp_path, "trained model v2\n", "return order.age > 5;\n")
    resolved = resolve_candidate(store, ref, tmp_path / "resolved")
    FakeTransport.calls = {"submit": 0, "collect": 0}
    tester = IrisRegionBacktester(
        {"dev": environment(tmp_path)},
        settings=(
            RegionSetting(
                name="heuristic_cancel_bps",
                region="config",
                symbol="toy_heuristic",
                syntax="double",
                scale=0.0001,
            ),
        ),
        setting_defaults={"heuristic_cancel_bps": 40},
        ledger=SubmissionLedger(tmp_path / "ledger.jsonl", daily_limit=1),
        transport_factory=FakeTransport,
        poll_seconds=0,
    )
    settings = {"heuristic_cancel_bps": 30}
    work = tmp_path / "work"
    result = tester.run(resolved, settings, "dev", work)
    table = source_evidence(tester, resolved, settings, "dev", work, result["provenance"])
    proof = json.loads(Path(table["path"]).read_text())
    assert proof["status"] == "VERIFIED" and proof["protected_text_unchanged"] is True
    assert proof["candidate_source_sha256"] == raw_digest(FakeTransport.submitted.encode())
    assert "toy_heuristic = 0.003;" in proof["source_diff"]
    assert "config" in proof["changed_regions"]
    artifact = table_record(
        store,
        Path(table["path"]),
        name="execution_source",
        description="fixture source proof",
        lineage={"experiment": "fixture"},
        exposure="synthetic",
    )
    view = table_evidence(store, artifact)
    assert view["source_evidence"]["submitted_hash_matches"] is True
    assert "candidate_source" not in view["source_evidence"]
    # Cached results must bind the reconstruction to an explicit original checksum.
    cached = tmp_path / "cached"
    cached.mkdir()
    reused = source_evidence(
        tester,
        resolved,
        settings,
        "dev",
        cached,
        {"candidate_sha256": proof["candidate_source_sha256"], "request_id": "original"},
    )
    assert (
        json.loads(Path(reused["path"]).read_text())["capture_origin"]
        == "cached_request_reconstruction"
    )
    with pytest.raises(IntegrityError, match="recorded request"):
        source_evidence(
            tester, resolved, settings, "dev", cached, {"candidate_sha256": "sha256:" + "0" * 64}
        )
    submission = json.loads((work / "submission.json").read_text())
    Path(submission["candidate_path"]).write_text("tampered")
    with pytest.raises(IntegrityError, match="recorded request"):
        source_evidence(tester, resolved, settings, "dev", work, result["provenance"])
    assert FakeTransport.calls["submit"] == 1


@pytest.mark.parametrize("capture", [False, True])
def test_source_capture_is_opt_in_and_registered_as_observed_evidence(tmp_path, capture):
    from jarvis.autonomous_research import AutonomousResearch
    from jarvis.autonomy_adapters import RegionSetting, SubmissionLedger
    from jarvis.autonomy_contracts import (
        AnalysisDelivery,
        AutonomyContract,
        AutonomySessionSpec,
        BacktestAction,
    )
    from jarvis.quant_fixture import QuantFixtureProvider
    from jarvis.research_executors import Executors

    store = Store(tmp_path / "store")
    ref = candidate_with(store, tmp_path, "trained model v2\n", None)
    base = Candidate.model_validate(store.artifacts.get(ref)).model_copy(
        update={"origin": "owner_baseline"}
    )
    ref = store.artifacts.put(base)
    tester = IrisRegionBacktester(
        {"dev": environment(tmp_path)},
        settings=(
            RegionSetting(
                name="heuristic_cancel_bps",
                region="config",
                symbol="toy_heuristic",
                syntax="double",
                scale=0.0001,
            ),
        ),
        setting_defaults={"heuristic_cancel_bps": 40},
        ledger=SubmissionLedger(tmp_path / "ledger.jsonl", 1),
        transport_factory=FakeTransport,
        poll_seconds=0,
    )
    c = AutonomyContract(
        task_id="source_fixture",
        instruction="SIMULATED_FIXTURE source evidence acceptance",
        objective="fixture",
        focus="source",
        domain="synthetic",
        evaluator_id="fixed",
        dataset_id="fixture",
        periods=("dev",),
        exposure="synthetic",
        baseline_candidate_ref=ref,
        settings=(
            {
                "name": "heuristic_cancel_bps",
                "kind": "float",
                "minimum": 1,
                "maximum": 100,
                "baseline": 40,
                "description": "fixture",
            },
        ),
        metrics=({"name": "pl", "direction": "maximize", "description": "fixture units"},),
        acceptance_profiles=(
            {"name": "gain", "objectives": [{"metric": "pl"}], "description": "fixture"},
        ),
        constraints=("synthetic only",),
        origin="declared_fixture",
        analysis_delivery=AnalysisDelivery(capture_submitted_source=capture),
    )
    engine = AutonomousResearch(
        store,
        Executors(Workbench(), backtester=tester),
        lambda cfg, role: QuantFixtureProvider(role),
    )
    o = engine.create(AutonomySessionSpec(contract_ref=store.artifacts.put(c)))
    child = store.create(
        {
            "kind": "AutonomousAction",
            "world_id": "fixture",
            "task": {"budget": engine.spec(o).budget.model_dump()},
            "budget_owner": o,
        }
    )
    FakeTransport.calls = {"submit": 0, "collect": 0}
    action = BacktestAction(
        branch_id="b",
        question_id="q",
        hypothesis_ids=("h",),
        rationale="fixture",
        expected_observation="source proof",
        evidence_refs=(),
        design="fixture",
        experiments=(
            {
                "label": "source",
                "candidate_ref": ref,
                "settings": [{"name": "heuristic_cancel_bps", "value": 30}],
                "period": "dev",
                "hypothesis": "fixture",
                "falsification": "fixture",
            },
        ),
    )
    result, registered = engine._backtest(o, child, 0, action)
    obs = store.artifacts.get(registered["observations"][0])
    assert ("execution_source" in obs["tables"]) is capture
    assert result["experiments"][0]["status"] == "SUCCEEDED"
    if capture:
        proof = store.artifacts.get(obs["tables"]["execution_source"])
        assert proof["blob_ref"] and proof["lineage"]["candidate_ref"] == ref
    else:
        assert "capture_submitted_source" not in c.analysis_delivery.model_dump()


def test_region_patch_validator_checks_markers_and_scope(tmp_path):
    env = environment(tmp_path)
    store = Store(tmp_path / "store")
    ref = candidate_with(store, tmp_path, "model\n", None)
    base = resolve_candidate(store, ref, tmp_path / "resolved")
    validator = RegionPatchValidator(Path(env.template), env.template_sha256)
    target = ImplementTarget(
        name="cancelconditions",
        kind="strategy_patch",
        files=("cancelconditions.region",),
        description="d",
        interface="i",
    )
    files = validator.base_files(target, base)
    assert files == {"cancelconditions.region": b"return false;\n"}
    work = tmp_path / "work"
    work.mkdir()
    (work / "cancelconditions.region").write_text("return true;\n")
    assert validator.validate(target, base, work, Workbench())["status"] == "PASS"
    (work / "cancelconditions.region").write_text("x\n// @end:cancelconditions\n// @start:config\n")
    assert validator.validate(target, base, work, Workbench())["status"] == "FAIL"
    Path(env.template).write_text(TEMPLATE + "drift\n")
    with pytest.raises(IntegrityError):
        validator.base_files(target, base)


@pytest.fixture
def isolated(tmp_path):
    if Workbench(analysis_environment()).probe(tmp_path / "probe")["status"] != "PASS":
        pytest.skip("OS workbench isolation unavailable")


def test_command_trainer_runs_owner_entry_point_in_workbench(tmp_path, isolated):
    owner_code = tmp_path / "owner"
    owner_code.mkdir()
    entry = owner_code / "train_entry.py"
    entry.write_text(
        "import json, pathlib\n"
        "req = json.loads(pathlib.Path('request.json').read_text())\n"
        "pathlib.Path('outputs/cancelmodel.region').write_text('model trained with l2=%s\\n' % req['config']['l2'])\n"
        "pathlib.Path('result.json').write_text(json.dumps({'status': 'SUCCEEDED', 'model_id': 'm-new',"
        " 'files': ['cancelmodel.region'], 'validation': {'oof_auc': 0.7}, 'data_policy': 'owner pipeline'}))\n"
    )
    spec = CommandSpec(
        argv=(str(entry),),
        readonly=(str(owner_code),),
        source_hashes={str(entry): file_digest(entry)},
        space={"l2": {"kind": "float", "minimum": 0, "maximum": 1}},
        description="toy trainer",
    )
    trainer = CommandTrainer(spec, Workbench(analysis_environment()))
    trainer.validate_config(None, {"l2": 0.5})
    with pytest.raises(ValueError):
        trainer.validate_config(None, {"l2": 3})
    store = Store(tmp_path / "store")
    base = resolve_candidate(
        store, candidate_with(store, tmp_path, "model\n", None), tmp_path / "resolved"
    )
    out = trainer.run(base, {"l2": 0.5}, None, None, tmp_path / "train")
    assert out["status"] == "SUCCEEDED" and out["model_id"] == "m-new"
    assert Path(out["files"][0]["path"]).read_text() == "model trained with l2=0.5\n"
    entry.write_text("# changed\n")
    with pytest.raises(IntegrityError, match="executor source changed"):
        trainer.run(base, {"l2": 0.5}, None, None, tmp_path / "train2")


def test_command_patch_validator_and_declared_domain(tmp_path, isolated):
    pipeline = tmp_path / "pipeline"
    pipeline.mkdir()
    (pipeline / "features.py").write_text("def features(o):\n    return [1.0]\n")
    check = pipeline / "check.py"
    check.write_text("import features\nassert features.features({}) == [1.0]\nprint('ok')\n")
    spec = CommandSpec(
        argv=(str(check),),
        readonly=(str(pipeline),),
        source_hashes={str(check): file_digest(check)},
        description="unit tests",
    )
    validator = CommandPatchValidator({"features.py": str(pipeline / "features.py")}, spec)
    target = ImplementTarget(
        name="features",
        kind="pipeline_patch",
        files=("features.py",),
        description="d",
        interface="i",
    )
    store = Store(tmp_path / "store")
    base = resolve_candidate(
        store, candidate_with(store, tmp_path, "model\n", None), tmp_path / "resolved"
    )
    work = tmp_path / "work"
    work.mkdir()
    (work / "features.py").write_bytes(validator.base_files(target, base)["features.py"])
    assert (
        validator.validate(target, base, work, Workbench(analysis_environment()))["status"]
        == "PASS"
    )
    (work / "features.py").write_text("def features(o):\n    return [2.0]\n")
    assert (
        validator.validate(target, base, work, Workbench(analysis_environment()))["status"]
        == "FAIL"
    )
    # Declared domain: contract + owner baseline + validators, no Iris yet.
    (tmp_path / "region.txt").write_text("// @start:cancelmodel\nm\n// @end:cancelmodel\n")
    (tmp_path / "trades.csv").write_text("order_id,pl\n1,1.0\n")
    contract = {
        "task_id": "hermes-cancel",
        "instruction": "i",
        "objective": "o",
        "focus": "f",
        "domain": "hermes",
        "evaluator_id": "e",
        "dataset_id": "d",
        "periods": ["dev"],
        "exposure": "development_exposed",
        "metrics": [{"name": "pl", "direction": "maximize", "description": "d"}],
        "acceptance_profiles": [
            {"name": "p", "objectives": [{"metric": "pl"}], "description": "d"}
        ],
        "implement_targets": [target.model_dump(mode="json")],
        "source_scopes": [
            {
                "name": "features",
                "path": str(pipeline / "features.py"),
                "kind": "pipeline",
                "description": "d",
            }
        ],
        "constraints": ["c"],
        "origin": "human_provided",
    }
    (tmp_path / "contract.json").write_text(json.dumps(contract))
    built = declared_domain(
        store,
        {
            "contract": str(tmp_path / "contract.json"),
            "baseline": {
                "model_id": "M0",
                "model_files": {"cancelmodel.region": str(tmp_path / "region.txt")},
                "data_policy": "owner",
            },
            "command_validators": {
                "features": {
                    "base_paths": {"features.py": str(pipeline / "features.py")},
                    "check": spec.model_dump(mode="json"),
                }
            },
            "inputs": [
                {
                    "name": "trades",
                    "path": str(tmp_path / "trades.csv"),
                    "description": "baseline trades",
                }
            ],
        },
    )
    assert built["executors"].backtester is None and "features" in built["executors"].validators
    assert store.artifacts.get(built["input_refs"][0])["rows"] == 1
    saved = store.artifacts.get(built["contract_ref"])
    assert saved["source_scopes"][0]["sha256"] == file_digest(pipeline / "features.py")


def test_relay_provider_round_trip(tmp_path):
    import threading

    from jarvis.providers import RelayProvider
    from jarvis.researcher import AgentConfig

    relay = tmp_path / "relay"
    provider = RelayProvider(AgentConfig(model="assistant"), relay, timeout_seconds=30)

    def answer():
        import time

        for _ in range(300):
            tickets = (
                list((relay / "pending").glob("*.json")) if (relay / "pending").exists() else []
            )
            if tickets:
                request = json.loads(tickets[0].read_text())
                assert request["schema_title"] == "Answer"
                Path(request["directory"], "answer.json").write_text('{"ok": true}')
                return
            time.sleep(0.05)

    thread = threading.Thread(target=answer)
    thread.start()
    out = provider.complete("question", {"title": "Answer", "type": "object"}, tmp_path / "req-1")
    thread.join()
    assert out["answer"] == {"ok": True} and out["error"] is None and out["cost"]["llm_tokens"] > 0
    assert not list((relay / "pending").glob("*.json"))


def test_claude_code_provider_parses_structured_output_and_quota(tmp_path, monkeypatch):
    from jarvis.contracts import JarvisError
    from jarvis.providers import ClaudeCodeProvider
    from jarvis.researcher import AgentConfig

    monkeypatch.delenv("JARVIS_CLAUDE_EXECUTABLE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(JarvisError, match="unavailable"):
        ClaudeCodeProvider(AgentConfig(model="m"))
    fake = tmp_path / "claude"
    payload = {
        "structured_output": {"ok": True},
        "result": "",
        "is_error": False,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3},
    }
    fake.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo 9.9; exit 0; fi\ncat >/dev/null\n'
        f"echo '{json.dumps(payload)}'\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("JARVIS_CLAUDE_EXECUTABLE", str(fake))
    provider = ClaudeCodeProvider(AgentConfig(model="m", reasoning_effort="medium"))
    assert provider.identity()["tools"] == "disabled"
    out = provider.complete("q", {"type": "object"}, tmp_path / "a")
    assert (
        out["answer"] == {"ok": True}
        and out["usage"]["input_tokens"] == 13
        and out["error"] is None
    )
    limited = {"result": "Usage limit reached for this account", "is_error": True, "usage": {}}
    fake.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo 9.9; exit 0; fi\ncat >/dev/null\n'
        f"echo '{json.dumps(limited)}'\n"
    )
    out = ClaudeCodeProvider(AgentConfig(model="m")).complete(
        "q", {"type": "object"}, tmp_path / "b"
    )
    assert out["error"].startswith("PROVIDER_QUOTA_EXHAUSTED") and out["cost"]["llm_tokens"] == 0


def test_command_trainer_interpreter_mode(tmp_path, isolated):
    entry = tmp_path / "owner" / "entry.py"
    entry.parent.mkdir()
    entry.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path('outputs/m.region').write_text(' '.join(sys.argv[1:]))\n"
        "pathlib.Path('result.json').write_text(json.dumps({'status': 'SUCCEEDED', 'model_id': 'x', 'files': ['m.region'], 'validation': {}}))\n"
    )
    spec = CommandSpec(
        argv=(str(entry), "--side", "LONG"),
        readonly=(str(entry.parent),),
        description="d",
        mode="interpreter",
    )
    store = Store(tmp_path / "store")
    base = resolve_candidate(
        store, candidate_with(store, tmp_path, "model\n", None), tmp_path / "resolved"
    )
    out = CommandTrainer(spec, Workbench(analysis_environment())).run(
        base, {}, None, None, tmp_path / "t"
    )
    assert (
        out["status"] == "SUCCEEDED" and Path(out["files"][0]["path"]).read_text() == "--side LONG"
    )
