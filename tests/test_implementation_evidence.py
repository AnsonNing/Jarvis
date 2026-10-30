"""Synthetic regression: analysis definitions must reach the implementing worker."""

import json

import pytest

from jarvis.autonomous_research import AutonomousResearch, table_record
from jarvis.autonomy_contracts import AutonomySessionSpec, ImplementAction
from jarvis.planning_fixture import PlanningFixtureProvider, declared_world
from jarvis.quant_fixture import QuantFixtureProvider
from jarvis.quant_world import analysis_environment, build_world, world_executors
from jarvis.storage import Store
from jarvis.workbench import Workbench


class EvidenceWorker(QuantFixtureProvider):
    def __init__(self, *, noop=False):
        super().__init__()
        self.noop = noop
        self.seen = []

    def worker(self, c):
        task = c["task"]
        self.seen = task["inputs"]
        base = dict(
            context_id=c["context_id"],
            note="Synthetic evidence plumbing",
            path=None,
            content=None,
            arguments=[],
            summary=None,
            findings=[],
            output_files=[],
            final_script=None,
        )
        if not c["transcript"] and not self.noop:
            selected = next(
                x["path"] for x in task["inputs"] if "canonical_definition-" in x["path"]
            )
            assert len(task["inputs"]) == 2  # the cited result and its one visible registered table
            return dict(
                base,
                tool="run_python",
                path="work/build.py",
                content=f"""
import json
from pathlib import Path
value = json.loads(Path({selected!r}).read_text())['multiplier']
assert value == 1.5
Path('work/cancel_rule.py').write_text('def should_cancel(order, settings):\\n    return order["adverse_move_bp"] > settings["heuristic_cancel_bps"] * ' + str(value) + '\\n')
""",
            )
        return dict(
            base,
            tool="finish",
            summary="No edit: required definition unavailable"
            if self.noop
            else "Rule uses the cited definition",
            findings=[
                dict(statement="Scope", value="SIMULATED_FIXTURE; no measured financial gain")
            ],
            output_files=["work/cancel_rule.py"],
        )


@pytest.mark.parametrize("noop", [False, True])
def test_cited_analysis_tables_reach_worker_and_noop_explains_rejection(
    tmp_path, monkeypatch, noop
):
    if Workbench(analysis_environment()).probe(tmp_path / "probe")["status"] != "PASS":
        pytest.skip("OS workbench isolation unavailable")
    store = Store(tmp_path / "store")
    world = build_world(store, tmp_path / "world", per_day=5)
    provider = EvidenceWorker(noop=noop)
    engine = AutonomousResearch(store, world_executors(world), lambda cfg, role: provider)
    owner = engine.create(AutonomySessionSpec(contract_ref=world["contract_ref"]))

    def table(name, multiplier):
        file = tmp_path / (name + ".json")
        file.write_text(json.dumps({"multiplier": multiplier}))
        return table_record(
            store,
            file,
            name=name,
            description="Synthetic formula",
            lineage={"origin": "declared_fixture"},
            exposure="synthetic",
        )

    canonical = table("canonical_definition", 1.5)
    hidden = table("hidden_definition", 900)
    unregistered = table("unregistered_definition", 1500)
    result = store.artifacts.put(
        {"kind": "AnalysisResult", "output_tables": [canonical, hidden, unregistered]}
    )
    store.append(
        owner,
        "ARTIFACTS_REGISTERED",
        dict(
            index=-1,
            tables=[canonical, hidden],
            observations=[],
            candidates=[],
            models=[],
            patches=[],
        ),
    )
    store.append(
        owner,
        "ACTION_COMPLETED",
        dict(index=-1, kind="ANALYZE", branch_id="fixture", result_ref=result),
    )
    # Frozen decision visibility excludes both the later/hidden table and unregistered bytes.
    context = store.artifacts.put({"visible_evidence_ids": [result, canonical]})
    monkeypatch.setattr(engine, "_decision", lambda state, index: {"context_id": context})
    child = store.create(
        dict(
            kind="SyntheticImplementationAction",
            world_id="synthetic-only",
            budget_owner=owner,
            task={"budget": engine.spec(owner).budget.model_dump()},
        )
    )
    baseline = store.artifacts.get(world["contract_ref"])["baseline_candidate_ref"]
    action = ImplementAction(
        label="evidence_rule",
        target="cancel_rule",
        base_candidate_ref=baseline,
        requirement="Use the canonical definition exactly",
        branch_id="fixture",
        question_id="fixture",
        hypothesis_ids=("fixture",),
        evidence_refs=(result,),
        rationale="Synthetic regression",
        expected_observation="A rule or an actionable limitation",
    )
    try:
        value, registered = engine._implement(owner, child, 0, action)
        assert {x["ref"] for x in provider.seen} == {result, canonical}
        assert set(value["input_refs"]) == {result, canonical}
        from jarvis.autonomy_evidence import build_review_bundle

        value_ref = store.artifacts.put(value)
        bundle = build_review_bundle(
            store,
            owner,
            0,
            value_ref,
            action.model_dump(mode="json"),
            [value_ref, result, canonical, baseline],
            child,
        )
        assert [t["table_ref"] for t in bundle["tables"]] == [canonical]
        if noop:
            assert value["status"] == "REJECTED"
            assert value["rejection_reasons"] == ["NO_CHANGED_TARGET_FILES"]
            assert "definition unavailable" in value["summary"]
            assert value["findings"] and not registered
        else:
            assert value["status"] == "ACCEPTED" and registered["candidates"]
            patch = store.artifacts.get(value["patch_ref"])
            assert b"* 1.5" in store.blobs.get(patch["files"]["cancel_rule.py"])
    finally:
        engine.runner.shutdown()


def test_planning_repair_names_the_exact_declared_hypothesis(tmp_path):
    from jarvis.autonomy_contracts import AutonomyDecision
    from jarvis.identity import digest

    store = Store(tmp_path / "store")
    world = declared_world(store, {})
    engine = AutonomousResearch(
        store, world["executors"], lambda cfg, role: PlanningFixtureProvider(role)
    )
    owner = engine.create(AutonomySessionSpec(contract_ref=world["contract_ref"]))
    context = engine.director_context(owner, engine.state(owner))
    raw = PlanningFixtureProvider().director({**context, "context_id": digest(context)})
    expected = raw["question"]["hypotheses"][0]
    raw["planning_review"]["directions"][0]["hypothesis"]["prediction"] = "A different prediction"
    try:
        with pytest.raises(ValueError, match="copy the declared") as error:
            engine.validate_decision(owner, AutonomyDecision.model_validate(raw), context)
        assert expected["hypothesis_id"] in str(error.value)
        assert expected["prediction"] in str(error.value)
        assert (
            store.artifacts.get(world["contract_ref"])["baseline_candidate_ref"]
            in context["analyzable_evidence_ids"]
        )
    finally:
        engine.runner.shutdown()
