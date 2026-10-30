import json
import shutil
from pathlib import Path

import pytest

from jarvis.autonomous_research import AutonomousResearch
from jarvis.autonomy_contracts import (
    AcceptanceProfile,
    AutonomyContract,
    AutonomyDecision,
    AutonomySessionSpec,
    Grants,
    MetricSpec,
    Objective,
    SettingSpec,
    SettingValue,
    TrainModelAction,
)
from jarvis.autonomy_jobs import ActionRunner, local_job
from jarvis.autonomy_policy import ResearchPolicy, enforce
from jarvis.autonomy_report import deliverable, replay_session, session_outcome, verify_session
from jarvis.candidates import assess_profile, compare_results, pareto_front, select_finalist
from jarvis.contracts import AdmissionError, Budget, Cost, IntegrityError
from jarvis.quant_fixture import QuantFixtureProvider
from jarvis.quant_world import analysis_environment, build_world, world_executors
from jarvis.research_roles import strict_schema
from jarvis.storage import Store
from jarvis.workbench import Workbench

EXPLAIN = ResearchPolicy(
    name="explain",
    explain_gains_before_more_backtests=True,
    require_model_branch_before_stop=True,
    min_analyses_before_stop=1,
    max_values_per_setting=4,
    rationale="test",
    origin="declared_fixture",
)


def workbench_or_skip(tmp_path: Path) -> None:
    if Workbench(analysis_environment()).probe(tmp_path / "probe")["status"] != "PASS":
        pytest.skip("OS workbench isolation unavailable")


def session(tmp_path, *, policy=None, provider=None, per_day=40, **spec):
    store = Store(tmp_path / "store")
    world = build_world(store, tmp_path / "world", per_day=per_day)
    engine = AutonomousResearch(
        store, world_executors(world), provider or (lambda cfg, role: QuantFixtureProvider(role))
    )
    owner = engine.create(
        AutonomySessionSpec(
            contract_ref=world["contract_ref"],
            policy_ref=store.artifacts.put(policy) if policy else None,
            **{"max_decisions": 30, "max_concurrent_actions": 1, **spec},
        )
    )
    return store, engine, owner, world


def kinds(store, owner):
    return [
        store.artifacts.get(e["payload"]["decision_ref"])["action"]["kind"]
        for e in store.events(owner)
        if e["event_type"] == "AUTONOMY_DECISION"
    ]


def rejections(store, owner):
    return [
        e["payload"]["reason"]
        for e in store.events(owner)
        if e["event_type"] == "LAB_ROLE_REJECTED"
    ]


# ---------------------------------------------------------------- contracts and comparison


def test_llm_facing_schemas_have_no_free_form_objects():
    from jarvis.autonomy_contracts import AutonomyReview
    from jarvis.autonomy_dream import PolicyProposalBatch
    from jarvis.workspace_agent import WorkerStep

    def free_form(node, path):
        if isinstance(node, dict):
            if node.get("type") == "object" and not node.get("properties"):
                yield path
            for k, v in node.items():
                yield from free_form(v, path + "/" + k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from free_form(v, f"{path}[{i}]")

    for model in (AutonomyDecision, AutonomyReview, WorkerStep, PolicyProposalBatch):
        assert not list(free_form(strict_schema(model), "")), model.__name__


def test_policy_rules_are_enforced_on_decisions():
    def decision(kind, **action):
        return AutonomyDecision.model_validate(
            {
                "context_id": "sha256:" + "1" * 64,
                "situation": "s",
                "branch": None,
                "question": None,
                "guidance_responses": [],
                "action": {
                    "branch_id": "b",
                    "question_id": "q",
                    "hypothesis_ids": ["h"],
                    "rationale": "r",
                    "expected_observation": "e",
                    "evidence_refs": [],
                    "kind": kind,
                    **action,
                },
            }
        )

    backtest = decision(
        "BACKTEST",
        design="d",
        experiments=[
            {
                "label": "x",
                "candidate_ref": "sha256:" + "2" * 64,
                "settings": [{"name": "bp", "value": 9.0}],
                "period": "dev",
                "hypothesis": "h",
                "falsification": "f",
            }
        ],
    )
    stop = decision(
        "STOP",
        scope="session",
        conclusion="inconclusive",
        closures=[
            {
                "branch_id": "b",
                "conclusion": "negative",
                "reason": "r",
                "evidence_refs": ["sha256:" + "3" * 64],
            }
        ],
    )
    state = {
        "history": [{"kind": "BACKTEST", "branch_kind": "parameter"}] * 2,
        "branch_kinds": {"b": "parameter"},
        "unexplained_gains": ["bp30(trades=sha256:x)"],
        "input_shifting_gain": True,
        "trainer_available": True,
        "setting_values": {"bp": ["1.0", "2.0"]},
        "candidate_settings": {},
    }
    policy = ResearchPolicy(
        name="p",
        max_consecutive_backtests=2,
        explain_gains_before_more_backtests=True,
        require_model_branch_before_stop=True,
        min_analyses_before_stop=1,
        max_values_per_setting=2,
        rationale="r",
        origin="declared_fixture",
    )
    with pytest.raises(ValueError, match="consecutive backtests"):
        enforce(policy, state, backtest)
    relaxed = policy.model_copy(update={"max_consecutive_backtests": None})
    with pytest.raises(ValueError, match="explain passing results"):
        enforce(relaxed, state, backtest)
    with pytest.raises(ValueError, match="enumeration limit"):
        enforce(relaxed, {**state, "unexplained_gains": []}, backtest)
    unchanged = {
        **state,
        "unexplained_gains": [],
        "candidate_settings": {"sha256:" + "2" * 64: {"bp": 9.0}},
    }
    enforce(relaxed, unchanged, backtest)  # a no-op setting is not a new enumerated value
    with pytest.raises(ValueError, match="more mechanism analysis"):
        enforce(policy, state, stop)
    analysed = {**state, "history": [{"kind": "ANALYZE", "branch_kind": "diagnosis"}]}
    with pytest.raises(ValueError, match="open a model branch"):
        enforce(policy, analysed, stop)
    enforce(policy, {**analysed, "branch_kinds": {"b": "parameter", "m": "model"}}, stop)
    # Model research recorded in earlier sessions' memory also satisfies the rule.
    enforce(policy, {**analysed, "model_research_in_memory": True}, stop)


def test_contract_rejects_undeclared_profile_metrics_and_grants_keep_stop():
    with pytest.raises(ValueError, match="undeclared metric"):
        AutonomyContract.model_validate(
            {
                **_minimal_contract(),
                "acceptance_profiles": [
                    {"name": "p", "objectives": [{"metric": "sharpe"}], "description": "d"}
                ],
            }
        )
    with pytest.raises(ValueError, match="always available"):
        Grants(actions=("ANALYZE", "STOP"))
    spec = SettingSpec(
        name="bp", kind="float", minimum=5, maximum=10, baseline=6.0, description="d"
    )
    spec.check(7.5)
    for bad in (11.0, True, "x"):
        with pytest.raises(ValueError):
            spec.check(bad)


def _minimal_contract():
    return {
        "task_id": "t",
        "instruction": "i",
        "objective": "o",
        "focus": "f",
        "domain": "d",
        "evaluator_id": "e",
        "dataset_id": "ds",
        "periods": ["dev"],
        "exposure": "synthetic",
        "baseline_candidate_ref": "sha256:" + "0" * 64,
        "metrics": [MetricSpec(name="pl", direction="maximize", description="d").model_dump()],
        "acceptance_profiles": [
            AcceptanceProfile(
                name="p", objectives=(Objective(metric="pl"),), description="d"
            ).model_dump()
        ],
        "constraints": ["c"],
        "origin": "declared_fixture",
    }


def test_multi_objective_profiles_pareto_and_declared_finalist():
    contract = AutonomyContract.model_validate(
        {
            **_minimal_contract(),
            "metrics": [
                {"name": "pl", "direction": "maximize", "description": "d"},
                {"name": "mean_pr", "direction": "maximize", "description": "d"},
                {"name": "mdd", "direction": "minimize", "description": "d"},
            ],
            "acceptance_profiles": [
                {
                    "name": "pl_first",
                    "objectives": [{"metric": "pl", "min_gain": 0.02, "relative": True}],
                    "risk_limits": [{"metric": "mdd", "max_worsening": 0.1}],
                    "description": "d",
                },
                {
                    "name": "pr_first",
                    "objectives": [{"metric": "mean_pr", "min_gain": 0.05, "relative": True}],
                    "risk_limits": [{"metric": "pl", "max_worsening": 0.05}],
                    "description": "d",
                },
            ],
        }
    )
    base = {"pl": 100.0, "mean_pr": 1.0, "mdd": 10.0}
    pr_only = {"pl": 99.0, "mean_pr": 1.2, "mdd": 10.0}
    assert (
        assess_profile(contract, contract.acceptance_profiles[0], pr_only, base)["verdict"]
        == "FAIL"
    )
    assert (
        assess_profile(contract, contract.acceptance_profiles[1], pr_only, base)["verdict"]
        == "PASS"
    )
    risky = {"pl": 120.0, "mean_pr": 1.0, "mdd": 20.0}
    assert (
        assess_profile(contract, contract.acceptance_profiles[0], risky, base)["risk_limits"][0][
            "status"
        ]
        == "BREACHED"
    )
    common = {"status": "SUCCEEDED", "evaluator_id": "e", "dataset_id": "ds", "period": "dev"}
    rows = [
        {**common, "result_ref": "b", "candidate_ref": "cb", "metrics": base},
        {**common, "result_ref": "r1", "candidate_ref": "c1", "metrics": pr_only, "label": "pr"},
        {**common, "result_ref": "r2", "candidate_ref": "c2", "metrics": risky, "label": "risky"},
    ]
    comparison = compare_results(contract, rows[0], rows[1:])
    assert set(pareto_front(contract, rows)) == {"r1", "r2"}
    finalist = select_finalist(contract, comparison)
    assert finalist["profile"] == "pr_first" and finalist["result_ref"] == "r1"
    with pytest.raises(AdmissionError, match="same evaluator"):
        compare_results(contract, rows[0], [{**rows[1], "period": "other"}])


def test_risk_limit_floor_is_absolute():
    contract = AutonomyContract.model_validate(
        {
            **_minimal_contract(),
            "metrics": [
                {"name": "pl", "direction": "maximize", "description": "d"},
                {"name": "mean_pr", "direction": "maximize", "description": "d"},
                {"name": "mdd", "direction": "minimize", "description": "d"},
            ],
            "acceptance_profiles": [
                {
                    "name": "pl_first",
                    "objectives": [{"metric": "pl"}],
                    "risk_limits": [
                        {"metric": "mean_pr", "floor": 0.001},
                        {"metric": "mdd", "floor": 30.0},
                    ],
                    "description": "d",
                }
            ],
        }
    )
    profile = contract.acceptance_profiles[0]
    base = {"pl": 100.0, "mean_pr": 0.0025, "mdd": 10.0}
    # PR far below baseline but above the floor, and a doubled MDD under its cap, still pass.
    assert (
        assess_profile(contract, profile, {"pl": 150.0, "mean_pr": 0.0011, "mdd": 20.0}, base)[
            "verdict"
        ]
        == "PASS"
    )
    low = assess_profile(contract, profile, {"pl": 150.0, "mean_pr": 0.0009, "mdd": 20.0}, base)
    assert low["verdict"] == "FAIL" and low["risk_limits"][0]["status"] == "BREACHED"
    deep = assess_profile(contract, profile, {"pl": 150.0, "mean_pr": 0.002, "mdd": 31.0}, base)
    assert deep["risk_limits"][1]["status"] == "BREACHED"
    with pytest.raises(ValueError, match="max_worsening or floor"):
        AutonomyContract.model_validate(
            {
                **_minimal_contract(),
                "acceptance_profiles": [
                    {
                        "name": "p",
                        "objectives": [{"metric": "pl"}],
                        "risk_limits": [{"metric": "pl"}],
                        "description": "d",
                    }
                ],
            }
        )


# ---------------------------------------------------------------- jobs and workbench


def test_workbench_isolation_and_declared_packages(tmp_path):
    workbench_or_skip(tmp_path)
    bench = Workbench(analysis_environment())
    work = tmp_path / "w"
    work.mkdir()
    (work / "a.py").write_text("import pandas as pd\nprint(int(pd.Series([1,2,3]).sum()))\n")
    assert bench.execute("python", ["a.py"], work)["stdout"].strip() == "6"
    secret = tmp_path / "secret.txt"
    secret.write_text("token")
    (work / "b.py").write_text(f"print(open({str(secret)!r}).read())\n")
    assert bench.execute("python", ["b.py"], work)["status"] == "FAIL"


def test_interrupted_local_job_is_settled_and_rerun(tmp_path):
    store = Store(tmp_path)
    owner = store.create({"kind": "t", "world_id": "w", "task": {"budget": Budget().model_dump()}})
    envelope = {"kind": "LocalJob", "key": "k", "attempt": 0, "request": {"x": 1}, "world_id": "w"}
    job = store.reserve(
        owner, envelope, Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=5)
    )
    store.start_job(job)  # the process "dies" here: RUNNING without a receipt
    calls = []
    out = local_job(store, owner, "k", {"x": 1}, lambda: calls.append(1) or {"ok": True})
    assert out["value"] == {"ok": True} and calls == [1]
    events = [e["event_type"] for e in store.events(owner)]
    assert "LOCAL_JOB_INTERRUPTED" in events and events.count("LOCAL_JOB_COMPLETED") == 1
    assert local_job(store, owner, "k", {"x": 1}, lambda: calls.append(2))["value"] == {"ok": True}
    assert calls == [1]
    assert not any(store.resource_totals(owner)["pending"].values())


# ---------------------------------------------------------------- sessions


def test_naive_session_tunes_closes_audits_and_replays(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, _ = session(tmp_path)
    assert engine.run(owner, wall_seconds=600)["status"] == "CLOSED"
    sequence = kinds(store, owner)
    assert sequence[0] == "READ_SOURCE" and sequence[-2:] == ["COMPARE", "STOP"]
    assert "ANALYZE" not in sequence and "TRAIN_MODEL" not in sequence
    audit = verify_session(store, owner)
    assert audit["validation"] == "VERIFIED" and not any(audit["usage"]["pending"].values())
    replay = replay_session(store, owner)
    assert replay["status"] == "COMPLETE" and replay["external_calls"] == 0
    other = replay["decisions"][:-1] + ["sha256:" + "f" * 64]
    assert replay_session(store, owner, other)["status"] == "INSUFFICIENT_COVERAGE"
    outcome = session_outcome(store, owner)
    assert outcome["finalist"]["status"] == "SELECTED"


def test_policy_redirects_to_diagnosis_rules_and_retraining(tmp_path):
    workbench_or_skip(tmp_path)
    naive_store, naive, naive_owner, _ = session(tmp_path / "q0")
    naive.run(naive_owner, wall_seconds=600)
    store, engine, owner, _ = session(tmp_path / "q1", policy=EXPLAIN)
    assert engine.run(owner, wall_seconds=600)["status"] == "CLOSED"
    sequence = kinds(store, owner)
    assert {"ANALYZE", "IMPLEMENT", "TRAIN_MODEL"} <= set(sequence)
    assert any("explain passing results" in r for r in rejections(store, owner))
    best = session_outcome(store, owner)
    naive_best = session_outcome(naive_store, naive_owner)

    def gain(outcome):
        row = next(
            r
            for r in outcome["comparison"]["rows"]
            if r["result_ref"] == outcome["finalist"]["result_ref"]
        )
        return row["metrics"]["pl"]

    assert gain(best) > gain(naive_best)
    report = deliverable(store, owner)
    origins = {c["origin"] for c in report["candidates"]}
    assert {"owner_baseline", "agent_trained", "agent_implemented"} <= origins
    analysis = [
        store.artifacts.get(e["payload"]["result_ref"])
        for e in store.events(owner)
        if e["event_type"] == "ACTION_COMPLETED" and e["payload"]["kind"] == "ANALYZE"
    ][0]
    assert analysis["reproducible"]["all_identical"] is True
    assert analysis["output_tables"] and analysis["evidence_level"] == "descriptive"
    assert (
        "## 比較" in report["markdown"]
        and report["validation_verdict"] == "DEVELOPMENT_IMPROVEMENT"
    )


def test_owner_guidance_must_be_answered_and_shapes_research(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, _ = session(tmp_path)
    gid = engine.advise(owner, "Please check whether the cancel model needs retraining")
    engine.run(owner, wall_seconds=600)
    first = next(
        store.artifacts.get(e["payload"]["decision_ref"])
        for e in store.events(owner)
        if e["event_type"] == "AUTONOMY_DECISION"
    )
    assert (
        first["guidance_responses"][0]["guidance_id"] == gid
        and first["guidance_responses"][0]["adopted"]
    )
    assert "TRAIN_MODEL" in kinds(store, owner)
    report = deliverable(store, owner)
    assert report["owner_guidance"][0]["response"]["adopted"] is True
    # A decision that ignores pending guidance is invalid.
    later = engine.advise(owner, "another idea")
    state = engine.state(owner)
    context = engine.director_context(owner, state)
    decision = AutonomyDecision.model_validate(
        {**first, "context_id": "sha256:" + "1" * 64, "guidance_responses": []}
    )
    with pytest.raises(ValueError, match="pending owner guidance"):
        engine.validate_decision(owner, decision, context)
    assert later in context["pending_guidance_ids"]


def test_stop_guard_rejects_premature_stop_and_research_continues(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, _ = session(
        tmp_path, provider=lambda cfg, role: QuantFixtureProvider(role, eager_stop=True)
    )
    assert engine.run(owner, wall_seconds=600)["status"] == "CLOSED"
    assert any("stop guard" in r for r in rejections(store, owner))
    assert kinds(store, owner)[-2:] == ["COMPARE", "STOP"]
    stop = [e["payload"] for e in store.events(owner) if e["event_type"] == "SESSION_STOPPED"][0]
    assert stop["conclusion"] == "research_complete"


def test_decision_validation_rejects_repeats_and_out_of_contract_actions(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, world = session(tmp_path)
    engine.run(owner, wall_seconds=600)
    state = engine.state(owner)
    context = engine.director_context(owner, state)
    baseline = (
        world["contract_ref"]
        and store.artifacts.get(world["contract_ref"])["baseline_candidate_ref"]
    )
    q = state["questions"]["q-param"]

    def decision(action):
        return AutonomyDecision.model_validate(
            {
                "context_id": "sha256:" + "1" * 64,
                "situation": "s",
                "branch": {"branch_id": "b-new", "kind": "parameter", "goal": "g"},
                "question": None,
                "guidance_responses": [],
                "action": {
                    "branch_id": "b-new",
                    "question_id": "q-param",
                    "hypothesis_ids": [q["hypotheses"][0]["hypothesis_id"]],
                    "rationale": "r",
                    "expected_observation": "e",
                    "evidence_refs": [],
                    **action,
                },
            }
        )

    def experiment(label, settings):
        return {
            "label": label,
            "candidate_ref": baseline,
            "settings": settings,
            "period": "dev",
            "hypothesis": "h",
            "falsification": "f",
        }

    for action, message in [
        (
            {
                "kind": "BACKTEST",
                "design": "d",
                "experiments": [experiment("x", [{"name": "heuristic_cancel_bps", "value": 30.0}])],
            },
            "repeats an observed configuration",
        ),
        (
            {
                "kind": "BACKTEST",
                "design": "d",
                "experiments": [experiment("x", [{"name": "unknown", "value": 1.0}])],
            },
            "undeclared setting",
        ),
        (
            {
                "kind": "BACKTEST",
                "design": "d",
                "experiments": [
                    experiment("x", [{"name": "heuristic_cancel_bps", "value": 999.0}])
                ],
            },
            "outside declared space",
        ),
        (
            {
                "kind": "BACKTEST",
                "design": "d",
                "experiments": [{**experiment("x", []), "period": "holdout"}],
            },
            "undeclared period",
        ),
        (
            {"kind": "ANALYZE", "question": "q", "input_refs": ["sha256:" + "2" * 64]},
            "ANALYZE inputs must be registered",
        ),
        ({"kind": "WAIT"}, "requires a running action"),
        (
            {
                "kind": "IMPLEMENT",
                "label": "x",
                "target": "nope",
                "base_candidate_ref": baseline,
                "requirement": "r",
            },
            "not declared",
        ),
        ({"kind": "READ_SOURCE", "scopes": ["secret"]}, "unknown source scope"),
        (
            {
                "kind": "TRAIN_MODEL",
                "label": "x",
                "base_candidate_ref": baseline,
                "settings": [{"name": "unknown", "value": 1.0}],
            },
            "undeclared setting",
        ),
        (
            {
                "kind": "TRAIN_MODEL",
                "label": "x",
                "base_candidate_ref": baseline,
                "settings": [{"name": "heuristic_cancel_bps", "value": 999.0}],
            },
            "outside declared space",
        ),
    ]:
        with pytest.raises(ValueError, match=message):
            engine.validate_decision(owner, decision(action), context)
    # Backtest observations (not only tables) are analyzable evidence, materialized as JSON.
    observation = state["observations"][0]
    engine.validate_decision(
        owner,
        decision({"kind": "ANALYZE", "question": "a new question", "input_refs": [observation]}),
        context,
    )


def test_pause_budget_and_runtime_identity_boundaries(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, world = session(tmp_path)
    store.control(owner, "PAUSED")
    assert engine.advance(owner)["status"] == "PAUSED"
    store.control(owner, "RUNNING")
    assert engine.advance(owner)["status"] == "ACTION_COMPLETED"
    tight = session(
        tmp_path / "tight",
        budget=Budget(
            max_actions=3,
            max_tool_calls=3,
            max_llm_tokens=4000,
            max_wall_seconds=600,
            max_compute_units=10,
            max_parallel_jobs=4,
        ),
    )
    result = tight[1].run(tight[2], wall_seconds=600)
    assert result["status"] == "BUDGET_EXHAUSTED"
    assert any(e["event_type"] == "SESSION_PARTIAL_STOP" for e in tight[0].events(tight[2]))
    changed = world_executors(world)
    changed.validators.pop("features")
    other = AutonomousResearch(store, changed, lambda cfg, role: QuantFixtureProvider(role))
    with pytest.raises(IntegrityError, match="executors changed"):
        other.advance(owner)


def test_changed_declared_source_refuses_new_session(tmp_path):
    store = Store(tmp_path / "store")
    world = build_world(store, tmp_path / "world", per_day=20)
    (tmp_path / "world" / "strategy" / "README.md").write_text("edited")
    engine = AutonomousResearch(
        store, world_executors(world), lambda cfg, role: QuantFixtureProvider(role)
    )
    with pytest.raises(AdmissionError, match="declared source changed"):
        engine.create(AutonomySessionSpec(contract_ref=world["contract_ref"]))


class _NoRun(ActionRunner):
    def submit(self, child, function):  # simulate a process that dies before running the action
        return None


def test_launched_action_resumes_in_a_new_process(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, world = session(tmp_path)
    dead = AutonomousResearch(
        store, world_executors(world), lambda cfg, role: QuantFixtureProvider(role), runner=_NoRun()
    )
    assert dead.advance(owner)["action"] == "READ_SOURCE"
    launched = dead.advance(owner)
    assert launched["status"] == "ACTION_LAUNCHED"
    assert dead.advance(owner)["status"] == "WAITING"
    fresh = AutonomousResearch(
        store, world_executors(world), lambda cfg, role: QuantFixtureProvider(role)
    )
    assert fresh.run(owner, wall_seconds=600)["status"] == "CLOSED"
    assert verify_session(store, owner)["validation"] == "VERIFIED"


class _BadRule(QuantFixtureProvider):
    def worker(self, c):
        if c["task"]["kind"] == "implementation":
            base = {
                "context_id": c["context_id"],
                "path": None,
                "content": None,
                "arguments": [],
                "summary": None,
                "findings": [],
                "output_files": [],
                "final_script": None,
            }
            step = len(c["transcript"])
            if step == 0:
                return {
                    **base,
                    "note": "n",
                    "tool": "write_file",
                    "path": "work/cancel_rule.py",
                    "content": "def should_cancel(order, settings):\n    return 1\n",
                }
            if step == 1:
                return {**base, "note": "n", "tool": "validate"}
            return {
                **base,
                "note": "n",
                "tool": "finish",
                "summary": "done",
                "output_files": ["cancel_rule.py"],
            }
        return super().worker(c)


def test_invalid_implementation_is_rejected_without_candidate(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, _ = session(
        tmp_path, policy=EXPLAIN, provider=lambda cfg, role: _BadRule(role)
    )
    engine.run(owner, wall_seconds=600)
    results = [
        store.artifacts.get(e["payload"]["result_ref"])
        for e in store.events(owner)
        if e["event_type"] == "ACTION_COMPLETED" and e["payload"]["kind"] == "IMPLEMENT"
    ]
    assert results and results[0]["status"] == "REJECTED"
    assert results[0]["validation"]["status"] == "FAIL"
    assert all(c["origin"] != "agent_implemented" for c in deliverable(store, owner)["candidates"])
    assert verify_session(store, owner)["validation"] == "VERIFIED"


def test_concurrent_actions_run_in_background(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, _ = session(tmp_path, max_concurrent_actions=2)
    assert engine.run(owner, wall_seconds=600)["status"] == "CLOSED"
    assert verify_session(store, owner)["validation"] == "VERIFIED"


def test_deliverable_files_from_cli(tmp_path):
    workbench_or_skip(tmp_path)
    from jarvis.cli import parser, run

    declaration = tmp_path / "decl.yaml"
    declaration.write_text(
        "adapter: jarvis.quant_world:declared_world\n"
        f"adapter_args: {{root: {json.dumps(str(tmp_path / 'world'))}, seed: 5, family: calm}}\n"
        "agent: fixture\nmax_decisions: 25\nmax_concurrent_actions: 1\n"
    )
    store = tmp_path / "store"
    outputs = []

    def call(*args):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            run(parser().parse_args(["--store", str(store), "autonomy", *args]))
        outputs.append(json.loads(buffer.getvalue()))
        return outputs[-1]

    owner = call("start", str(declaration))["owner"]
    assert call("status", owner)["recent_actions"] == []
    assert call("advise", owner, "consider retraining the model")["guidance_id"] == "g1"
    assert call("run", owner, "--wall-seconds", "600")["status"] == "CLOSED"
    assert call("report", owner)["validation"] == "VERIFIED"
    assert call("replay", owner)["status"] == "COMPLETE"
    written = call("deliverable", owner, str(tmp_path / "out"))["written"]
    assert all(Path(p).is_file() for p in written)
    shutil.rmtree(tmp_path / "out")


def test_worker_run_python_accepts_inline_code_from_workspace_root(tmp_path):
    """Regression from the real-LLM smoke run: inline code and root-relative paths."""
    workbench_or_skip(tmp_path)
    from jarvis.workspace_agent import WorkerStep, execute_step, prepare_workspace

    store = Store(tmp_path / "store")
    owner = store.create({"kind": "t", "world_id": "w", "task": {"budget": Budget().model_dump()}})
    workspace = prepare_workspace(
        tmp_path / "ws", inputs={"t.csv": b"a\n1\n2\n"}, sources={"s.py": b"x = 1\n"}, base={}
    )
    bench = Workbench(analysis_environment())
    blank = {
        "context_id": "sha256:" + "0" * 64,
        "note": "n",
        "path": None,
        "content": None,
        "arguments": [],
        "summary": None,
        "findings": [],
        "output_files": [],
        "final_script": None,
    }
    inline = WorkerStep.model_validate(
        {
            **blank,
            "tool": "run_python",
            "content": "import pandas as pd\nd = pd.read_csv('inputs/t.csv')\n"
            "d.to_csv('work/out.csv', index=False)\nprint(int(d.a.sum()), open('sources/s.py').read().strip())\n",
        }
    )
    out = execute_step(store, owner, 0, inline, workspace, bench, None)
    assert (
        out["status"] == "PASS"
        and out["stdout"].strip() == "3 x = 1"
        and out["script"] == "work/step_0.py"
    )
    assert (workspace / "work" / "out.csv").is_file()
    named = WorkerStep.model_validate(
        {
            **blank,
            "tool": "run_python",
            "path": "work/again.py",
            "content": "open('inputs/t.csv', 'a')\n",
        }
    )
    assert (
        execute_step(store, owner, 1, named, workspace, bench, None)["status"] == "FAIL"
    )  # inputs are read-only
    rerun = WorkerStep.model_validate({**blank, "tool": "run_python", "arguments": ["step_0.py"]})
    assert execute_step(store, owner, 2, rerun, workspace, bench, None)["status"] == "PASS"
    missing = WorkerStep.model_validate({**blank, "tool": "run_python", "path": "work/none.py"})
    assert execute_step(store, owner, 3, missing, workspace, bench, None)["status"] == "ERROR"


class _FlakyProvider(QuantFixtureProvider):
    """Times out once for the Director and once for the worker (process ended, no usage)."""

    failed: set[str] = set()
    failure_error: str = "PROVIDER_TIMEOUT"

    def complete(self, prompt, schema, directory):
        title = schema.get("title")
        if title in {"AutonomyDecision", "WorkerStep"} and title not in _FlakyProvider.failed:
            _FlakyProvider.failed.add(title)
            directory.mkdir(parents=True, exist_ok=True)
            return {
                "answer": None,
                "usage": None,
                "cost": None,
                "error": _FlakyProvider.failure_error,
                "events": [],
                "stderr": "",
            }
        return super().complete(prompt, schema, directory)


@pytest.mark.parametrize("error", ["PROVIDER_TIMEOUT", "PROVIDER_INCOMPLETE:OUTPUT_LIMIT"])
def test_ended_provider_calls_are_charged_and_retried(tmp_path, error):
    workbench_or_skip(tmp_path)
    _FlakyProvider.failed = set()
    _FlakyProvider.failure_error = error
    store, engine, owner, _ = session(
        tmp_path, policy=EXPLAIN, provider=lambda cfg, role: _FlakyProvider(role)
    )
    assert engine.run(owner, wall_seconds=600)["status"] == "CLOSED"
    assert _FlakyProvider.failed == {"AutonomyDecision", "WorkerStep"}
    settled = [
        e
        for c in [owner, *engine.state(owner)["launched"].values()]
        for e in store.events(c)
        if e["event_type"] == "JOB_SETTLED_AT_RESERVATION"
    ]
    assert len(settled) == 2 and all(e["payload"]["reason"] == error for e in settled)
    assert any(e["event_type"] == "DIRECTOR_FAILURE" for e in store.events(owner))
    audit = verify_session(store, owner)
    assert audit["validation"] == "VERIFIED" and not any(audit["usage"]["pending"].values())
    assert audit["usage"]["actual"]["llm_tokens"] >= 2 * 120000  # both reservations were charged
    worker_contexts = [
        store.artifacts.get(e["payload"]["context_ref"])
        for c in engine.state(owner)["launched"].values()
        for e in store.events(c)
        if e["event_type"] == "LAB_ROLE_REQUESTED" and e["payload"]["role"] == "autonomy_worker"
    ]
    retries = [c for c in worker_contexts if c.get("retry_attempt")]
    assert retries
    if error.endswith("OUTPUT_LIMIT"):
        assert "one small step" in retries[0]["output_limit_recovery"]["instruction"]
        assert "full reservation" in retries[0]["output_limit_recovery"]["reason"]
    else:
        assert "output_limit_recovery" not in retries[0]


def test_codex_usage_limit_events_are_classified_as_quota_refusals():
    from jarvis.providers import quota_refusal

    events = [
        {"type": "thread.started"},
        {"type": "turn.started"},
        {"type": "error", "message": "You've hit your usage limit. Try again at 3:00 AM."},
        {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}},
    ]
    assert "usage limit" in quota_refusal(events)
    assert (
        quota_refusal([{"type": "turn.failed", "error": {"message": "stream disconnected"}}])
        is None
    )


class _QuotaProvider(QuantFixtureProvider):
    """Refuses the first Director and first worker call with a usage-limit error."""

    refused: set[str] = set()

    def complete(self, prompt, schema, directory):
        title = schema.get("title")
        if title in {"AutonomyDecision", "WorkerStep"} and title not in _QuotaProvider.refused:
            _QuotaProvider.refused.add(title)
            directory.mkdir(parents=True, exist_ok=True)
            return {
                "answer": None,
                "usage": {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0},
                "cost": Cost(llm_tokens=0, wall_seconds=0.01).model_dump(mode="json"),
                "error": "PROVIDER_QUOTA_EXHAUSTED: You've hit your usage limit.",
                "events": [],
                "stderr": "",
            }
        return super().complete(prompt, schema, directory)


def test_provider_quota_pauses_the_session_and_resumes_without_failures(tmp_path):
    workbench_or_skip(tmp_path)
    _QuotaProvider.refused = set()
    store, engine, owner, _ = session(
        tmp_path, policy=EXPLAIN, provider=lambda cfg, role: _QuotaProvider(role)
    )
    first = engine.run(owner, wall_seconds=600)
    assert (
        first["status"] == "PROVIDER_UNAVAILABLE" and store.status(owner) == "PROVIDER_UNAVAILABLE"
    )
    statuses = [first["status"]]
    for _ in range(4):  # each new run after quota returns resumes where it stopped
        result = engine.run(owner, wall_seconds=600)
        statuses.append(result["status"])
        if result["status"] == "CLOSED":
            break
    assert statuses[-1] == "CLOSED" and statuses.count("PROVIDER_UNAVAILABLE") == 2
    assert not any(e["event_type"] == "DIRECTOR_FAILURE" for e in store.events(owner))
    children = engine.state(owner)["launched"].values()
    assert any(
        e["event_type"] == "CHILD_PROVIDER_PAUSED" for c in children for e in store.events(c)
    )
    audit = verify_session(store, owner)
    assert audit["validation"] == "VERIFIED" and not any(audit["usage"]["pending"].values())
    assert "ANALYZE" in kinds(store, owner)


def test_director_context_is_bounded_and_keeps_every_experiment(tmp_path):
    workbench_or_skip(tmp_path)
    from jarvis.autonomous_research import CONTEXT_BUDGET, clip

    assert clip("x" * 1000, 10).startswith("x" * 10) and "990 chars" in clip("x" * 1000, 10)
    assert len(clip(list(range(100)), items=5)) == 6
    store, engine, owner, _ = session(tmp_path, policy=EXPLAIN)
    engine.run(owner, wall_seconds=600)
    state = engine.state(owner)
    context = engine.director_context(owner, state)
    assert len(json.dumps(context, ensure_ascii=False)) <= CONTEXT_BUDGET
    assert [r["result_ref"] for r in context["experiment_ledger"]] == state["observations"]
    tight = engine._director_context(owner, state, 1, 0)
    assert len(json.dumps(tight)) < len(json.dumps(context))
    assert all("result" not in o for o in tight["observations"][:-1])


class _SloppyFinish(QuantFixtureProvider):
    """Real-run regression: the first finish names a non-existent output file."""

    def worker(self, c):
        answer = super().worker(c)
        finishes = [t for t in c["transcript"] if t["request"]["tool"] == "finish"]
        if answer["tool"] == "finish" and c["task"]["kind"] == "analysis" and not finishes:
            return {**answer, "output_files": [*answer["output_files"], "work/extra.csv peaceful?"]}
        return answer


def test_invalid_finish_goes_back_to_the_worker(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, _ = session(
        tmp_path, policy=EXPLAIN, provider=lambda cfg, role: _SloppyFinish(role)
    )
    assert engine.run(owner, wall_seconds=600)["status"] == "CLOSED"
    analyses = [
        store.artifacts.get(e["payload"]["result_ref"])
        for e in store.events(owner)
        if e["event_type"] == "ACTION_COMPLETED" and e["payload"]["kind"] == "ANALYZE"
    ]
    assert analyses and all(a["status"] == "OBSERVED" for a in analyses)
    ledger = engine.director_context(owner, engine.state(owner))["experiment_ledger"]
    assert ledger and all("result_origin" in row["provenance"] for row in ledger)


def test_training_settings_reach_the_trainer_and_the_trained_candidate(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, world = session(tmp_path)
    engine.run(owner, wall_seconds=600)
    baseline = store.artifacts.get(world["contract_ref"])["baseline_candidate_ref"]
    seen = []
    trainer = engine.executors.trainer
    run = trainer.run

    def spy(base, *args):
        seen.append(dict(base.settings))
        return run(base, *args)

    trainer.run = spy
    action = TrainModelAction(
        label="bound",
        base_candidate_ref=baseline,
        settings=(SettingValue(name="heuristic_cancel_bps", value=55.0),),
        rationale="r",
        expected_observation="e",
        branch_id="b",
        question_id="q",
        hypothesis_ids=("h",),
        evidence_refs=(),
    )
    child = store.create(
        {
            "kind": "AutonomousAction",
            "parent": owner,
            "index": 99,
            "decision_ref": baseline,
            "world_id": "sha256:" + "3" * 64,
            "task": {"budget": engine.spec(owner).budget.model_dump()},
            "budget_owner": owner,
        }
    )
    value, register = engine._train(owner, child, 99, action)
    assert value["status"] == "SUCCEEDED"
    assert seen[0]["heuristic_cancel_bps"] == 55.0
    candidate = store.artifacts.get(register["candidates"][0])
    assert candidate["settings"]["heuristic_cancel_bps"] == 55.0


def test_oversized_backtest_tables_are_noted_not_fatal(tmp_path):
    workbench_or_skip(tmp_path)
    store, engine, owner, _ = session(tmp_path)
    store.blobs.limit = 64  # smaller than any fixture table
    engine.run(owner, wall_seconds=600)
    observations = [store.artifacts.get(r) for r in engine.state(owner)["observations"]]
    assert observations and all(o["status"] == "SUCCEEDED" for o in observations)
    assert any(o.get("tables_not_stored") for o in observations)
