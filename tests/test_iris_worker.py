import json
import sys
from pathlib import Path

import pytest
from test_iris_queue import iris_environment as iris_environment
from test_iris_queue import trial

from jarvis.contracts import AdmissionError, IntegrityError
from jarvis.identity import file_digest
from jarvis.iris import LocalIrisTransport
from jarvis.iris_admission import model_status, prepare_environment
from jarvis.iris_contracts import IrisBaselineProof, IrisQueueSpec
from jarvis.research_queue import IrisResearchQueue, queue_tasks, verify_queue
from jarvis.storage import Store

EVALUATOR = """
def evaluate(path, *, trading_dates, expected_commit):
    assert expected_commit == "abc1234"
    assert trading_dates == ["2026-01-05", "2026-01-06"]
    return {"status":"PASS", "daily_calendar":"explicit_trading_dates",
      "metrics":{"count":2,"pl":"2", "daily_realized_mdd":"-1", "worst_day_pl":"-1",
        "daily_pl":{trading_dates[0]:"3",trading_dates[1]:"-1"}, "mean_pr":"0.1", "zero_entry_days":0},
      "entry_execution_sha256":"toy-entries", "snapshot_rows_excluded":1}
"""


def local_case(env):
    root = Path(env.root)
    raw = root / "sim.hermes_trades.csv"
    raw.write_text("product,dynamicexit,pl\nA,0,3\nA,-1,999\nB,0,-1\n")
    job = {
        "strategy": "hermes",
        "branch": "toy",
        "period": ["2026/01/05", "2026/01/06"],
        "sha": "abc1234",
    }
    case = {
        "label": "accepted",
        "status": "completed",
        "request_id": "1234abcd",
        "sha256": env.template_sha256[7:],
        "result_exit_code": 0,
        "job": job,
        "result_output": "2 days, 0 failed",
        "result_dir": str(root),
    }
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"cases": [case]}))
    Path(env.evaluator).write_text(EVALUATOR)
    Path(env.root, "iris_agent.py").write_text(
        "raise AssertionError('credential API must not be imported for local baseline')\n"
    )
    return env.model_copy(
        update={
            "result_origin": "OBSERVED_IRIS_BACKTEST",
            "python": sys.executable,
            "evaluator_sha256": file_digest(Path(env.evaluator)),
            "agent_script_sha256": file_digest(root / "iris_agent.py"),
            "accepted_inputs": {str(manifest): file_digest(manifest)},
        }
    ), IrisBaselineProof(
        manifest=str(manifest), label="accepted", raw=str(raw), raw_sha256=file_digest(raw)
    )


def test_local_baseline_is_recomputed_without_loading_credentials_or_submitting(
    tmp_path, iris_environment, monkeypatch
):
    env, proof = local_case(iris_environment)
    store = Store(tmp_path / "evidence")
    queue = IrisResearchQueue(store)
    qid = queue.create(IrisQueueSpec(goal="Entry-only objective", environment=env, baseline=proof))
    queue.enqueue(qid, trial())
    result = queue.tick(qid)
    assert result["baseline_reused"] and result["remote_submissions"] == 0
    row = queue_tasks(store, qid)[0]
    observed = store.artifacts.get(row["result_ref"])
    assert observed["entry_only_pl"] == 2 and observed["count"] == 2
    assert observed["provenance"]["snapshots_excluded"] == 1
    assert observed["risk"]["largest_positive_product_share"] == 1
    assert store.resource_totals(qid)["actual"]["tool_calls"] == 0
    monkeypatch.setattr(
        LocalIrisTransport, "invoke", lambda *_: pytest.fail("audit invoked worker")
    )
    Path(proof.raw).unlink()
    assert verify_queue(store, qid)["submissions"] == 0


def test_baseline_ledger_drift_is_rejected_before_worker(tmp_path, iris_environment):
    env, proof = local_case(iris_environment)
    store = Store(tmp_path / "evidence")
    queue = IrisResearchQueue(store)
    qid = queue.create(IrisQueueSpec(goal="Entry-only objective", environment=env, baseline=proof))
    queue.enqueue(qid, trial())
    Path(proof.raw).write_text("changed")
    with pytest.raises(IntegrityError, match="proof"):
        queue.tick(qid)
    assert not store.jobs(qid)


def test_private_worker_submission_and_collection_contract(tmp_path, iris_environment):
    env, _ = local_case(iris_environment)
    root = Path(env.root)
    agent = root / "iris_agent.py"
    agent.write_text("""
import json
from pathlib import Path
QUEUED,REJECTED,JOB,DONE="queued.txt","rejected.txt","job.json","done.txt"
def load_config(): return {"name":"fixture"}
def upload(cfg, candidate, comment):
    assert 'req:1234abcd' in comment and candidate.exists()
    Path("upload-count.txt").write_text("one")
def answers(cfg, rid, unused): return {QUEUED:0,JOB:1,DONE:2,"sim.hermes_trades.csv":3}
def download(cfg, ref):
    return ["fixture quota acknowledgement",json.dumps({"strategy":"hermes","branch":"toy","period":["2026/01/05","2026/01/06"],"sha":"abc1234"}),"2 days, 0 failed"][ref]
def exit_code(reply): return 0
def outcome(cfg, rid, found, directory):
    directory.mkdir(parents=True,exist_ok=True)
    (directory/"sim.hermes_trades.csv").write_text("product,dynamicexit,pl\\nA,0,3\\nA,-1,999\\nB,0,-1\\n")
    return 0
def result_dir(cfg,rid,found,directory): return directory
""")
    env = env.model_copy(update={"agent_script_sha256": file_digest(agent)})
    transport = LocalIrisTransport(env)
    request = {
        "request_id": "1234abcd",
        "candidate_path": env.template,
        "candidate_sha256": env.template_sha256,
        "period": env.period.request_period,
    }
    directory = tmp_path / "worker"
    assert transport.submit(request, directory)["status"] == "SUBMITTED"
    assert transport.submit(request, directory)["status"] == "SUBMITTED"  # cached ack
    assert transport.collect(request, directory)["entry_only_pl"] == 2
    assert (root / "upload-count.txt").read_text() == "one"


def test_incomplete_multimonth_gate_does_not_prepare_environment(tmp_path):
    assert model_status(tmp_path)["status"] == "WAITING_FOR_MODEL_ACCEPTANCE"
    with pytest.raises(AdmissionError, match="WAITING_FOR_MODEL_ACCEPTANCE"):
        prepare_environment(tmp_path, "long", "08", Path(sys.executable))
