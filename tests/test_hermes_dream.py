import json
from datetime import UTC, datetime, timedelta

import pytest
from test_iris_queue import iris_environment as iris_environment
from test_iris_queue import trial

from jarvis.contracts import Cost, IntegrityError, JarvisError
from jarvis.hermes_dream import (
    HermesDream,
    HermesDreamSpec,
    fixture_candidates,
    verify_hermes_dream,
)
from jarvis.hermes_policy import (
    HermesWorldPlan,
    q0,
    replay_hermes_world,
    seal_world,
)
from jarvis.identity import digest
from jarvis.iris_contracts import IrisAuthorization, IrisExperiment, IrisQueueSpec, IrisSettings
from jarvis.iris_fixture import FixtureIrisTransport
from jarvis.research_program import ResearchProgram, ResearchProgramSpec, program_report
from jarvis.research_queue import IrisResearchQueue
from jarvis.research_roles import RoleCaller, verify_roles
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


def plan(env, family, index=0, exposure="development_exposed"):
    env = env.model_copy(
        update={
            "model_lineage": family + f"-{index}",
            "period": env.period.model_copy(
                update={"group_id": family + f"-{index}", "exposure": exposure}
            ),
        }
    )
    auth = IrisAuthorization(
        environment_id=digest(env),
        max_submissions=4,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
        owner_note="Artificial fixture only",
    )
    trials = (
        trial(),
        IrisExperiment(
            label="entry",
            family="entry",
            settings=IrisSettings(trading_cutoff_delta=0.0001),
            hypothesis="Toy entry hypothesis",
            falsification="Objective does not improve",
            origin="declared_fixture",
        ),
        trial("exit30", take_profit_bps=30),
        IrisExperiment(
            label="heuristic",
            family="heuristic",
            settings=IrisSettings(heuristic_cancel_bps=60),
            hypothesis="Toy alternative",
            falsification="Objective does not improve",
            origin="declared_fixture",
        ),
    )
    return HermesWorldPlan(
        queue=IrisQueueSpec(
            goal="Maximize artificial entry-only PL", environment=env, authorization=auth
        ),
        trials=trials,
        family_id=family,
    )


class Backends:
    def __init__(self):
        self.values = {}

    def __call__(self, env):
        key = digest(env)
        if key not in self.values:
            self.values[key] = FixtureIrisTransport(
                env, shift=env.model_lineage.startswith("shifted")
            )
        return self.values[key]


def collect(store, backends, world, *, limit=4):
    queue = IrisResearchQueue(store, backends)
    qid = queue.create(world.queue)
    for t in world.trials:
        queue.enqueue(qid, t)
    for _ in range(limit * 2):
        queue.tick(qid)
    return qid, seal_world(store, qid, world.family_id)


def test_exact_hermes_replay_unknown_branch_and_seal(tmp_path, iris_environment, monkeypatch):
    store = Store(tmp_path / "evidence")
    backends = Backends()
    p = plan(iris_environment, "quadratic")
    qid, ref = collect(store, backends, p, limit=2)  # baseline+entry only; exit is unobserved.
    q = fixture_candidates()[0]
    assert replay_hermes_world(store, ref, q, 1)["status"] == "INSUFFICIENT_COVERAGE"
    assert replay_hermes_world(store, ref, q0(), 1)["status"] == "COMPLETE"
    assert seal_world(store, qid, p.family_id) == ref
    for b in backends.values.values():
        monkeypatch.setattr(b, "submit", lambda *_: pytest.fail("replay submitted"))
        monkeypatch.setattr(b, "collect", lambda *_: pytest.fail("replay polled"))
    assert replay_hermes_world(store, ref, q0(), 1)["external_calls"] == 0


def test_complete_hermes_dream_fresh_canary_and_rollback(tmp_path, iris_environment, monkeypatch):
    store = Store(tmp_path / "evidence")
    backends = Backends()
    dev = tuple(
        collect(store, backends, plan(iris_environment, f, i))[1]
        for f in ["quadratic", "piecewise", "saturating"]
        for i in range(2)
    )
    held = tuple(
        collect(store, backends, plan(iris_environment, f, i, "sealed_unseen"))[1]
        for f in ["cubic", "logarithmic", "hinge"]
        for i in range(2)
    )
    engine = HermesDream(store, transport_factory=backends)
    sid = engine.create(
        HermesDreamSpec(
            development=dev,
            heldout=held,
            fresh=(plan(iris_environment, "exponential", exposure="sealed_unseen"),),
            canary=(plan(iris_environment, "shifted", exposure="sealed_unseen"),),
            developer="fixture",
            experiments_per_world=1,
        )
    )
    for _ in range(30):
        value = engine.advance(sid)
        if value.get("kind") == "HermesDreamReport":
            break
    assert value["status"] == "ROLLED_BACK"
    assert value["active_policy"] == q0().model_dump(mode="json")
    events = store.events(sid)
    assert sum(e["event_type"] == "HERMES_FINALIST_FROZEN" for e in events) == 1
    assert any(e["event_type"] == "HERMES_POLICY_ACTIVATED" for e in events)
    assert any(e["event_type"] == "HERMES_POLICY_ROLLED_BACK" for e in events)
    for b in backends.values.values():
        monkeypatch.setattr(b, "submit", lambda *_: pytest.fail("audit submitted"))
        monkeypatch.setattr(b, "collect", lambda *_: pytest.fail("audit polled"))
    assert verify_hermes_dream(store, sid)["external_calls"] == 0
    assert engine.advance(sid) == value
    duplicate = engine.create(
        HermesDreamSpec(development=dev, heldout=held, developer="fixture", experiments_per_world=1)
    )
    engine.advance(duplicate)
    assert engine.advance(duplicate)["status"] == "INCONCLUSIVE_HELDOUT_ALREADY_EXPOSED"


def test_exposed_results_cannot_be_heldout_and_small_pool_cannot_promote(
    tmp_path, iris_environment
):
    store = Store(tmp_path / "evidence")
    backends = Backends()
    _, ref = collect(store, backends, plan(iris_environment, "quadratic"))
    engine = HermesDream(store, transport_factory=backends)
    with pytest.raises((IntegrityError, ValueError)):
        engine.create(HermesDreamSpec(development=(ref,), heldout=(ref,), developer="fixture"))
    sid = engine.create(HermesDreamSpec(development=(ref,), developer="fixture"))
    assert engine.advance(sid)["status"] == "NEEDS_MORE_INDEPENDENT_HISTORIES"
    assert verify_hermes_dream(store, sid)["status"] == "VERIFIED"


class DirectorFixture:
    def __init__(self):
        self.calls = []

    def identity(self):
        return {"provider": "fixture-director-not-real-LLM"}

    def complete(self, prompt, schema, directory):
        context = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        self.calls.append(context)
        experiments = []
        if len(self.calls) == 1:
            for label, kwargs in [
                ("exit30", {"take_profit_bps": 30}),
                ("heuristic", {"heuristic_cancel_bps": 60}),
            ]:
                t = trial(label, **kwargs).model_copy(
                    update={
                        "origin": "agent_proposed",
                        "evidence_refs": tuple(context["visible_evidence_ids"]),
                    }
                )
                experiments.append(t.model_dump(mode="json"))
        else:
            experiments.append(
                IrisExperiment(
                    label="entry",
                    family="entry",
                    settings=IrisSettings(trading_cutoff_delta=0.0001),
                    hypothesis="Distinct toy hypothesis after results",
                    falsification="No measured gain",
                    evidence_refs=tuple(context["visible_evidence_ids"]),
                    origin="agent_proposed",
                ).model_dump(mode="json")
            )
        return {
            "answer": {
                "kind": "ResearchDirections",
                "schema_version": 1,
                "context_id": context["context_id"],
                "analysis_summary": "Respond to measured evidence; risk remains visible",
                "experiments": experiments,
                "capability_requests": [],
                "stop_reason": None,
            },
            "error": None,
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "cost": Cost(llm_tokens=120, wall_seconds=0.01).model_dump(),
        }


def test_concurrent_director_epochs_reuse_baseline_and_share_submission_grant(
    tmp_path, iris_environment, monkeypatch
):
    store = Store(tmp_path / "program")
    backends = Backends()
    provider = DirectorFixture()
    p = plan(iris_environment, "quadratic")
    program = ResearchProgram(
        store, provider_factory=lambda _: provider, transport_factory=backends
    )
    pid = program.create(
        ResearchProgramSpec(
            queue=p.queue,
            directions=("Maximize PL and investigate exit/entry tradeoffs",),
            max_direction_batches=2,
        )
    )
    for _ in range(25):
        value = program.advance(pid)
        if value["status"] == "CLOSED":
            break
    assert value["status"] == "CLOSED"
    report = program_report(store, pid)
    assert len(report["world_refs"]) == 2
    assert all(
        replay_hermes_world(store, ref, q0(), 3)["status"] == "COMPLETE"
        for ref in report["world_refs"]
    )
    assert len(provider.calls) == 2
    assert sum(len(b.submissions) for b in backends.values.values()) == 4
    assert report["best_development_candidate"]["result"]["entry_only_pl"] == 5
    assert all(r["external_calls"] == 0 for r in report["verification"])
    # The second director sees the first candidate, while another is still pending.
    assert len(provider.calls[1]["observations"]) == 2
    monkeypatch.setattr(
        provider, "complete", lambda *_: pytest.fail("completed program called provider")
    )
    assert program.advance(pid)["status"] == "CLOSED"
    assert verify_roles(store, pid)["calls"] == 2


def test_role_receipt_recovery_and_unknown_usage(tmp_path):
    from jarvis.contracts import Budget

    store = Store(tmp_path / "roles")
    provider = DirectorFixture()
    owner = store.create(
        {"world_id": "toy-world", "task": {"budget": Budget(max_llm_tokens=10000).model_dump()}}
    )
    caller = RoleCaller(store, provider, AgentConfig(reserve_tokens=1000, max_calls=2))
    context = {"visible_evidence_ids": []}
    with pytest.raises(JarvisError, match="receipt interruption"):
        caller.call(owner, "direction", context, {"type": "object"}, fault="after_receipt")
    assert caller.call(owner, "direction", context, {"type": "object"})["cost"]["llm_tokens"] == 120
    assert len(provider.calls) == 1
    assert verify_roles(store, owner)["calls"] == 1
