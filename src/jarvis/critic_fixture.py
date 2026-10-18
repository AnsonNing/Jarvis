"""Independent Critic acceptance on artificial objectives, never remote Iris."""

import json
import uuid
from pathlib import Path
from typing import Any, Literal

from jarvis.contracts import Cost, IntegrityError
from jarvis.identity import canonical, digest, file_digest
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.research_fixture import (
    FixtureBackends,
    fixture_environment,
    fixture_plan,
    fixture_trial,
)
from jarvis.research_program import ResearchProgram, ResearchProgramSpec, program_report
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


class CriticFixture:
    """Declared engineering control, not an LLM or autonomous discovery."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def identity(self) -> dict[str, Any]:
        return {
            "provider": "independent-critic-fixture",
            "implementation": file_digest(Path(__file__)),
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        context = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        self.calls.append(context)
        assessments = []
        outcomes = {o["experiment_ref"]: o for o in context["outcomes"]}
        observations = {o["result_ref"]: o["result"] for o in context["observations"]}
        for p in context["proposals"]:
            evidence = context["visible_evidence_ids"][:1]
            if context["phase"] == "proposal":
                settings = p["experiment"]["settings"]
                verdict = (
                    "revise"
                    if sum(v is not None for k, v in settings.items() if k != "schema_version") > 1
                    else "approve"
                )
            else:
                o = outcomes[p["experiment_ref"]]
                evidence = [o["result_ref"]] if o["result_ref"] else []
                verdict = (
                    "not_executed"
                    if o["status"] == "NOT_EXECUTED"
                    else (
                        "supported"
                        if observations[o["result_ref"]]["entry_only_pl"] > 1
                        else "negative"
                    )
                )
            assessments.append(
                {
                    "schema_version": 1,
                    "experiment_ref": p["experiment_ref"],
                    "verdict": verdict,
                    "rationale": "Check intervention isolation and observed artificial objective against the fixed baseline",
                    "concerns": ["Selection bias and artificial data limit transfer"],
                    "evidence_refs": evidence,
                    "next_step": "Use one bounded intervention; retain negative evidence",
                }
            )
        return {
            "answer": {
                "schema_version": 1,
                "kind": "ResearchCritique",
                "context_id": context["context_id"],
                "phase": context["phase"],
                "batch_ref": context["batch_ref"],
                "summary": "Independent declared review; conclusions are advisory, evaluator values are immutable",
                "assessments": assessments,
                "limitations": ["Artificial workflow evidence only"],
                "capability_requests": [],
                "stop_recommended": False,
            },
            "error": None,
            "cost": Cost().model_dump(),
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


class ReviewedDirectorFixture:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def identity(self) -> dict[str, Any]:
        return {
            "provider": "reviewed-director-fixture",
            "implementation": file_digest(Path(__file__)),
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        context = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        self.calls.append(context)
        second = bool(context["critic_feedback"])
        design = (
            [fixture_trial("entry", "entry", trading_cutoff_delta=0.0004)]
            if second
            else [
                fixture_trial("exit", "exit", take_profit_bps=30),
                fixture_trial("negative-heuristic", "heuristic", heuristic_cancel_bps=100),
                fixture_trial("confounded", "joint", take_profit_bps=20, heuristic_cancel_bps=80),
            ]
        )
        design = design[: context["batch_constraints"]["experiments_maximum"]]
        return {
            "answer": {
                "kind": "ResearchDirections",
                "schema_version": 1,
                "context_id": context["context_id"],
                "analysis_summary": "Revise using independent critique and retained negative observations"
                if second
                else "Test positive, negative and confounded artificial hypotheses",
                "experiments": [
                    e.model_copy(
                        update={
                            "origin": "agent_proposed",
                            "evidence_refs": tuple(context["visible_evidence_ids"]),
                        }
                    ).model_dump(mode="json")
                    for e in design
                ],
                "capability_requests": [],
                "stop_reason": "Declared two-round engineering acceptance complete"
                if second
                else None,
            },
            "error": None,
            "cost": Cost().model_dump(),
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


def run_critic_fixture(
    store: Store,
    *,
    agent: Literal["fixture", "codex"] = "fixture",
    executable: str = "codex",
) -> dict[str, Any]:
    env = fixture_environment(store.root / "fixture-inputs" / uuid.uuid4().hex)
    backends = FixtureBackends()
    rounds = 2 if agent == "fixture" else 1
    config = AgentConfig(
        executable=executable, max_calls=rounds, reserve_tokens=65536, timeout_seconds=180
    )
    critic_config = config.model_copy(update={"max_calls": 2 * rounds})
    director: ResearchProvider = (
        CodexProvider(config) if agent == "codex" else ReviewedDirectorFixture()
    )
    critic: ResearchProvider = CodexProvider(critic_config) if agent == "codex" else CriticFixture()
    engine = ResearchProgram(
        store,
        provider_factory=lambda _: director,
        critic_provider_factory=lambda _: critic,
        transport_factory=backends,
    )
    program = engine.create(
        ResearchProgramSpec(
            queue=fixture_plan(env, "quadratic").queue,
            agent=config,
            critic=critic_config,
            directions=(
                "Study falsifiable single-intervention artificial hypotheses; analyze negative results, all risks and independent critique before revising",
            ),
            max_direction_batches=rounds,
            experiments_per_batch=3 if agent == "fixture" else 2,
        )
    )
    for _ in range(80):
        status = engine.advance(program)["status"]
        if status == "CLOSED":
            break
        if status in {"NEEDS_RECONCILIATION", "CRITIC_NEEDS_ATTENTION", "BUDGET_EXHAUSTED"}:
            raise IntegrityError(
                f"Critic fixture stopped: {status}; preserve receipts, do not retry"
            )
    else:
        raise IntegrityError("finite Critic acceptance did not close")
    report = program_report(store, program)
    destination = store.root / "reports" / (program + ".json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_bytes(canonical(report) + b"\n")
    return {
        "kind": "IndependentCriticAcceptance",
        "status": status,
        "agent": agent,
        "scope": "SIMULATED_FIXTURE",
        "program_id": program,
        "report": str(destination),
        "report_ref": digest(report),
        "verification": report["program_verification"],
        "role_calls": {
            role: sum(
                e["event_type"] == "ROLE_COMPLETED" and e["payload"]["role"] == role
                for e in store.events(program)
            )
            for role in ["direction", "critic"]
        },
        "usage": store.resource_totals(program),
        "artificial_executions": sum(len(b.submissions) for b in backends.values.values()),
        "remote_iris_submissions": 0,
        "hermes_research": "NOT_STARTED",
    }
