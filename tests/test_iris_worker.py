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
    return ["fixture quota acknowledgement",json.dumps({"strategy":"hermes","branch":"toy","period":["2026/01/05","2026/01/06"],"sha":"abc1234"}),"2 days, 0 failed","product,dynamicexit,pl\\nA,0,3\\nA,-1,999\\nB,0,-1\\n"][ref]
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
    (tmp_path / "workflow_state.json").write_text('{"stage":"needs_attention"}')
    assert model_status(tmp_path)["status"] == "MODEL_ACCEPTANCE_FAILED"
    (tmp_path / "workflow_state.json").write_text('{"stage":')
    assert model_status(tmp_path)["status"] == "WAITING_FOR_MODEL_ACCEPTANCE"


def test_quota_observation_is_read_only_scoped_and_keeps_original_timestamp(
    tmp_path, iris_environment
):
    env, _ = local_case(iris_environment)
    agent = Path(env.root) / "iris_agent.py"
    agent.write_text("""
def load_config(): return {"token":"inert-fixture-value","channel":"fixture"}
def api(token,method,**params):
    assert method=="files.list"
    files=[{"name":"iris-99999999-queued.txt","created":100,"id":"unrelated"}] if params["page"]==1 else [{"name":"iris-1234abcd-queued.txt","created":200,"id":"own"}]
    return {"files":files,"paging":{"pages":2}}
def download(cfg,file):
    assert file["id"]=="own"
    return "Queued toy, today 4/100."
""")
    env = env.model_copy(update={"agent_script_sha256": file_digest(agent)})
    result = LocalIrisTransport(env).quota(["1234abcd"], tmp_path / "quota")
    assert result["status"] == "QUOTA_OBSERVED"
    observed = result["snapshot"]
    assert observed["used"] == 4 and observed["limit"] == 100
    assert observed["ack_created_at"].startswith("1970-01-01T00:03:20")
    assert not observed["authoritative_current"]
    assert "inert-fixture-value" not in json.dumps(result)


def test_large_attachments_stream_to_disk_and_resume(tmp_path, monkeypatch):
    """Recorded failure: a 772 MB raw CSV was re-downloaded on every collect and killed."""
    import io
    import types
    import urllib.request

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "jarvis"))
    import iris_worker

    payload = b"x" * (20 * 1024 * 1024 + 7)
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(payload)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    iris = types.SimpleNamespace(
        QUEUED="queued.txt",
        JOB="job.json",
        DONE="done.txt",
        HTTP_TIMEOUT=60,
        result_dir=lambda cfg, rid, found, out: out / "hermes" / "branch" / f"{rid}_abc1234",
        download=lambda cfg, file: "✅ Build passed",
    )
    found = {
        "done.txt": {"url_private": "https://files.example/u-done"},
        "job.json": {"url_private": "https://files.example/u-job"},
        "sim.hermes_trades_abc1234.csv": {
            "url_private": "https://files.example/u-raw",
            "size": len(payload),
        },
    }
    folder = iris_worker.save_attachments(iris, {"token": "t"}, "deadbeef", found, tmp_path)
    raw = folder / "sim.hermes_trades_abc1234.csv"
    assert raw.stat().st_size == len(payload) and (folder / "result.txt").exists()
    assert not list(folder.glob("*.part")) and calls == ["https://files.example/u-raw"]
    # A later collect of the same request does not download the complete file again.
    iris_worker.save_attachments(iris, {"token": "t"}, "deadbeef", found, tmp_path)
    assert calls == ["https://files.example/u-raw"]
    # A truncated transfer is never accepted as the raw output.
    found["sim.hermes_trades_abc1234.csv"]["size"] = len(payload) + 1
    with pytest.raises(ValueError, match="incomplete"):
        iris_worker.save_attachments(iris, {"token": "t"}, "deadbeef", found, tmp_path)
    assert raw.stat().st_size == len(payload)


def test_oversized_raw_output_fails_instead_of_waiting(tmp_path, iris_environment):
    env, _ = local_case(iris_environment)
    root = Path(env.root)
    agent = root / "iris_agent.py"
    agent.write_text("""
import json
QUEUED,REJECTED,JOB,DONE="queued.txt","rejected.txt","job.json","done.txt"
def load_config(): return {"name":"fixture"}
def answers(cfg, rid, unused): return {QUEUED:0,JOB:1,DONE:2}
def download(cfg, ref):
    return ["ack",json.dumps({"strategy":"hermes","branch":"toy","period":["2026/01/05","2026/01/06"],"sha":"abc1234"}),
            "Build passed (2 days, 0 failed)\\nTrades are 5.6 GB, over Slack's 1 GB file limit, so they were not uploaded."][ref]
def exit_code(reply): return 0
def result_dir(cfg,rid,found,directory): return directory
""")
    env = env.model_copy(update={"agent_script_sha256": file_digest(agent)})
    request = {
        "request_id": "1234abcd",
        "candidate_path": env.template,
        "candidate_sha256": env.template_sha256,
        "period": env.period.request_period,
    }
    value = LocalIrisTransport(env).collect(request, tmp_path / "worker")
    assert value["status"] == "FAILED" and value["reason"].startswith("RAW_OUTPUT_TOO_LARGE")
