from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis.contracts import AdmissionError, IntegrityError, JarvisError
from jarvis.identity import digest, file_digest
from jarvis.iris import render_candidate, setting_capabilities
from jarvis.iris_contracts import (
    IrisAuthorization,
    IrisEnvironment,
    IrisExperiment,
    IrisQueueSpec,
    IrisSettings,
)
from jarvis.iris_fixture import FixtureIrisTransport
from jarvis.research_queue import IrisResearchQueue, queue_tasks, verify_queue
from jarvis.storage import Store


@pytest.fixture
def iris_environment(tmp_path):
    root = tmp_path / "upstream"
    root.mkdir()
    template = root / "toy.txt"
    template.write_text(
        "// protected\n// @start:config\nconstexpr double toy_exit = 0.004;\nconstexpr double toy_entry = 0;\nconstexpr double toy_heuristic = 0.004;\n// @end:config\n// protected end\n"
    )
    agent = root / "iris_agent.py"
    evaluator = root / "evaluator.py"
    agent.write_text("# inert fixture\n")
    evaluator.write_text("# inert fixture\n")
    return IrisEnvironment(
        root=str(root),
        python="unused-fixture-runtime",
        branch="toy",
        template=str(template),
        template_sha256=file_digest(template),
        evaluator=str(evaluator),
        evaluator_sha256=file_digest(evaluator),
        agent_script_sha256=file_digest(agent),
        accepted_inputs={str(template): file_digest(template)},
        side="long",
        model_lineage="quadratic-fixture",
        result_origin="SIMULATED_FIXTURE",
        bindings=(
            {
                "setting": "take_profit_bps",
                "symbol": "toy_exit",
                "region": "config",
                "syntax": "double",
                "scale": 0.0001,
            },
            {
                "setting": "trading_cutoff_delta",
                "symbol": "toy_entry",
                "region": "config",
                "syntax": "double",
                "mode": "delta",
            },
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


def trial(label="baseline", **settings):
    family = "baseline" if not settings else "exit"
    if "heuristic_cancel_bps" in settings:
        family = "heuristic"
    return IrisExperiment(
        label=label,
        family=family,
        hypothesis="Artificial controlled hypothesis",
        falsification="Measured entry PL fails to improve",
        settings=IrisSettings(**settings),
        origin="declared_fixture",
    )


def setup_queue(tmp_path, env, *, authorized=True):
    transport = FixtureIrisTransport(env)
    store = Store(tmp_path / "evidence")
    queue = IrisResearchQueue(store, lambda _: transport)
    auth = (
        IrisAuthorization(
            environment_id=digest(env),
            max_submissions=4,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            owner_note="Synthetic fixture only",
        )
        if authorized
        else None
    )
    qid = queue.create(
        IrisQueueSpec(
            goal="Maximize artificial entry-only PL; retain risk",
            environment=env,
            authorization=auth,
        )
    )
    return store, queue, qid, transport


def test_region_only_candidate_and_capability_bounds(iris_environment):
    env = iris_environment
    text = render_candidate(env, IrisSettings(take_profit_bps=30))
    assert "toy_exit = 0.003" in text and text.endswith("// protected end\n")
    with pytest.raises(AdmissionError, match="unbound"):
        render_candidate(env, IrisSettings(stop_loss_bps=30))
    env = env.model_copy(
        update={"bindings": (env.bindings[0].model_copy(update={"minimum": 0.0035}),)}
    )
    with pytest.raises(AdmissionError, match="bounds"):
        render_candidate(env, IrisSettings(take_profit_bps=30))


def test_closed_literal_product_uses_source_units_and_preserves_baseline(iris_environment):
    path = Path(iris_environment.template)
    path.write_text(
        path.read_text().replace("toy_heuristic = 0.004", "toy_heuristic = 40 * 0.0001")
    )
    env = iris_environment.model_copy(update={"template_sha256": file_digest(path)})
    assert setting_capabilities(env)[2]["baseline_value"] == 40
    assert render_candidate(env, IrisSettings()) == path.read_text()
    assert "toy_heuristic = 0.005" in render_candidate(env, IrisSettings(heuristic_cancel_bps=50))
    for expression in ("40 * external_value", "40 * 0.0001 * 2", "function()", "40 + 1"):
        path.write_text(path.read_text().replace("40 * 0.0001", expression))
        changed = env.model_copy(update={"template_sha256": file_digest(path)})
        with pytest.raises(AdmissionError):
            render_candidate(changed, IrisSettings(heuristic_cancel_bps=50))
        path.write_text(path.read_text().replace(expression, "40 * 0.0001"))


@pytest.mark.parametrize("fault", ["after_reserve", "after_start", "after_receipt"])
def test_remote_crash_recovery_never_resends(tmp_path, iris_environment, fault):
    store, queue, qid, transport = setup_queue(tmp_path, iris_environment)
    queue.enqueue(qid, trial())
    if fault == "after_receipt":
        queue.tick(qid)
    with pytest.raises(JarvisError, match="injected"):
        queue.tick(qid, fault=fault)
    if fault == "after_start":
        transport.collect = lambda request, directory: {
            "status": "PENDING",
            "request_id": request["request_id"],
        }
    # after_start is ambiguous and must collect the reserved ID, never upload.
    for _ in range(3):
        queue.tick(qid)
    assert len(transport.submissions) == (0 if fault == "after_start" else 1)
    assert len(store.jobs(qid)) == 1
    assert verify_queue(store, qid)["completed"] == (0 if fault == "after_start" else 1)
    assert bool(any(store.resource_totals(qid)["pending"].values())) == (fault == "after_start")


def test_finite_queue_dedup_and_offline_audit(tmp_path, iris_environment, monkeypatch):
    store, queue, qid, transport = setup_queue(tmp_path, iris_environment)
    queue.enqueue(qid, trial())
    first = queue.enqueue(qid, trial("exit30", take_profit_bps=30))
    assert queue.enqueue(qid, trial("duplicate-label", take_profit_bps=30)) == first
    for _ in range(4):
        queue.tick(qid)
    assert len(transport.submissions) == 2
    assert queue_tasks(store, qid)[-1]["status"] == "SUCCEEDED"
    monkeypatch.setattr(transport, "submit", lambda *_: pytest.fail("audit submitted"))
    monkeypatch.setattr(transport, "collect", lambda *_: pytest.fail("audit collected"))
    assert verify_queue(store, qid)["external_calls"] == 0
    assert store.resource_totals(qid)["actual"]["tool_calls"] == 2


def test_no_authorization_prepares_without_network(tmp_path, iris_environment):
    store, queue, qid, transport = setup_queue(tmp_path, iris_environment, authorized=False)
    queue.enqueue(qid, trial())
    assert queue.tick(qid)["status"] == "AWAITING_SUBMISSION_AUTHORIZATION"
    assert not transport.submissions and not store.jobs(qid)


def test_wrong_result_and_upstream_drift_fail_closed(tmp_path, iris_environment):
    store, queue, qid, transport = setup_queue(tmp_path, iris_environment)
    queue.enqueue(qid, trial())
    queue.tick(qid)
    collect = transport.collect
    transport.collect = lambda *args: {**collect(*args), "entry_only_pl": 999}
    assert queue.tick(qid)["status"] == "INVALID_RESULT"
    assert store.status(qid) == "PAUSED"
    assert verify_queue(store, qid)["completed"] == 1
    from pathlib import Path

    Path(iris_environment.template).write_text("mutated")
    with pytest.raises(IntegrityError):
        queue.tick(qid)


def test_future_evidence_is_not_accepted(tmp_path, iris_environment):
    store, queue, qid, transport = setup_queue(tmp_path, iris_environment)
    with pytest.raises(AdmissionError, match="future"):
        queue.enqueue(qid, trial().model_copy(update={"evidence_refs": ("unseen",)}))


def test_operator_grant_is_immutable_shared_and_baseline_import_recovers(
    tmp_path, iris_environment, monkeypatch
):
    store, queue, qid, transport = setup_queue(tmp_path, iris_environment, authorized=False)
    queue.enqueue(qid, trial())
    queue.enqueue(qid, trial("exit30", take_profit_bps=30))
    grant = IrisAuthorization(
        environment_id=digest(iris_environment),
        max_submissions=2,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        owner_note="Explicit bounded artificial permission",
    )
    assert queue.tick(qid)["status"] == "AWAITING_SUBMISSION_AUTHORIZATION"
    queue.authorize(qid, grant)
    queue.authorize(qid, grant)
    with pytest.raises(AdmissionError, match="immutable"):
        queue.authorize(qid, grant.model_copy(update={"max_submissions": 3}))
    for _ in range(4):
        queue.tick(qid)
    spec = IrisQueueSpec.model_validate(store.manifest(qid)["spec"]).model_copy(
        update={"prior_queues": (qid,)}
    )
    child = queue.create(spec)
    queue.enqueue(child, trial())
    queue.enqueue(child, trial("exit20", take_profit_bps=20))
    finish = queue._finish_import
    monkeypatch.setattr(
        queue,
        "_finish_import",
        lambda *_: (_ for _ in ()).throw(JarvisError("import interruption")),
    )
    with pytest.raises(JarvisError, match="import interruption"):
        queue.adopt_baseline(child, qid)
    monkeypatch.setattr(queue, "_finish_import", finish)
    assert queue.tick(child)["status"] == "ADOPTED"
    assert queue.tick(child)["status"] == "SUBMISSION_LIMIT"
    assert len(transport.submissions) == 2
    assert verify_queue(store, child)["submissions"] == 0
