"""Finite unattended recovery, native execution and provider-free delivery."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.autonomous_research import AutonomousResearch
from jarvis.autonomy_contracts import AutonomySessionSpec
from jarvis.autonomy_report import replay_session, verify_session
from jarvis.contracts import Budget, Cost, JarvisError
from jarvis.identity import digest, file_digest
from jarvis.planning_fixture import PlanningFixtureProvider, declared_world
from jarvis.research_roles import RoleCaller
from jarvis.researcher import AgentConfig
from jarvis.runtime_controls import (
    ContextEnvelopeExceeded,
    RuntimeControls,
    bounded_context,
    encoded_size,
)
from jarvis.runtime_delivery import export_exit
from jarvis.storage import Store


def root_owner(store, **budget):
    return store.create(
        {
            "world_id": "synthetic",
            "task": {
                "budget": Budget(
                    **{"max_llm_tokens": 100000, "max_wall_seconds": 1000, **budget}
                ).model_dump()
            },
        }
    )


class CountingProvider:
    def __init__(self, *, error=None, unknown=False, tokens=200):
        self.calls = 0
        self.error, self.unknown, self.tokens = error, unknown, tokens

    def identity(self):
        return {"provider": "scripted-synthetic"}

    def complete(self, prompt, schema, directory):
        self.calls += 1
        return {
            "answer": {"observed": "synthetic"},
            "error": self.error if self.calls == 1 else None,
            "cost": None if self.unknown else Cost(llm_tokens=self.tokens).model_dump(),
        }


def test_known_overrun_is_charged_without_poisoning_all_future_roles(tmp_path):
    s = Store(tmp_path)
    o = root_owner(s, max_llm_tokens=10000, max_wall_seconds=1000)
    p = CountingProvider(tokens=200)
    caller = RoleCaller(s, p, AgentConfig(reserve_tokens=100))
    caller.call(o, "autonomy_consultant", {"turn": 1}, {})
    caller.call(o, "autonomy_director", {"turn": 2}, {})
    assert p.calls == 2
    assert s.resource_totals(o)["actual"]["llm_tokens"] == 400
    assert len([e for e in s.events(o) if e["event_type"] == "ROLE_RESERVATION_OVERRUN"]) == 2


def test_transient_retry_is_finite_durable_and_reuses_the_same_frozen_context(tmp_path):
    s = Store(tmp_path)
    o = root_owner(s, max_llm_tokens=10000, max_wall_seconds=1000)
    p = CountingProvider(error="PROVIDER_TRANSIENT:synthetic")
    cfg = AgentConfig(reserve_tokens=1000, provider_retries=2)
    first = RoleCaller(s, p, cfg).call(o, "autonomy_director", {"turn": 1}, {})
    second = RoleCaller(s, p, cfg).call(o, "autonomy_director", {"turn": 1}, {})
    assert first == second and p.calls == 2
    requests = [s.artifacts.get(j["request_ref"]) for j in s.jobs(o)]
    assert requests[0]["context_id"] == requests[1]["context_id"]
    assert s.resource_totals(o)["actual"]["llm_tokens"] == 400
    assert len([e for e in s.events(o) if e["event_type"] == "ROLE_TRANSPORT_RETRY"]) == 1


def test_unknown_provider_completion_is_not_resubmitted(tmp_path):
    s = Store(tmp_path)
    o = root_owner(s)
    p = CountingProvider(error="PROVIDER_TRANSIENT:synthetic", unknown=True)
    caller = RoleCaller(s, p, AgentConfig(provider_retries=2))
    for _ in range(2):
        with pytest.raises(JarvisError, match="unknown"):
            caller.call(o, "autonomy_director", {"turn": 1}, {})
    assert p.calls == 1 and s.status(o) == "NEEDS_RECONCILIATION"
    assert s.resource_totals(o)["pending"]["llm_tokens"] > 0


def test_context_compaction_keeps_constraints_and_does_not_invent_or_expose_evidence(tmp_path):
    s = Store(tmp_path)
    contract = {
        "fixed_evaluator": "fixed-v1",
        "constraints": ["retain locked split"],
        "objective": 123.25,
    }
    context = {
        "research_contract": contract,
        "research_plan": {"questions": ["exact hypothesis"]},
        "tables": [{"ref": digest(i), "preview": "x" * 4000, "number": i} for i in range(60)],
        "visible_evidence_ids": [digest(i) for i in range(200)],
        "analyzable_evidence_ids": [digest(i) for i in range(200)],
    }
    frozen = bounded_context(s, context, 64000)
    assert encoded_size(context) > 196000 and encoded_size(frozen) <= 64000
    assert (
        frozen["research_contract"] == contract
        and frozen["research_plan"] == context["research_plan"]
    )
    assert s.artifacts.get(frozen["context_compaction"]["full_view_ref"]) == context
    assert set(frozen["visible_evidence_ids"]) < set(context["visible_evidence_ids"])
    assert set(frozen["analyzable_evidence_ids"]) <= set(frozen["visible_evidence_ids"])


def test_irreducible_context_is_rejected_before_any_provider_dispatch(tmp_path):
    s = Store(tmp_path)
    with pytest.raises(ContextEnvelopeExceeded, match="immutable"):
        bounded_context(s, {"research_contract": {"instruction": "x" * 70000}}, 64000)
    o = root_owner(s)
    p = CountingProvider()
    with pytest.raises(ContextEnvelopeExceeded):
        RoleCaller(s, p, AgentConfig(max_context_bytes=8000, max_prompt_bytes=20000)).call(
            o, "autonomy_director", {"instruction": "x" * 9000}, {}
        )
    assert p.calls == 0 and not s.jobs(o)


def test_context_accepts_already_compacted_workspace_history(tmp_path):
    s = Store(tmp_path)
    previous = [{"step": i, "note": "retained older step", "tool": "run_python"} for i in range(12)]
    recent = [
        {
            "step": 12 + i,
            "request": {"tool": "run_python", "note": "recent", "content": "x" * 20000},
            "observation": {"status": "PASS"},
        }
        for i in range(8)
    ]
    context = {
        "task": {"question": "unchanged"},
        "transcript": [*previous, *recent],
        "visible_evidence_ids": [],
    }
    result = bounded_context(s, context, 64000)
    assert encoded_size(result) <= 64000
    assert result["transcript"][:12] == previous
    assert len(result["transcript"]) == 20
    assert result["task"] == context["task"]


def planning_session(tmp_path, *, provider=None, runtime=None, **changes):
    s = Store(tmp_path)
    world = declared_world(s, {})
    e = AutonomousResearch(
        s, world["executors"], provider or (lambda cfg, role: PlanningFixtureProvider(role))
    )
    spec = AutonomySessionSpec(
        contract_ref=world["contract_ref"],
        runtime=runtime or RuntimeControls(provider_backoff_seconds=0),
        max_concurrent_actions=1,
        **changes,
    )
    return s, e, e.create(spec)


@pytest.mark.parametrize("control", ["PAUSED", "CANCELLED"])
def test_owner_stop_always_delivers_without_resuming_or_dispatching(tmp_path, control):
    s, e, o = planning_session(tmp_path)
    s.control(o, control)
    result = e.run(o)
    assert result["status"] == control and not s.jobs(o)
    report = json.loads((Path(result["delivery"]["directory"]) / "deliverable.json").read_text())
    assert report["runtime_exit"]["workflow_complete"] is False
    assert report["runtime_exit"]["control"] == control
    assert s.get_control(o) == control


@pytest.mark.parametrize("case", ["budget", "runner", "exception"])
def test_partial_exit_always_exports_an_auditable_package(tmp_path, case):
    changes = {"budget": Budget(max_llm_tokens=1)} if case == "budget" else {}
    s, e, o = planning_session(tmp_path, **changes)
    if case == "exception":
        e.advance = lambda owner: (_ for _ in ()).throw(OSError("synthetic interruption"))
    result = e.run(o, max_steps=0 if case == "runner" else 5)
    assert (
        result["status"]
        == {
            "budget": "BUDGET_EXHAUSTED",
            "runner": "RUNNER_LIMIT",
            "exception": "INFRASTRUCTURE_INTERRUPTED",
        }[case]
    )
    path = Path(result["delivery"]["directory"])
    report = json.loads((path / "deliverable.json").read_text())
    assert report["runtime_exit"]["workflow_complete"] is False
    assert (path / "deliverable.md").exists()
    manifest = json.loads((path / "manifest.json").read_text())
    assert all(file_digest(path / name) == ref for name, ref in manifest["files"].items())
    assert (
        export_exit(s, o, {k: v for k, v in result.items() if k != "delivery"})
        == result["delivery"]
    )


def test_provider_refusals_have_one_finite_campaign_retry_envelope(tmp_path):
    class Refusing(PlanningFixtureProvider):
        calls = 0

        def complete(self, prompt, schema, directory):
            type(self).calls += 1
            return {
                "answer": None,
                "cost": Cost(llm_tokens=3).model_dump(),
                "error": "PROVIDER_QUOTA_EXHAUSTED:synthetic",
            }

    s, e, o = planning_session(tmp_path, provider=lambda cfg, role: Refusing(role))
    result = e.run(o)
    assert result["status"] == "PROVIDER_UNAVAILABLE" and Refusing.calls == 3
    assert len([x for x in s.events(o) if x["event_type"] == "RUNTIME_PROVIDER_RETRY"]) == 2
    assert "delivery" in result
    # A new process/run cannot reset and multiply the declared retry allowance.
    result = e.run(o)
    assert result["status"] == "PROVIDER_UNAVAILABLE" and Refusing.calls <= 4


def test_legacy_models_do_not_gain_optional_identity_fields():
    cfg = AgentConfig().model_dump()
    assert not {"max_context_bytes", "max_prompt_bytes", "provider_retries"} & set(cfg)
    assert "runtime" not in AutonomySessionSpec(contract_ref=digest("test")).model_dump()


def test_role_allowance_is_not_reported_as_total_budget_exhaustion(tmp_path):
    from jarvis.runtime_controls import RoleCallLimitExceeded

    s = Store(tmp_path)
    o = root_owner(s)
    provider = CountingProvider()
    caller = RoleCaller(s, provider, AgentConfig(max_calls=1, reserve_tokens=1000))
    caller.call(o, "autonomy_director", {"turn": 1}, {})
    with pytest.raises(RoleCallLimitExceeded, match="ROLE_CALL_LIMIT"):
        caller.call(o, "autonomy_director", {"turn": 2}, {})
    assert provider.calls == 1


def test_reused_delivery_detects_changed_files(tmp_path):
    from jarvis.contracts import IntegrityError

    s, e, o = planning_session(tmp_path)
    result = e.run(o, max_steps=0)
    (Path(result["delivery"]["directory"]) / "deliverable.md").write_text("changed")
    with pytest.raises(IntegrityError, match="sealed delivery changed"):
        export_exit(s, o, {"status": "RUNNER_LIMIT"})


def test_missing_evidence_delivers_unverified_partial_package(tmp_path):
    s, e, o = planning_session(tmp_path)
    # An unreadable event payload is an integrity failure, never a fake successful report.
    event = s.events(o)[-1]
    s.artifacts.path(event["payload_ref"]).unlink()
    result = export_exit(
        s, o, {"status": "NEEDS_RECONCILIATION", "reason": "synthetic lost payload"}
    )
    report = json.loads((Path(result["directory"]) / "deliverable.json").read_text())
    assert report["audit"]["validation"] == "FAILED"
    assert report["validation_verdict"] == "UNVERIFIED"
    assert report["runtime_exit"]["workflow_complete"] is False
    assert o in report["runtime_exit"]["journal_read_errors"]


def test_single_cli_run_executes_native_recovers_validates_and_delivers(tmp_path):
    base = [sys.executable, "-m", "jarvis", "--store", str(tmp_path / "store"), "autonomy"]

    def call(*args):
        result = subprocess.run(
            [*base, *args], text=True, capture_output=True, timeout=90, check=True
        )
        return json.loads(result.stdout)

    declaration = Path(__file__).resolve().parents[1] / "examples/autonomy/runtime.declaration.yaml"
    o = call("start", str(declaration))["owner"]
    result = call("run", o, "--wall-seconds", "60")
    assert result["status"] == "CLOSED", result
    s = Store(tmp_path / "store")
    assert verify_session(s, o)["validation"] == "VERIFIED"
    assert replay_session(s, o)["external_calls"] == 0
    directory = Path(result["delivery"]["directory"])
    report = json.loads((directory / "deliverable.json").read_text())
    assert report["runtime_exit"]["workflow_complete"] is True
    assert report["stage_gate"] == "PASS"
    assert report["independent_validation"] == "NOT_RUN"
    assert report["runtime_exit"]["pending_jobs"] == []
    assert any(
        x["event"] == "ROLE_TRANSPORT_RETRY" for x in report["runtime_exit"]["recovery_history"]
    )
    assert any(
        b["closure"]["conclusion"] == "negative" for b in report["research_plan"]["branches"]
    )
    launches = [e["payload"]["child"] for e in s.events(o) if e["event_type"] == "ACTION_LAUNCHED"]
    native = [
        s.artifacts.get(e["payload"]["observation_ref"])
        for child in launches
        for e in s.events(child)
        if e["event_type"] == "WORKER_STEP"
        and s.artifacts.get(e["payload"]["step_ref"])["tool"] == "run_native"
    ]
    assert len(native) == 1 and native[0]["status"] == "PASS"
    assert native[0]["execution"]["stdout"].strip() == "6"
    analysis = next(a for a in report["actions"] if a["kind"] == "ANALYZE")
    assert analysis["status"] == "OBSERVED"
    jobs_before = {m: len(s.jobs(m)) for m in [o, *launches]}
    # Running a closed session again adopts existing evidence; no native rerun/provider call.
    assert call("run", o)["status"] == "CLOSED"
    assert jobs_before == {m: len(s.jobs(m)) for m in [o, *launches]}


def test_large_memory_is_reduced_to_owner_intent_before_refusing(tmp_path):
    """Recorded failure: owner guidance + preference lessons + skills pushed a fixed
    contract and plan over the envelope after one decision."""
    s = Store(tmp_path)
    context = {
        "research_contract": {"instruction": "fixed owner mandate " * 2500},
        "research_plan": {"branches": ["b"] * 1000},
        "research_memory": {
            "owner_guidance": [{"text": f"guidance {i} " + "g" * 4000} for i in range(9)],
            "lessons": [
                {"kind": "owner_preference", "statement": f"pref {i} " + "p" * 3000}
                for i in range(9)
            ],
            "skills": [{"name": f"skill{i}", "description": "d" * 3000} for i in range(6)],
            "open_questions": ["q" * 2000] * 5,
        },
        "visible_evidence_ids": [],
    }
    assert encoded_size(context) > 120000
    result = bounded_context(s, context, 80000)
    assert encoded_size(result) <= 80000
    assert result["research_contract"] == context["research_contract"]
    assert result["research_plan"] == context["research_plan"]
    memory = result["research_memory"]
    assert len(memory["owner_guidance"]) == 8 and memory["owner_guidance"][-1].startswith(
        "guidance 8"
    )
    assert memory["owner_preferences"][-1].startswith("pref 8")
    assert memory["skills"] == [f"skill{i}" for i in range(6)]
    assert s.artifacts.get(memory["view_ref"]) == context


def test_context_cap_leaves_room_for_schema_and_reservation(tmp_path):
    from jarvis.autonomous_research import role_config

    s = Store(tmp_path)
    world = declared_world(s, {})
    small = AutonomySessionSpec(contract_ref=world["contract_ref"], runtime=RuntimeControls())
    assert role_config(small, "director").max_context_bytes == 80000  # 120000 reservation
    large = AutonomySessionSpec(
        contract_ref=world["contract_ref"],
        runtime=RuntimeControls(),
        role_config={"director": {**AgentConfig().model_dump(), "reserve_tokens": 200000}},
    )
    assert role_config(large, "director").max_context_bytes == 120000


def test_cli_resume_continues_a_recoverable_stop_only(tmp_path):
    import contextlib
    import io

    from jarvis.cli import parser, run
    from jarvis.contracts import AdmissionError

    s = Store(tmp_path / "store")
    world = declared_world(s, {})
    e = AutonomousResearch(s, world["executors"], lambda cfg, r: PlanningFixtureProvider(r))
    o = e.create(AutonomySessionSpec(contract_ref=world["contract_ref"]))

    def call(*args):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            run(parser().parse_args(["--store", str(tmp_path / "store"), "autonomy", *args]))
        return json.loads(buffer.getvalue())

    s.append(o, "SESSION_PARTIAL_STOP", {"reason": "CONTEXT_ENVELOPE: x"})
    s.set_status(o, "NEEDS_ATTENTION")
    with pytest.raises(AdmissionError, match="only a recoverable role stop"):
        call("resume", o)
    s.append(
        o,
        "SESSION_PARTIAL_STOP",
        {
            "reason": "director: DECISION_REPAIR_EXHAUSTED",
            "code": "DECISION_REPAIR_EXHAUSTED",
            "role": "director",
            "index": 0,
            "failure_counts": {},
            "recoverable": True,
        },
    )
    resumed = call("resume", o)
    assert resumed["status"] == "RUNNING" and resumed["recovered"] == {
        "role": "director",
        "index": 0,
    }
    recovery = [x["payload"] for x in s.events(o) if x["event_type"] == "ROLE_RECOVERY"][-1]
    assert recovery["mode"] == "manual"


def test_critic_views_compact_earlier_observations_and_memory_lessons(tmp_path):
    store = Store(tmp_path / "store")
    big = "x" * 20000
    context = {
        "task": {"objective": "fixed"},
        "result": {"status": "OBSERVED"},
        "earlier_observations": [
            {
                "index": i,
                "kind": "TRAIN_MODEL",
                "result_ref": f"r{i}",
                "status": "OBSERVED",
                "result": {"validation": big},
            }
            for i in range(6)
        ],
        "research_memory_lessons": [{"kind": "owner_preference", "statement": "PR > 0.001"}]
        + [{"kind": "finding", "statement": big} for _ in range(4)],
    }
    out = bounded_context(store, context, 60000)
    assert encoded_size(out) <= 60000 and out["task"] == context["task"]
    assert out["earlier_observations"][0] == {
        "index": 0,
        "kind": "TRAIN_MODEL",
        "result_ref": "r0",
        "status": "OBSERVED",
        "details_omitted": True,
    }
    assert out["research_memory_lessons"][0]["kind"] == "owner_preference"
