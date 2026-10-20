"""Explicit scripted engineering control; no real LLM, Hermes or Iris work."""

import inspect
import json
from pathlib import Path
from typing import Any

from jarvis.contracts import Cost
from jarvis.critic_fixture import CriticFixture, ReviewedDirectorFixture
from jarvis.identity import digest, file_digest
from jarvis.lab_fixture import fixture_lab_spec
from jarvis.mechanism_research import (
    MechanismResearch,
    MechanismResearchSpec,
    replay_workflow,
    workflow_report,
)
from jarvis.research_acceptance import ResearchAcceptance
from jarvis.research_contract import ResearchContract
from jarvis.research_fixture import FixtureBackends
from jarvis.research_lab import payloads
from jarvis.research_program import ResearchProgram
from jarvis.research_skills import SkillRegistry, diagnostic_case
from jarvis.researcher import AgentConfig
from jarvis.skill_builder import git
from jarvis.storage import Store
from jarvis.trading_diagnostics import attribution, inventory


def fixture_skill_source() -> str:
    """Declared known implementation exercises plumbing, not autonomous invention."""
    return (
        "from __future__ import annotations\nfrom decimal import Decimal\nfrom types import SimpleNamespace\n"
        "class DiagnosticResult(dict):\n"
        "    def __init__(self, **values):\n"
        "        super().__init__(schema_version=1, kind='DiagnosticResult', **values)\n"
        "    def __getattr__(self, name):\n        return self[name]\n"
        + inspect.getsource(inventory)
        + "\n"
        + inspect.getsource(attribution)
        + "\ndef run(payload):\n"
        "    values = {}\n"
        "    for side in ('baseline', 'candidate'):\n"
        "        raw = dict(payload[side])\n"
        "        raw['rows'] = tuple(SimpleNamespace(**{**r, 'pl': Decimal(str(r['pl'])), 'path': tuple(r.get('path', ())), 'score': r.get('score')}) for r in raw['rows'])\n"
        "        values[side] = SimpleNamespace(**raw)\n"
        "    return attribution(SimpleNamespace(**values))\n"
    )


class WorkflowFixtureProvider:
    def identity(self) -> dict[str, str]:
        return {
            "provider": "mechanism-research-fixture",
            "implementation": file_digest(Path(__file__)),
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        c = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        refs = c["visible_evidence_ids"]
        if schema["title"] == "BuilderSource":
            answer = {
                "context_id": c["context_id"],
                "implementation": fixture_skill_source(),
                "self_test": "from implementation import run\ndef self_test():\n    d={'entity_kind':'fill','stable_keyspace':'fixture','rows':[]}\n    r=run({'baseline':d,'candidate':d})\n    assert r['facts']['total_pl_delta']=='0'\n",
                "card": "Declared fixture candidate. Strict matching and PL reconciliation; no causal or utility claim.",
                "evidence_refs": refs[:1],
            }
        elif schema["title"] == "ActionReview":
            facts = c["result"]["value"].get("result", {}).get("facts", {})
            assessments = []
            if "shared_pl_delta" in facts:
                assessments = [
                    {
                        "hypothesis_id": "h-composition",
                        "verdict": "refuted",
                        "evidence_refs": [c["result_ref"]],
                        "explanation": "Shared-record PL also changed; a pure selection-only explanation fails in these artificial records.",
                    }
                ]
            answer = {
                "context_id": c["context_id"],
                "summary": "Independent fixture review of the actual recorded action output.",
                "evidence_refs": [c["result_ref"]],
                "assessments": assessments,
                "limitations": [
                    "Artificial engineering evidence; matching is not causal mediation or research-policy utility."
                ],
            }
        elif schema["title"] == "ExpertOpinion":
            answer = {
                "context_id": c["context_id"],
                "hypothesis": "Test the changed common-record path separately.",
                "counterargument": "A descriptive match cannot prove the mechanism.",
                "test": "Freeze an explicit controlled follow-up.",
                "evidence_refs": refs[-1:],
                "question_for": None,
                "question": None,
            }
        else:
            history = c["history"]
            kinds = [h["observation"]["action"] for h in history]
            skilled = any(s["manifest"]["name"] == "trade_attribution" for s in c["skills"])
            question = (
                None
                if c["questions"]
                else {
                    "question_id": "q-composition",
                    "parent_id": None,
                    "question": "Does the observed PL difference arise only from selected-record composition?",
                    "hypotheses": [
                        {
                            "hypothesis_id": "h-composition",
                            "claim": "Only selected-record composition changed.",
                            "prediction": "Shared-record PL delta is zero.",
                            "falsification": "A nonzero shared-record delta refutes the claim.",
                            "evidence_refs": refs[:1],
                        },
                        {
                            "hypothesis_id": "h-path",
                            "claim": "Common records also contribute to the observed change.",
                            "prediction": "Shared PL or recorded execution paths differ.",
                            "falsification": "All common records are unchanged.",
                            "evidence_refs": refs[:1],
                        },
                    ],
                }
            )
            if not history and not skilled:
                body = {"kind": "INSPECT", "input_refs": c["dataset_refs"]}
            elif "DIAGNOSE" not in kinds:
                body = {
                    "kind": "DIAGNOSE",
                    "skill_name": "trade_attribution",
                    "baseline_ref": c["dataset_refs"][0],
                    "candidate_ref": c["dataset_refs"][1],
                }
            elif not skilled and "BUILD_SKILL" not in kinds:
                body = {
                    "kind": "BUILD_SKILL",
                    "skill_name": "trade_attribution",
                    "protocol": "paired-attribution-v1",
                    "requirement": "Strict identity matching and exact Decimal PL reconciliation including unmatched residuals.",
                }
            elif not skilled:
                body = {"kind": "STOP", "scope": "session", "conclusion": "needs_successor"}
            elif c["experiment_available"] and "EXPERIMENT" not in kinds:
                body = {
                    "kind": "EXPERIMENT",
                    "direction": "Test a controlled alternative after rejecting the composition-only explanation; keep the fixed evaluator and risks.",
                }
            elif "CONSULT" not in kinds:
                body = {
                    "kind": "CONSULT",
                    "specialist": "mechanism",
                    "question": "Which follow-up distinguishes common-record changes from pure sample selection?",
                }
            else:
                body = {"kind": "STOP", "scope": "session", "conclusion": "research_complete"}
            answer = {
                "context_id": c["context_id"],
                "question": question,
                "action": {
                    "question_id": "q-composition",
                    "hypothesis_ids": ["h-composition", "h-path"],
                    "rationale": "Choose the next action using the actual prior observations in this declared scripted control.",
                    "expected_observation": "A diagnostic result distinguishes the competing explanations before another backtest.",
                    "evidence_refs": refs[-1:],
                    **body,
                },
            }
        return {
            "answer": answer,
            "cost": Cost(llm_tokens=100).model_dump(mode="json"),
            "usage": {"origin": "SIMULATED_TOKEN_UNITS"},
            "error": None,
        }


def fixture_workflow(
    store: Store, *, include_experiment: bool = True
) -> tuple[MechanismResearch, str]:
    registry = SkillRegistry.create(store)
    ref = registry.define_builtin("inventory", "inventory-v1")
    registry.register(ref, registry.validate(registry.registry, ref))
    base = fixture_lab_spec(store)
    config = AgentConfig(
        model="gpt-6.1-sol", reasoning_effort="high", max_calls=32, reserve_tokens=65536
    )
    acceptance = ResearchAcceptance(
        pr_max_decline_fraction=0, mdd_max_increase_fraction=0, require_positive_pl_gain=True
    )
    assert base.program.execution_budget is not None
    program = base.program.model_copy(
        update={
            "agent": config,
            "critic": config,
            "max_direction_batches": 1,
            "experiments_per_batch": 2,
            "acceptance": acceptance,
            "execution_budget": base.program.execution_budget.model_copy(
                update={"max_llm_tokens": 1000000}
            ),
        }
    )
    example = diagnostic_case()
    snapshots = [
        s.model_copy(update={"evaluator_id": program.queue.environment.evaluator_sha256})
        for s in (example.baseline, example.candidate)
    ]
    baseline_ref = store.artifacts.put(
        {
            "kind": "DeclaredFixtureBaseline",
            "origin": "declared_fixture",
            "description": "Artificial PL-change question; no real strategy or market data",
        }
    )
    contract = ResearchContract(
        task_id="mechanism-fixture",
        instruction="Explain the mechanism before optimizing thresholds. Build a missing diagnostic when useful, then use it in a new session.",
        objective="Explain observed PL differences and design discriminating tests",
        focus="Composition versus changed common-record outcomes",
        baseline_refs=(baseline_ref,),
        evaluator_id=program.queue.environment.evaluator_sha256,
        dataset_id=example.baseline.dataset_id,
        exposure="synthetic",
        candidate_freedom=("inspect", "diagnose", "build", "controlled experiments"),
        acceptance=acceptance.model_dump(exclude={"schema_version"}),
        constraints=("Frozen evaluator and data identity",),
        origin="declared_fixture",
        allow_skill_building=True,
        experiment_environment_id=digest(program.queue.environment),
    )
    repository = store.root / "fixture-builder-repository"
    repository.mkdir(parents=True, exist_ok=True)
    if not (repository / ".git").exists():
        git(repository, "init")
        (repository / "README.md").write_text(
            "Declared empty engineering repository for isolated skill construction.\n"
        )
        git(repository, "add", "README.md")
        git(repository, "commit", "-m", "Initialize synthetic builder fixture")
    backend = FixtureBackends()
    director, critic = ReviewedDirectorFixture(), CriticFixture()
    engine = MechanismResearch(
        store,
        provider_factory=lambda _: WorkflowFixtureProvider(),
        program=ResearchProgram(
            store,
            provider_factory=lambda _: director,
            critic_provider_factory=lambda _: critic,
            transport_factory=backend,
        ),
    )
    owner = engine.create(
        MechanismResearchSpec(
            contract_ref=store.artifacts.put(contract),
            dataset_refs=tuple(store.artifacts.put(s) for s in snapshots),
            registry=registry.registry,
            config=config,
            program=program if include_experiment else None,
            builder_repository=str(repository),
        )
    )
    return engine, owner


def finish_workflow(engine: MechanismResearch, owner: str) -> dict[str, Any]:
    for _ in range(150):
        result = engine.advance(owner)
        if result["status"] == "CLOSED":
            return workflow_report(engine.store, owner)
        if result["status"] in {"BUDGET_EXHAUSTED", "NEEDS_RECONCILIATION"}:
            raise RuntimeError("fixture incomplete: " + str(result))
    raise RuntimeError("fixture exceeded its declared engineering step bound")


def run_mechanism_fixture(store: Store) -> dict[str, Any]:
    engine, owner = fixture_workflow(store)
    initial = finish_workflow(engine, owner)
    built = payloads(store, owner, "SKILL_BUILD_COMPLETED")
    if not built or built[-1]["status"] != "SKILL_READY_FOR_SUCCESSOR":
        return {
            "status": "INCOMPLETE",
            "initial": initial,
            "reason": "Isolated skill construction did not pass; no successor adoption claimed",
            "external_llm_calls": 0,
            "remote_iris_submissions": 0,
        }
    child = engine.successor(owner)
    subsequent = finish_workflow(engine, child)
    return {
        "status": "VERIFIED",
        "initial": initial,
        "successor": subsequent,
        "full_owner_usage": store.resource_totals_tree(owner),
        "exact_replay": replay_workflow(store, child),
        "unknown_replay": replay_workflow(store, child, [digest({"unobserved": True})]),
        "external_llm_calls": 0,
        "remote_iris_submissions": 0,
        "boundary": "Scripted providers and artificial data; real isolated Python execution and local candidate Git commit. Fidelity/reuse demonstrated; research utility and Dream policy gains remain unverified.",
    }
