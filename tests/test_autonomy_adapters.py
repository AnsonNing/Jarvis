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
    "// @start:config\nconstexpr double toy_heuristic = 0.004;\n// @end:config\n"
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
    model = ModelArtifact(
        model_id="m1",
        files={"cancelmodel.region": store.blobs.put_file(folder / "cancelmodel.region")},
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
    store = Store(tmp_path / "store")
    ref = candidate_with(store, tmp_path, "trained model v2\n", "return order.age > 5;\n")
    resolved = resolve_candidate(store, ref, tmp_path / "resolved")
    FakeTransport.calls = {"submit": 0, "collect": 0}
    tester = IrisRegionBacktester(
        {"dev": environment(tmp_path)},
        setting_defaults={"heuristic_cancel_bps": 40.0},
        transport_factory=FakeTransport,
        poll_seconds=0,
    )
    out = tester.run(resolved, {"heuristic_cancel_bps": 30.0}, "dev", tmp_path / "work")
    assert (
        out["status"] == "SUCCEEDED"
        and out["metrics"]["pl"] == 2.5
        and out["tables"][0]["name"] == "trades"
    )
    sent = FakeTransport.submitted
    assert "trained model v2" in sent and "return order.age > 5;" in sent and "0.003" in sent
    assert "old model" not in sent and sent.startswith("// protected header")
    # A crashed process resumes by collecting the recorded request only.
    FakeTransport.calls["collect"] = 1
    again = tester.run(resolved, {"heuristic_cancel_bps": 30.0}, "dev", tmp_path / "work")
    assert again["status"] == "SUCCEEDED" and FakeTransport.calls["submit"] == 1
    with pytest.raises(AdmissionError, match="REQUIRES_CAPABILITY"):
        tester.render(resolved, {"unbound_setting": 1}, tmp_path / "work2", "dev")


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
