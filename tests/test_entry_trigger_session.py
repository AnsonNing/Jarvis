import json
import sys
from pathlib import Path

import pytest
from test_iris_queue import iris_environment as iris_environment
from test_iris_worker import local_case

from jarvis.contracts import Budget, IntegrityError
from jarvis.identity import file_digest
from jarvis.iris import render_candidate
from jarvis.iris_contracts import (
    IrisBinding,
    IrisExperiment,
    IrisPeriod,
    IrisQueueSpec,
    IrisSettings,
)
from jarvis.iris_lab_session import IrisLabSession, IrisLabSessionSpec
from jarvis.iris_quota import IrisQuotaPolicy
from jarvis.iris_selected import SelectedBaselineSpec, prepare_selected
from jarvis.lab_fixture import fixture_lab_engine, fixture_lab_spec
from jarvis.providers import CodexProvider
from jarvis.research_acceptance import ResearchAcceptance, assess_candidate
from jarvis.research_notebook import export_notebook
from jarvis.research_queue import IrisResearchQueue, queue_tasks, verify_queue
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


def test_trigger_is_distinct_from_score_cutoff_and_preserves_other_side(iris_environment):
    env = iris_environment
    source = Path(env.template)
    source.write_text(
        source.read_text().replace(
            "// @start:config",
            "// @start:config\nconstexpr double toy_trigger = 40 * 0.0001;\nconstexpr double other_trigger = 0.004;",
        )
    )
    env = env.model_copy(
        update={
            "template_sha256": file_digest(source),
            "bindings": (
                *env.bindings,
                IrisBinding(
                    setting="entry_trigger_bps",
                    symbol="toy_trigger",
                    region="config",
                    syntax="double",
                    scale=0.0001,
                    minimum=0,
                    maximum=0.1,
                ),
            ),
        }
    )
    candidate = render_candidate(env, IrisSettings(entry_trigger_bps=30))
    assert "toy_trigger = 0.0030000000000000001;" in candidate
    assert "other_trigger = 0.004;" in candidate and "toy_entry = 0;" in candidate
    assert render_candidate(env, IrisSettings()) == source.read_text()
    assert IrisExperiment(
        label="trigger",
        family="entry",
        hypothesis="test",
        falsification="no change",
        settings=IrisSettings(entry_trigger_bps=30),
        origin="declared_fixture",
    )
    with pytest.raises(ValueError):
        IrisSettings(entry_trigger_bps=-1)


def test_explicit_effort_is_in_command_and_legacy_serialization_unchanged(tmp_path):
    assert "reasoning_effort" not in AgentConfig().model_dump()
    assert "entry_trigger_bps" not in IrisSettings().model_dump()
    provider = object.__new__(CodexProvider)
    provider.executable = Path(sys.executable)
    provider.config = AgentConfig(model="gpt-6.1-sol", reasoning_effort="high")
    command = provider.command(tmp_path)
    assert command[command.index("--model") + 1] == "gpt-6.1-sol"
    assert 'model_reasoning_effort="high"' in command
    assert "features.multi_agent=false" in command


@pytest.mark.parametrize(
    "pl,pr,mdd,qualified",
    [(2, 0.1, -1, True), (2, 0.09, -1, False), (2, 0.1, -2, False), (1, 0.1, -1, False)],
)
def test_pr_and_risk_are_explicit_selection_constraints(pl, pr, mdd, qualified):
    baseline = {"entry_only_pl": 1, "mdd": -1, "risk": {"mean_pr": 0.1}}
    candidate = {"entry_only_pl": pl, "mdd": mdd, "risk": {"mean_pr": pr}}
    assert assess_candidate(candidate, baseline, ResearchAcceptance())["qualified"] == qualified
    assert (
        assess_candidate({"entry_only_pl": 2}, baseline, ResearchAcceptance())["status"]
        == "INSUFFICIENT_EVIDENCE"
    )


def sliced_case(env):
    env, proof = local_case(env)
    raw = Path(proof.raw)
    raw.write_text("txdate,product,dynamicexit,pl\n2026-01-05,A,0,3\n2026-01-06,B,0,-1\n")
    evaluator = Path(env.evaluator)
    evaluator.write_text("""
import csv
def evaluate(path, *, trading_dates, expected_commit):
    assert expected_commit == "abc1234"
    with open(path) as stream: rows=list(csv.DictReader(stream))
    assert set(r['txdate'] for r in rows) <= set(trading_dates)
    daily={d:sum(float(r['pl']) for r in rows if r['txdate']==d and r['dynamicexit']=='0') for d in trading_dates}
    return {"status":"PASS","daily_calendar":"explicit_trading_dates","metrics":{
      "count":len(rows),"pl":str(sum(daily.values())),"daily_realized_mdd":"-1","worst_day_pl":str(min(daily.values())),
      "daily_pl":daily,"mean_pr":".1","zero_entry_days":sum(v==0 for v in daily.values())},
      "entry_execution_sha256":"fixed-synthetic-entries","snapshot_rows_excluded":0}
""")
    env = env.model_copy(
        update={
            "evaluator_sha256": file_digest(evaluator),
            "period": IrisPeriod(
                start="2026-01-06",
                end="2026-01-06",
                trading_dates=("2026-01-06",),
                group_id="toy-dev",
            ),
        }
    )
    proof = proof.model_copy(
        update={
            "raw_sha256": file_digest(raw),
            "selection_dates": env.period.trading_dates,
            "parent_trading_dates": IrisPeriod(
                start="2026-01-05",
                end="2026-01-06",
                trading_dates=("2026-01-05", "2026-01-06"),
                group_id="parent",
            ).trading_dates,
        }
    )
    return env, proof


def test_explicit_baseline_slice_preserves_parent_job_and_has_zero_uploads(
    tmp_path, iris_environment
):
    env, proof = sliced_case(iris_environment)
    store = Store(tmp_path / "evidence")
    engine = IrisResearchQueue(store)
    owner = engine.create(IrisQueueSpec(goal="test", environment=env, baseline=proof))
    engine.enqueue(
        owner,
        IrisExperiment(
            label="baseline",
            family="baseline",
            hypothesis="fixed",
            falsification="mismatch",
            origin="declared_fixture",
        ),
    )
    answer = engine.tick(owner)
    assert answer["remote_submissions"] == 0
    row = queue_tasks(store, owner)[0]
    result = store.artifacts.get(row["result_ref"])
    assert result["entry_only_pl"] == -1 and result["count"] == 1
    assert result["provenance"]["parent_raw_sha256"] == proof.raw_sha256
    assert file_digest(Path(proof.raw)) == proof.raw_sha256
    assert result["provenance"]["selection_dates"] == ["2026-01-06"]
    assert result["provenance"]["parent_period"] == "2026/01/05-2026/01/06"
    assert verify_queue(store, owner)["submissions"] == 0


def test_selected_package_admission_checks_hashes_commit_and_exposure(tmp_path, iris_environment):
    env, proof = sliced_case(iris_environment)
    package = Path(env.root)
    manifest = json.loads(Path(proof.manifest).read_text())
    manifest["branch"] = env.branch
    manifest["cases"][0]["side"] = "LONG"
    Path(proof.manifest).write_text(json.dumps(manifest))
    models = []
    for name in ["model_a.hpp", "model_b.hpp"]:
        path = package / name
        path.write_text("// declared fixture model")
        models.append(
            {
                "side": "LONG",
                "hpp_file": name,
                "hpp_sha256": file_digest(path)[7:],
                "max_score_error": 0,
                "decision_mismatches": 0,
            }
        )
    settings = package / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "branch": env.branch,
                "commits": [
                    {"side": "LONG", "commit": "abc1234", "source_sha256": env.template_sha256[7:]}
                ],
                "models": models,
            }
        )
    )
    calendar = package / "calendar.json"
    calendar.write_text(json.dumps({"01": ["2026-01-05", "2026-01-06"]}))
    index = package / "hashes.json"
    index.write_text(
        json.dumps(
            {name: file_digest(package / name)[7:] for name in ["model_a.hpp", "model_b.hpp"]}
        )
    )
    spec = SelectedBaselineSpec(
        package=str(package),
        manifest=proof.manifest,
        settings=str(settings),
        hash_index=str(index),
        calendar=str(calendar),
        source=env.template,
        api_root=env.root,
        python=sys.executable,
        evaluator=env.evaluator,
        side="long",
        baseline_commit="abc1234",
        case_label="accepted",
        period=env.period,
        bindings=env.bindings,
        owner_instruction="fixture owner selection",
    )
    selected, p = prepare_selected(spec)
    assert selected.side == "long" and p.selection_dates == env.period.trading_dates
    with pytest.raises(IntegrityError, match="lineage"):
        prepare_selected(spec.model_copy(update={"baseline_commit": "bad1234"}))
    (package / "model_a.hpp").write_text("changed")
    with pytest.raises(IntegrityError, match="bytes"):
        prepare_selected(spec)


def test_real_session_model_scope_quota_and_complete_synthetic_lab(tmp_path):
    store = Store(tmp_path / "evidence")
    lab, backends = fixture_lab_engine(store)
    a = fixture_lab_spec(store)
    cfg = AgentConfig(
        model="gpt-6.1-sol", reasoning_effort="high", reserve_tokens=1000, max_calls=16
    )
    a = a.model_copy(
        update={
            "role_config": cfg,
            "program": a.program.model_copy(update={"agent": cfg, "critic": cfg}),
        }
    )
    b = a.model_copy(update={"task_id": "following-research"})
    spec = IrisLabSessionSpec(
        stages=(a, b),
        phases=("smoke", "development"),
        quota_policy=IrisQuotaPolicy(scope="fixture-shared"),
        owner_instruction="fixture standing authority",
        model=cfg.model,
        reasoning_effort=cfg.reasoning_effort,
        budget=Budget(
            max_actions=1000,
            max_tool_calls=1000,
            max_compute_units=1000,
            max_llm_tokens=100000,
            max_wall_seconds=10000,
            max_parallel_jobs=2,
        ),
        shadow_review=True,
        propose_capability=True,
    )
    with pytest.raises(ValueError, match="all session LLM"):
        IrisLabSessionSpec.model_validate(spec.model_copy(update={"model": "other"}).model_dump())
    session = IrisLabSession(store, lab)
    owner = session.create(spec)
    assert session.refresh_quota(owner, [])["status"] == "AVAILABLE"
    for _ in range(100):
        result = session.advance(owner)
        if result["status"] in {"CLOSED", "SMOKE_NEEDS_ATTENTION"}:
            break
    assert result["status"] == "CLOSED"
    report = session.report(owner)
    assert len(report["stages"]) == 2 and report["quota_verification"]["external_calls"] == 0
    assert not any(report["usage"]["pending"].values())
    assert len(store.events(owner)) > 0
    reviews = [e for e in store.events(owner) if e["event_type"] == "SESSION_SHADOW_REVIEW"]
    assert len(reviews) == 2 and all(not e["payload"]["promoted"] for e in reviews)
    capability = next(
        e["payload"]
        for e in store.events(owner)
        if e["event_type"] == "SESSION_CAPABILITY_CANDIDATE"
    )
    assert capability["status"] == "FIDELITY_TESTED_UTILITY_UNVERIFIED"
    before = store.resource_totals_tree(owner)
    out = export_notebook(store, owner, tmp_path / "private-report")
    assert out["role_calls"] > 0 and out["external_calls"] == 0
    assert store.resource_totals_tree(owner) == before
    audit = json.loads((tmp_path / "private-report/audit.json").read_text())
    assert all(r["model"] == "gpt-6.1-sol" and r["effort"] == "high" for r in audit["roles"])
