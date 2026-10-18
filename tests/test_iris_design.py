import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.contracts import AdmissionError, IntegrityError
from jarvis.identity import digest
from jarvis.iris import render_candidate, setting_capabilities, split_regions, verify_environment
from jarvis.iris_contracts import IrisAuthorization, IrisExperiment, IrisQueueSpec, IrisSettings
from jarvis.iris_design import (
    IrisDesignProfile,
    IrisPredicate,
    IrisRule,
    compile_rule,
    design_capabilities,
    evaluate_rule,
    read_profile,
    region_bodies,
)
from jarvis.iris_design_fixture import (
    CompiledDesignTransport,
    design_environment,
    run_design_fixture,
)
from jarvis.research_program import program_report
from jarvis.research_queue import IrisResearchQueue, queue_tasks, verify_queue
from jarvis.storage import Store


def rule(signal="spread_bps", op="le", value=3, combine="all"):
    return IrisRule(combine=combine, predicates=(IrisPredicate(signal=signal, op=op, value=value),))


@pytest.fixture
def design_env(tmp_path):
    return design_environment(tmp_path / "inputs")


def test_cpp_driver_symlink_still_links_and_runs_standard_library(tmp_path, monkeypatch):
    compiler = shutil.which("clang++") or shutil.which("g++")
    assert compiler is not None
    alias = tmp_path / ("clang++" if "clang" in compiler else "g++")
    alias.symlink_to(Path(compiler).resolve())
    monkeypatch.setattr("jarvis.iris_design_fixture.shutil.which", lambda _: str(alias))
    env = design_environment(tmp_path / "inputs")
    profile = read_profile(env)
    assert profile is not None
    source, binary = tmp_path / "driver.cpp", tmp_path / "driver"
    source.write_text('#include <iostream>\nint main(){std::cout << "C++ driver retained";}\n')
    subprocess.run(
        [profile.build.compiler, "-std=c++17", str(source), "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    ran = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
    assert ran.stdout == "C++ driver retained"


def test_closed_rules_reject_code_unknown_ops_and_nonfinite_values():
    for payload in [
        dict(signal="x);system(1)", op="gt", value=1),
        dict(signal="score", op="eval", value=1),
        dict(signal="score", op="gt", value=float("nan")),
    ]:
        with pytest.raises(ValidationError):
            IrisPredicate.model_validate(payload)
    with pytest.raises(ValidationError):
        IrisRule(combine="all", predicates=())
    with pytest.raises(ValidationError):
        IrisRule(combine="all", predicates=rule().predicates * 2)
    with pytest.raises(ValidationError):
        IrisSettings(session_end_ms=True)


def test_rule_compiler_canonicalizes_and_checks_availability_units(design_env):
    profile = read_profile(design_env)
    binding = profile.rules[0]
    with pytest.raises(AdmissionError, match="unavailable"):
        compile_rule(rule("future_pl"), binding)
    with pytest.raises(AdmissionError, match="bounds"):
        compile_rule(rule(value=101), binding)
    predicates = (
        IrisPredicate(signal="spread_bps", op="le", value=3),
        IrisPredicate(signal="entry_score", op="gt", value=0.6),
    )
    a = IrisRule(combine="all", predicates=predicates)
    b = IrisRule(combine="all", predicates=predicates[::-1])
    assert compile_rule(a, binding) == compile_rule(b, binding)
    assert evaluate_rule(a, {"spread_bps": 3, "entry_score": 0.7})
    assert not evaluate_rule(a, {"spread_bps": float("nan"), "entry_score": 0.7})
    assert not evaluate_rule(a, {"spread_bps": 3})
    assert not evaluate_rule(a, {"spread_bps": 3, "entry_score": 0.6})


def test_compiled_rules_preserve_base_entry_expiry_and_numeric_boundaries(design_env, tmp_path):
    profile = read_profile(design_env)
    text = render_candidate(
        design_env, IrisSettings(entry_rule=rule(), cancel_rule=rule("model_risk", "gt", 0.85))
    )
    regions = region_bodies(text)
    driver = r"""
    #include <cassert>
    int main(){
      assert(toyEntry(.6,3,33000000)); assert(!toyEntry(.6,3.0001,33000000));
      assert(!toyEntry(.4,1,33000000));
      assert(!toyEntry(.6,std::nan(""),33000000));
      assert(toyCancel(.1,1,33000000,true));
      assert(!toyCancel(.85,1,33000000,false));
      assert(toyCancel(.850001,1,33000000,false));
      assert(!toyCancel(std::nan(""),1,33000000,false));
    }
    """
    cpp, binary = tmp_path / "probe.cpp", tmp_path / "probe"
    cpp.write_text("#include <cmath>\n#include <cstdint>\n" + "\n".join(regions.values()) + driver)
    subprocess.run(
        [profile.build.compiler, "-std=c++17", str(cpp), "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    subprocess.run([str(binary)], check=True)
    original = Path(design_env.template).read_text()
    assert [s for writable, s in split_regions(text) if not writable] == [
        s for writable, s in split_regions(original) if not writable
    ]


def test_schedule_clock_units_hour_boundary_and_ordering(design_env):
    settings = IrisSettings(session_start_ms=36000000, session_end_ms=45000000)
    text = render_candidate(design_env, settings)
    assert "toy_begin = 100000000;" in text
    assert "toy_end = 123000000;" in text
    values = {
        b["setting"]: b["baseline_value"] for b in setting_capabilities(design_env, text=text)
    }
    assert values["session_start_ms"] == 36000000
    assert values["session_end_ms"] == 45000000
    with pytest.raises(AdmissionError, match="session"):
        render_candidate(design_env, IrisSettings(session_start_ms=50000000))
    with pytest.raises(AdmissionError, match="session"):
        render_candidate(design_env, IrisSettings(flat_time_ms=30000000))


def test_profile_pins_private_source_but_prompt_uses_only_capability_descriptions(design_env):
    caps = design_capabilities(design_env)
    assert caps[0]["signals"][0]["availability"] == "entry_decision"
    assert "expression" not in json.dumps(caps)
    profile = Path(design_env.design_profile)
    profile.write_text(profile.read_text() + " ")
    with pytest.raises(IntegrityError):
        verify_environment(design_env)


def test_unbound_rules_and_wrong_stage_are_rejected(design_env):
    plain = design_env.model_copy(update={"design_profile": None})
    with pytest.raises(AdmissionError, match="unbound"):
        render_candidate(plain, IrisSettings(entry_rule=rule()))
    profile = read_profile(design_env).model_dump(mode="json")
    profile["rules"][0]["signals"][0]["availability"] = "cancel_decision"
    with pytest.raises(ValidationError, match="stage"):
        IrisDesignProfile.model_validate(profile)


def test_compile_admission_is_durable_and_offline_audit_never_recompiles(
    design_env, tmp_path, monkeypatch
):
    store = Store(tmp_path / "store")
    backend = CompiledDesignTransport(design_env)
    queue = IrisResearchQueue(store, lambda _: backend)
    qid = queue.create(
        IrisQueueSpec(
            goal="Artificial design admission",
            environment=design_env,
            authorization=IrisAuthorization(
                environment_id=digest(design_env),
                max_submissions=4,
                expires_at=None,
                owner_note="Fixture only",
            ),
        )
    )
    base = IrisExperiment(
        label="baseline",
        family="baseline",
        hypothesis="Reproduce artificial base",
        falsification="Mismatch",
        origin="declared_fixture",
    )
    queue.enqueue(qid, base)
    candidate = IrisExperiment(
        label="entryrule",
        family="entry",
        hypothesis="Remove adverse artificial subset",
        falsification="No objective gain",
        settings=IrisSettings(entry_rule=rule()),
        origin="declared_fixture",
    )
    first = queue.enqueue(qid, candidate)
    jobs = len(store.jobs(qid))
    assert queue.enqueue(qid, candidate) == first and len(store.jobs(qid)) == jobs
    for _ in range(4):
        queue.tick(qid)
    assert len(backend.submissions) == 2
    assert len([e for e in store.events(qid) if e["event_type"] == "IRIS_COMPILE_CHECKED"]) == 2
    monkeypatch.setattr(
        "jarvis.iris_design.subprocess.run", lambda *_a, **_k: pytest.fail("offline compiler call")
    )
    assert verify_queue(store, qid)["external_calls"] == 0
    assert all(t["status"] == "SUCCEEDED" for t in queue_tasks(store, qid))


def test_failed_compile_is_retained_and_never_submitted(design_env, tmp_path, monkeypatch):
    store = Store(tmp_path / "store")
    backend = CompiledDesignTransport(design_env)
    queue = IrisResearchQueue(store, lambda _: backend)
    qid = queue.create(IrisQueueSpec(goal="Artificial negative compile", environment=design_env))
    monkeypatch.setattr(
        "jarvis.iris_preflight.compile_candidate",
        lambda *_: {"status": "FAIL", "diagnostic": "Declared compiler failure"},
    )
    base = IrisExperiment(
        label="baseline",
        family="baseline",
        hypothesis="Toy",
        falsification="Mismatch",
        origin="declared_fixture",
    )
    with pytest.raises(AdmissionError, match="compile"):
        queue.enqueue(qid, base)
    assert len(store.jobs(qid)) == 1 and store.jobs(qid)[0]["finalized"]
    assert not queue_tasks(store, qid) and not backend.submissions


def test_end_to_end_design_research_keeps_negative_results_and_causal_revision(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "store")
    result = run_design_fixture(store)
    assert result["fixture_executions"] == 5 and result["remote_iris_submissions"] == 0
    report = program_report(store, result["program_id"])
    assert report["status"] == "CLOSED" and len(report["direction_batches"]) == 2
    assert len(report["verification"]) == 2
    candidates = {c["experiment"]: c for c in report["baseline_comparisons"]}
    assert candidates["design0"]["delta_from_baseline"]["entry_only_pl"] > 0
    assert candidates["design1"]["delta_from_baseline"]["entry_only_pl"] < 0
    assert report["best_development_candidate"]["result"]["result_origin"] == "SIMULATED_FIXTURE"
    calls = [
        store.artifacts.get(j["request_ref"])["context"] for j in store.jobs(result["program_id"])
    ]
    assert len(calls[1]["observations"]) > len(calls[0]["observations"])
    monkeypatch.setattr(
        "jarvis.iris_design.subprocess.run", lambda *_a, **_k: pytest.fail("audit executed")
    )
    assert (
        program_report(store, result["program_id"])["program_verification"]["external_calls"] == 0
    )


def test_legacy_direction_batch_defaults_keep_original_receipts_verifiable(tmp_path, monkeypatch):
    from jarvis.research_fixture import run_fixture
    from jarvis.research_roles import DirectionBatch

    store = Store(tmp_path / "store")
    original = store.artifacts.put
    old_fields = {
        "schema_version",
        "take_profit_bps",
        "stop_loss_bps",
        "heuristic_cancel_bps",
        "trading_cutoff_delta",
        "cancel_risk_delta",
        "sampling_ms",
        "bypass_heuristic_cancel",
        "bypass_cancel_model",
    }

    def legacy_put(value):
        if isinstance(value, DirectionBatch):
            value = value.model_dump(mode="json")
            for experiment in value["experiments"]:
                experiment["settings"] = {
                    k: v for k, v in experiment["settings"].items() if k in old_fields
                }
        return original(value)

    monkeypatch.setattr(store.artifacts, "put", legacy_put)
    result = run_fixture(store, "research")
    assert result["verification"]["status"] == "VERIFIED"
