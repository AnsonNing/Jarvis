"""Synthetic-only acceptance for trusted delivery, evidence views and stage gates."""

import pytest

from jarvis.autonomous_research import AutonomousResearch
from jarvis.autonomy_contracts import (
    AcceptanceProfile,
    AnalysisDelivery,
    AutonomyContract,
    AutonomySessionSpec,
    BacktestAction,
    Candidate,
    MetricSpec,
    Objective,
    ResearchStages,
    RiskLimit,
    SettingSpec,
)
from jarvis.autonomy_evidence import build_review_bundle
from jarvis.autonomy_report import deliverable, replay_session, session_outcome, verify_session
from jarvis.autonomy_stages import admit_staged_backtest, configuration
from jarvis.contracts import Budget, IntegrityError
from jarvis.quant_fixture import QuantFixtureProvider
from jarvis.quant_world import analysis_environment
from jarvis.research_executors import Executors
from jarvis.storage import Store
from jarvis.workbench import Workbench


def contract(store, **updates):
    baseline = store.artifacts.put(
        Candidate(
            label="baseline",
            parent_ref=None,
            model_ref=None,
            origin="owner_baseline",
            data_policy="SIMULATED_FIXTURE",
        )
    )
    values = dict(
        task_id="trusted_fixture",
        instruction="Synthetic staged research acceptance",
        objective="Improve fixture PL",
        focus="delivery and stages",
        domain="synthetic-trusted",
        evaluator_id="fixed-fixture-v1",
        dataset_id="fixture-v1",
        periods=("smoke", "dev", "confirm"),
        exposure="synthetic",
        baseline_candidate_ref=baseline,
        settings=(
            SettingSpec(
                name="bp",
                kind="float",
                minimum=1,
                maximum=100,
                baseline=40,
                description="fixture setting",
            ),
        ),
        metrics=(
            MetricSpec(name="pl", direction="maximize", description="fixture units"),
            MetricSpec(name="mdd", direction="minimize", description="fixture magnitude"),
            MetricSpec(name="mean_pr", direction="maximize", description="fixture units"),
        ),
        acceptance_profiles=(
            AcceptanceProfile(
                name="pl_first",
                objectives=(Objective(metric="pl"),),
                risk_limits=(
                    RiskLimit(metric="mdd", max_worsening=0.1),
                    RiskLimit(metric="mean_pr", max_worsening=0.05),
                ),
                description="Synthetic control",
            ),
        ),
        analysis_environment=analysis_environment(),
        constraints=("SIMULATED_FIXTURE only",),
        origin="declared_fixture",
        analysis_delivery=AnalysisDelivery(),
        stages=ResearchStages(screening=("smoke",), selection="dev", confirmation=("confirm",)),
    )
    return AutonomyContract(**(values | updates))


def owner(store, c):
    spec = AutonomySessionSpec(contract_ref=store.artifacts.put(c))
    return store.create(
        {
            "kind": "AutonomousResearchSession",
            "spec": spec.model_dump(mode="json"),
            "world_id": "fixture",
            "task": {"budget": spec.budget.model_dump()},
        }
    )


def observation(store, o, c, period, bp, *, pl=120, risk=10):
    row = dict(
        kind="BacktestObservation",
        label=f"{period}_{bp}",
        status="SUCCEEDED",
        candidate_ref=c.baseline_candidate_ref,
        candidate_origin="owner_baseline",
        settings={"bp": bp},
        changes={} if bp == 40 else {"bp": bp},
        period=period,
        evaluator_id=c.evaluator_id,
        dataset_id=c.dataset_id,
        metrics={"pl": 100 if bp == 40 else pl, "mdd": risk, "mean_pr": 1.0, "count": 100},
        model_id=None,
        data_policy="SIMULATED_FIXTURE",
        tables={},
        result_origin="SIMULATED_FIXTURE",
    )
    ref = store.artifacts.put(row)
    store.append(
        o,
        "ARTIFACTS_REGISTERED",
        {
            "index": 0,
            "tables": [],
            "candidates": [],
            "observations": [ref],
            "patches": [],
            "models": [],
        },
    )
    return ref


def backtest(c, period, bp=30):
    return BacktestAction(
        branch_id="b",
        question_id="q",
        hypothesis_ids=("h",),
        rationale="fixture",
        expected_observation="fixture",
        evidence_refs=(),
        design="fixture",
        experiments=(
            {
                "label": "test",
                "candidate_ref": c.baseline_candidate_ref,
                "settings": ({"name": "bp", "value": bp},),
                "period": period,
                "hypothesis": "fixture",
                "falsification": "fixture",
            },
        ),
    )


def freeze(store, o, c):
    out = session_outcome(store, o)
    receipt = admit_staged_backtest(store, o, c, backtest(c, "confirm"), out)
    store.append(o, "CONFIRMATION_CANDIDATE_FROZEN", {**receipt, "decision_index": 0})


def test_legacy_serialization_and_outcome_are_unchanged(tmp_path):
    s = Store(tmp_path)
    c = contract(s, stages=None, analysis_delivery=None)
    assert "stages" not in c.model_dump() and "analysis_delivery" not in c.model_dump()
    o = owner(s, c)
    observation(s, o, c, "smoke", 40)
    observation(s, o, c, "smoke", 30)
    assert session_outcome(s, o)["finalist"]["status"] == "SELECTED"
    assert "stage_gate" not in session_outcome(s, o)


def test_stages_must_be_complete_and_disjoint(tmp_path):
    s = Store(tmp_path)
    with pytest.raises(ValueError, match="distinct"):
        ResearchStages(screening=("dev",), selection="dev")
    with pytest.raises(ValueError, match="every declared period"):
        contract(s, stages=ResearchStages(selection="dev"))


def test_smoke_cannot_select_or_authorize_confirmation(tmp_path):
    s = Store(tmp_path)
    c = contract(s)
    o = owner(s, c)
    observation(s, o, c, "smoke", 40)
    observation(s, o, c, "smoke", 30, pl=1000)
    out = session_outcome(s, o)
    assert out["stage_gate"] == "NO_SELECTION_CANDIDATE"
    assert out["finalist"]["status"] != "SELECTED"
    with pytest.raises(ValueError, match="selection period"):
        admit_staged_backtest(s, o, c, backtest(c, "confirm"), out)


@pytest.mark.parametrize("risk,verdict", [(10, "PASS"), (12, "CONFIRMATION_FAILED")])
def test_confirmation_requires_exact_frozen_configuration_and_risk_gate(tmp_path, risk, verdict):
    s = Store(tmp_path)
    c = contract(s)
    o = owner(s, c)
    observation(s, o, c, "dev", 40)
    observation(s, o, c, "dev", 30)
    assert session_outcome(s, o)["stage_gate"] == "AWAITING_CONFIRMATION"
    freeze(s, o, c)
    observation(s, o, c, "confirm", 40)
    observation(s, o, c, "confirm", 30, risk=risk)
    out = session_outcome(s, o)
    assert out["stage_gate"] == verdict
    assert (out["finalist"]["status"] == "SELECTED") == (verdict == "PASS")
    assert out["independent_validation"] == "NOT_RUN"
    with pytest.raises(ValueError, match="frozen"):
        admit_staged_backtest(s, o, c, backtest(c, "dev", 25), out)
    with pytest.raises(ValueError, match="frozen candidate"):
        admit_staged_backtest(s, o, c, backtest(c, "confirm", 25), out)


def test_prefreeze_or_other_configuration_cannot_supply_confirmation(tmp_path):
    s = Store(tmp_path)
    c = contract(s)
    o = owner(s, c)
    observation(s, o, c, "dev", 40)
    observation(s, o, c, "dev", 30)
    observation(s, o, c, "confirm", 40)
    observation(s, o, c, "confirm", 30)
    freeze(s, o, c)
    assert session_outcome(s, o)["stage_gate"] == "AWAITING_CONFIRMATION"
    observation(s, o, c, "confirm", 25)
    assert session_outcome(s, o)["stage_gate"] == "AWAITING_CONFIRMATION"
    assert configuration(s, c, c.baseline_candidate_ref, {"bp": 30}) == configuration(
        s, c, c.baseline_candidate_ref, {"bp": 30.0}
    )


def test_every_confirmation_period_requires_a_matched_baseline(tmp_path):
    s = Store(tmp_path)
    c = contract(
        s,
        periods=("smoke", "dev", "c1", "c2"),
        stages=ResearchStages(screening=("smoke",), selection="dev", confirmation=("c1", "c2")),
    )
    o = owner(s, c)
    observation(s, o, c, "dev", 40)
    observation(s, o, c, "dev", 30)
    receipt = admit_staged_backtest(s, o, c, backtest(c, "c1"), session_outcome(s, o))
    s.append(o, "CONFIRMATION_CANDIDATE_FROZEN", {**receipt, "decision_index": 0})
    observation(s, o, c, "c1", 40)
    observation(s, o, c, "c1", 30)
    observation(s, o, c, "c2", 30)
    assert session_outcome(s, o)["stage_gate"] == "AWAITING_CONFIRMATION"
    observation(s, o, c, "c2", 40)
    assert session_outcome(s, o)["stage_gate"] == "PASS"


class FixtureBacktester:
    def __init__(self, fail_confirmation=False):
        self.fail_confirmation = fail_confirmation

    def identity(self):
        return {"kind": "synthetic-trusted", "fail_confirmation": self.fail_confirmation}

    def run(self, candidate, settings, period, workdir):
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "rows.csv").write_text("value\n1\n2\n3\n")
        base = settings["bp"] == 40
        return {
            "status": "SUCCEEDED",
            "metrics": {
                "pl": 100 if base else 120,
                "mdd": 12 if not base and period == "confirm" and self.fail_confirmation else 10,
                "mean_pr": 1.0,
                "count": 100,
            },
            "daily": {},
            "result_origin": "SIMULATED_FIXTURE",
            "provenance": {
                "request_id": f"fixture-{period}-{settings['bp']}",
                "calendar": ["fixture-day"],
            },
            "tables": [
                {
                    "name": "trades",
                    "path": str(workdir / "rows.csv"),
                    "description": "Synthetic values",
                }
            ],
        }


class StagedProvider(QuantFixtureProvider):
    def critic(self, c):
        answer = super().critic(c)
        answer["next_questions"] = ["Synthetic recommendation retained in review history"]
        return answer

    def director(self, c):
        rows = c["experiment_ledger"]
        obs = c["observations"]
        base = c["research_contract"]["baseline_candidate_ref"]
        periods = {r["period"] for r in rows}
        action = None
        if "smoke" not in periods:
            action = self.batch(base, "smoke")
        elif not any(o["kind"] == "ANALYZE" for o in obs):
            action = {
                "kind": "ANALYZE",
                "question": "Sum synthetic observed values",
                "input_refs": [rows[0]["tables"]["trades"]],
            }
        elif "dev" not in periods:
            action = self.batch(base, "dev")
        elif not any(o["kind"] == "COMPARE" for o in obs):
            action = self.compare(rows, "dev")
        elif "confirm" not in periods:
            action = self.batch(base, "confirm")
        elif sum(o["kind"] == "COMPARE" for o in obs) < 2:
            action = self.compare(rows, "confirm")
        else:
            passed = c["stage_progress"]["gate"] == "PASS"
            action = {
                "kind": "STOP",
                "scope": "session",
                "conclusion": "research_complete" if passed else "inconclusive",
                "closures": [
                    {
                        "branch_id": "b",
                        "conclusion": "supported" if passed else "negative",
                        "reason": "Declared stages evaluated",
                        "evidence_refs": [obs[-1]["result_ref"]],
                    }
                ],
                "finalist_result_ref": c["stage_progress"]["selection_finalist"].get("result_ref")
                if passed
                else None,
            }
        first = not c["research_plan"]["branches"]
        return {
            "context_id": c["context_id"],
            "situation": "Synthetic acceptance",
            "guidance_responses": [],
            "branch": {"branch_id": "b", "kind": "parameter", "goal": "Evaluate stages"}
            if first
            else None,
            "question": {
                "question_id": "q",
                "parent_id": None,
                "question": "Does fixture improve?",
                "hypotheses": [
                    {
                        "hypothesis_id": "h",
                        "claim": "Improves",
                        "prediction": "PL increases",
                        "falsification": "PL does not increase",
                        "evidence_refs": [],
                    },
                    {
                        "hypothesis_id": "h-null",
                        "claim": "No improvement",
                        "prediction": "PL stays unchanged",
                        "falsification": "PL increases",
                        "evidence_refs": [],
                    },
                ],
            }
            if first
            else None,
            "action": {
                "branch_id": "b",
                "question_id": "q",
                "hypothesis_ids": ["h"],
                "rationale": "Synthetic acceptance",
                "expected_observation": "Observed fixture evidence",
                "evidence_refs": [],
                **action,
            },
        }

    def batch(self, base, period):
        return {
            "kind": "BACKTEST",
            "design": "Synthetic paired control",
            "experiments": [
                {
                    "label": f"{period}_{bp}",
                    "candidate_ref": base,
                    "settings": [] if bp == 40 else [{"name": "bp", "value": bp}],
                    "period": period,
                    "hypothesis": "Fixture improves",
                    "falsification": "No improvement",
                }
                for bp in (40, 30)
            ],
        }

    def compare(self, rows, period):
        r = [r for r in rows if r["period"] == period]
        return {
            "kind": "COMPARE",
            "baseline_result_ref": r[0]["result_ref"],
            "result_refs": [r[1]["result_ref"]],
        }

    def worker(self, c):
        t = c["transcript"]
        blank = dict(
            context_id=c["context_id"],
            note="Synthetic analysis",
            path=None,
            content=None,
            arguments=[],
            summary=None,
            findings=[],
            output_files=[],
            final_script=None,
        )
        if not t:
            return {
                **blank,
                "tool": "run_python",
                "path": "work/calculate.py",
                "content": "import csv,glob\np=glob.glob('inputs/*.csv')[0]\nv=sum(int(r['value']) for r in csv.DictReader(open(p)))\nopen('work/answer.csv','w').write('sum\\n'+str(v)+'\\n')\nprint(v)\n",
            }
        # Deliberately incomplete first delivery: the same worker must repair it.
        if not any(x["request"]["tool"] == "finish" for x in t):
            return {
                **blank,
                "tool": "finish",
                "summary": "Computed sum 6, but omitted delivery files",
            }
        return {
            **blank,
            "tool": "finish",
            "summary": "Computed synthetic sum 6",
            "output_files": ["answer.csv"],
            "final_script": "calculate.py",
        }


class NonrepeatableProvider(StagedProvider):
    def worker(self, c):
        answer = super().worker(c)
        if not c["transcript"]:
            return {
                **answer,
                "content": "import random\nopen('work/answer.csv','w').write('sum\\n'+str(random.random())+'\\n')\n",
            }
        if c["transcript"][-1]["observation"].get("status") == "NONREPRODUCIBLE_OUTPUTS":
            clean = super().worker({**c, "transcript": []})
            return clean
        return answer


@pytest.mark.parametrize(
    "fail_confirmation,provider",
    [(False, StagedProvider), (True, StagedProvider), (False, NonrepeatableProvider)],
)
def test_complete_staged_session_repairs_delivery_and_audits(tmp_path, fail_confirmation, provider):
    bench = Workbench(analysis_environment())
    if bench.probe(tmp_path / "probe")["status"] != "PASS":
        pytest.skip("OS isolation unavailable")
    s = Store(tmp_path / "store")
    c = contract(s)
    from jarvis.autonomy_contracts import SourceScope
    from jarvis.identity import file_digest

    source = tmp_path / "declared_source.py"
    source.write_text("VERSION = 'synthetic'\n")
    c = c.model_copy(
        update={
            "source_scopes": (
                SourceScope(
                    name="fixture_source",
                    path=str(source),
                    sha256=file_digest(source),
                    kind="document",
                    description="Synthetic declared source",
                ),
            )
        }
    )
    engine = AutonomousResearch(
        s,
        Executors(bench, backtester=FixtureBacktester(fail_confirmation)),
        lambda cfg, role: provider(role),
    )
    o = engine.create(
        AutonomySessionSpec(
            contract_ref=s.artifacts.put(c),
            max_decisions=12,
            max_concurrent_actions=1,
            budget=Budget(
                max_actions=200,
                max_tool_calls=200,
                max_llm_tokens=1000000,
                max_wall_seconds=1000,
                max_compute_units=500,
                max_parallel_jobs=4,
            ),
        )
    )
    assert engine.run(o, wall_seconds=120)["status"] == "CLOSED"
    report = deliverable(s, o)
    assert report["stage_gate"] == ("CONFIRMATION_FAILED" if fail_confirmation else "PASS")
    assert report["validation_verdict"] == (
        "NO_DEVELOPMENT_IMPROVEMENT" if fail_confirmation else "DEVELOPMENT_IMPROVEMENT"
    )
    assert report["independent_validation"] == "NOT_RUN"
    assert verify_session(s, o)["validation"] == "VERIFIED"
    assert replay_session(s, o)["external_calls"] == 0
    packs = [e["payload"] for e in s.events(o) if e["event_type"] == "REVIEW_EVIDENCE_PACKED"]
    analysis_pack = next(
        s.artifacts.get(p["evidence_ref"])
        for p in packs
        if s.artifacts.get(p["result_ref"]).get("kind") == "AnalysisResult"
    )
    assert analysis_pack["reproducible"]["all_identical"] is True
    assert analysis_pack["reproducible"]["origin"] == "trusted_harness"
    assert analysis_pack["reproducible"]["execution"]["fresh_process_started"] is True
    assert analysis_pack["reproducible"]["execution"]["clean_workspace"] is True
    assert analysis_pack["reproducible"]["execution"]["exit_code"] == 0
    assert any(t["preview"] == [{"sum": "6"}] for t in analysis_pack["tables"])
    state = engine.state(o)
    from jarvis.autonomy_report import active_research_directions
    from jarvis.research_memory import KnowledgeBase

    assert report["next_research_directions"] == []
    assert KnowledgeBase(s).facts(o)["open_questions"] == []
    closure = next(e for e in s.events(o) if e["event_type"] == "BRANCH_CLOSED")
    prior = engine.state(o, before_sequence=closure["sequence"])
    assert active_research_directions(s, prior, respect_closures=True)
    assert active_research_directions(s, state, respect_closures=False)
    analysis_index = next(i for i, a in state["completed"].items() if a["kind"] == "ANALYZE")
    steps = [
        e["payload"]
        for e in s.events(state["launched"][analysis_index])
        if e["event_type"] == "WORKER_STEP"
    ]
    statuses = [s.artifacts.get(p["observation_ref"])["status"] for p in steps]
    assert statuses[-1] == "OK" and statuses.count("INVALID_DELIVERY") == 1
    assert statuses.count("NONREPRODUCIBLE_OUTPUTS") == (
        1 if provider is NonrepeatableProvider else 0
    )
    contexts = [
        s.artifacts.get(e["payload"]["context_ref"])
        for e in s.events(o)
        if e["event_type"] == "LAB_ROLE_RECORDED" and e["payload"]["role"] == "autonomy_critic"
    ]
    assert contexts and all("trusted_evidence" in x for x in contexts)
    assert all("identity_semantics" in x["trusted_evidence"] for x in contexts)
    worker_contexts = [
        s.artifacts.get(x["payload"]["context_ref"])
        for x in s.events(state["launched"][analysis_index])
        if x["event_type"] == "LAB_ROLE_RECORDED" and x["payload"]["role"] == "autonomy_worker"
    ]
    from jarvis.identity import raw_digest

    supplied_source = worker_contexts[0]["task"]["source_manifest"][0]
    materialized = (
        engine.workdir(state["launched"][analysis_index]) / "workspace" / supplied_source["path"]
    )
    assert supplied_source["sha256"] == file_digest(source) == file_digest(materialized)
    for item in worker_contexts[0]["task"]["inputs"]:
        assert item["ref"] == item["artifact_ref"]
        table = s.artifacts.get(item["artifact_ref"])
        assert item["blob_ref"] == raw_digest(s.blobs.get(table["blob_ref"]))
        assert item["artifact_ref"] != item["blob_ref"]
        assert "file bytes" in item["identity_semantics"]
    comparison_contexts = [x for x in contexts if x["decision"]["action"]["kind"] == "COMPARE"]
    assert comparison_contexts and all(
        len(x["trusted_evidence"]["experiments"]) == 2 for x in comparison_contexts
    )
    with pytest.raises(IntegrityError, match="not yet visible"):
        build_review_bundle(s, o, 0, packs[0]["result_ref"], {}, [], None)


def test_missing_or_nonfinite_risk_is_never_a_passing_candidate(tmp_path):
    from jarvis.candidates import assess_profile

    s = Store(tmp_path)
    c = contract(s)
    baseline = {"pl": 100, "mdd": 10, "mean_pr": 1, "count": 100}
    for invalid in (None, float("nan"), float("inf"), True):
        result = assess_profile(
            c, c.acceptance_profiles[0], {**baseline, "pl": 120, "mdd": invalid}, baseline
        )
        assert result["verdict"] == "INSUFFICIENT_DATA"
