from typing import Any

import pytest

from jarvis.autonomy_dream import (
    AutonomyDream,
    AutonomyDreamSpec,
    DreamTask,
    PolicyRegistry,
    dream_report,
)
from jarvis.autonomy_policy import ResearchPolicy, q0
from jarvis.contracts import IntegrityError
from jarvis.quant_fixture import QuantFixtureProvider
from jarvis.quant_world import QuantWorldFactory, analysis_environment
from jarvis.storage import Store
from jarvis.workbench import Workbench


@pytest.fixture(autouse=True)
def isolation(tmp_path):
    if Workbench(analysis_environment()).probe(tmp_path / "probe")["status"] != "PASS":
        pytest.skip("OS workbench isolation unavailable")


def spec(registry: str, **overrides: Any) -> AutonomyDreamSpec:
    return AutonomyDreamSpec(
        registry=registry,
        development=(DreamTask(task_id="dev1", family="balanced", seed=11),),
        validation=(
            DreamTask(task_id="val1", family="balanced", seed=21),
            DreamTask(task_id="val2", family="calm", seed=22),
        ),
        canary=(DreamTask(task_id="can1", family="volatile", seed=31),),
        session_max_decisions=30,
        **overrides,
    )


def provider(cfg, role):
    return QuantFixtureProvider(role)


def test_cohorts_must_be_disjoint():
    with pytest.raises(ValueError, match="disjoint"):
        AutonomyDreamSpec(
            registry="r",
            development=(DreamTask(task_id="a", family="balanced", seed=1),),
            validation=(DreamTask(task_id="b", family="balanced", seed=1),),
            canary=(DreamTask(task_id="c", family="calm", seed=2),),
        )


def test_dream_round_adopts_a_validated_research_policy(tmp_path):
    store = Store(tmp_path / "store")
    registry = PolicyRegistry(store).create()
    incumbent = PolicyRegistry(store).active(registry)
    dream = AutonomyDream(store, QuantWorldFactory(per_day=40), provider)
    round_id = dream.create(spec(registry))
    outcome = dream.run(round_id)
    assert outcome["outcome"] == "ADOPTED"
    assert PolicyRegistry(store).active(registry) == outcome["policy_ref"] != incumbent
    report = dream_report(store, round_id)
    # Developer saw development histories only; screen never estimated outcomes.
    assert {s["cohort"] for s in report["sessions"]} == {"development", "validation", "canary"}
    assert report["screen"]["external_calls"] == 0
    assert all(
        s["replay"] in {"COMPLETE", "INSUFFICIENT_COVERAGE"}
        for c in report["screen"]["candidates"]
        for s in c["sessions"]
    )
    chosen = next(
        e for e in report["validation"]["evaluations"] if e["policy_ref"] == outcome["policy_ref"]
    )
    assert chosen["passes"] and chosen["mean_gain"] > 0
    assert not report["canary"]["regressed"]
    # Re-running a closed round is idempotent; a new round must start from the new incumbent.
    assert dream.run(round_id) == {**outcome}
    adopted = ResearchPolicy.model_validate(store.artifacts.get(outcome["policy_ref"]))
    assert adopted.origin == "agent_proposed"
    stale = dream.create(spec(registry))
    PolicyRegistry(store).rollback(registry, incumbent, outcome["policy_ref"])
    with pytest.raises(IntegrityError, match="registry changed"):
        dream.run(stale)


class DegradedCanary(QuantWorldFactory):
    """Engineering control: the canary world's holdout punishes structural candidates."""

    def build(self, store, task, root):
        built = super().build(store, task, root)
        if task.task_id.startswith("can"):
            honest = built["holdout"]

            def holdout(candidate_ref, settings):
                value = dict(honest(candidate_ref, settings))
                if store.artifacts.get(candidate_ref)["origin"] != "owner_baseline":
                    value["pl"] = value["pl"] * 0.5
                return value

            built["holdout"] = holdout
        return built


def test_canary_regression_rolls_the_registry_back(tmp_path):
    store = Store(tmp_path / "store")
    registry = PolicyRegistry(store).create()
    incumbent = PolicyRegistry(store).active(registry)
    dream = AutonomyDream(store, DegradedCanary(per_day=40), provider)
    outcome = dream.run(dream.create(spec(registry)))
    assert outcome["outcome"] == "ROLLED_BACK"
    history = PolicyRegistry(store).history(registry)
    assert [h["event"] for h in history] == [
        "POLICY_ACTIVATED",
        "POLICY_ACTIVATED",
        "POLICY_ROLLED_BACK",
    ]
    assert PolicyRegistry(store).active(registry) == incumbent


class RulesOnlyCopy(QuantFixtureProvider):
    def __init__(self, role, guidance):
        super().__init__(role)
        self.guidance = guidance

    def developer(self, c):
        value = q0().model_dump(mode="json")
        return {
            "context_id": c["context_id"],
            "analysis": "a",
            "candidates": [
                {
                    **value,
                    "name": "copy",
                    "origin": "agent_proposed",
                    "rationale": "r",
                    "guidance": self.guidance,
                }
            ],
            "evidence_refs": c["visible_evidence_ids"][:1],
        }


def rounds(store):
    return [
        r[0]
        for r in store.connect().execute("select id from campaigns")
        if store.manifest(r[0])["kind"] == "AutonomyDreamRound"
    ]


def test_guidance_only_policy_needs_fresh_validation_and_is_not_adopted_without_gain(tmp_path):
    store = Store(tmp_path / "store")
    registry = PolicyRegistry(store).create()
    dream = AutonomyDream(
        store, QuantWorldFactory(per_day=40), lambda cfg, role: RulesOnlyCopy(role, "think harder")
    )
    outcome = dream.run(dream.create(spec(registry)))
    assert outcome["outcome"] == "NOT_ADOPTED"
    report = dream_report(store, rounds(store)[0])
    screened = report["screen"]["candidates"][0]
    assert not screened["behaviour_changes"] and screened["guidance_changed"]
    assert report["validation"]["evaluations"][0]["mean_gain"] == 0


def test_identical_policy_proposal_is_rejected(tmp_path):
    store = Store(tmp_path / "store")
    registry = PolicyRegistry(store).create()
    dream = AutonomyDream(
        store, QuantWorldFactory(per_day=40), lambda cfg, role: RulesOnlyCopy(role, "")
    )
    round_id = dream.create(spec(registry))
    with pytest.raises(Exception, match="corrective attempts"):
        dream.run(round_id)
    reasons = [
        e["payload"]["reason"]
        for e in store.events(round_id)
        if e["event_type"] == "LAB_ROLE_REJECTED"
    ]
    assert reasons and all("differ from the incumbent" in r for r in reasons)
