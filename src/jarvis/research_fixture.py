"""Repeatable artificial strategy research and Hermes Dream acceptance commands."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from jarvis.contracts import Cost, IntegrityError
from jarvis.hermes_dream import HermesDream, HermesDreamSpec, verify_hermes_dream
from jarvis.hermes_policy import HermesWorldPlan, seal_world
from jarvis.identity import canonical, digest, file_digest
from jarvis.iris_contracts import (
    IrisAuthorization,
    IrisEnvironment,
    IrisExperiment,
    IrisQueueSpec,
    IrisSettings,
)
from jarvis.iris_fixture import FixtureIrisTransport
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.research_program import ResearchProgram, ResearchProgramSpec, program_report
from jarvis.research_queue import IrisResearchQueue
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


def fixture_environment(directory: Path) -> IrisEnvironment:
    directory.mkdir(parents=True, exist_ok=False)
    template = directory / "toy.txt"
    template.write_text(
        "// Artificial constants only\n// @start:config\nconstexpr double toy_exit = 0.004;\nconstexpr double toy_entry = 0;\nconstexpr double toy_heuristic = 0.004;\n// @end:config\n"
    )
    agent = directory / "iris_agent.py"
    evaluator = directory / "evaluator.py"
    agent.write_text("# Inert artificial fixture; no external service\n")
    evaluator.write_text("# Inert artificial fixture; no market evaluator\n")
    return IrisEnvironment.model_validate(
        {
            "root": str(directory),
            "python": "unused-fixture-runtime",
            "branch": "toy",
            "template": str(template),
            "template_sha256": file_digest(template),
            "evaluator": str(evaluator),
            "evaluator_sha256": file_digest(evaluator),
            "agent_script_sha256": file_digest(agent),
            "accepted_inputs": {str(template): file_digest(template)},
            "side": "long",
            "model_lineage": "quadratic-fixture",
            "result_origin": "SIMULATED_FIXTURE",
            "bindings": [
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
            ],
            "period": {
                "start": "2026-01-05",
                "end": "2026-01-06",
                "trading_dates": ["2026-01-05", "2026-01-06"],
                "group_id": "toy-dev",
            },
        }
    )


def fixture_trial(
    label: str = "baseline", family: Any = "baseline", **settings: Any
) -> IrisExperiment:
    return IrisExperiment(
        label=label,
        family=family,
        settings=IrisSettings(**settings),
        hypothesis="Artificial single-factor objective hypothesis",
        falsification="Measured objective does not improve under matched controls",
        origin="declared_fixture",
    )


def fixture_plan(
    env: IrisEnvironment, family: str, index: int = 0, *, unseen: bool = False
) -> HermesWorldPlan:
    env = env.model_copy(
        update={
            "model_lineage": f"{family}-{index}",
            "period": env.period.model_copy(
                update={
                    "group_id": f"{family}-{index}",
                    "exposure": "sealed_unseen" if unseen else "development_exposed",
                }
            ),
        }
    )
    return HermesWorldPlan(
        queue=IrisQueueSpec(
            goal="Maximize artificial entry-only PL, retain all risks",
            environment=env,
            authorization=IrisAuthorization(
                environment_id=digest(env),
                max_submissions=8,
                expires_at=datetime.now(UTC) + timedelta(hours=2),
                owner_note="Artificial fixture only; no Iris permission",
            ),
        ),
        trials=(
            fixture_trial(),
            fixture_trial("entry", "entry", trading_cutoff_delta=0.0001),
            fixture_trial("exit", "exit", take_profit_bps=30),
            fixture_trial("heuristic", "heuristic", heuristic_cancel_bps=60),
        ),
        family_id=family,
    )


class FixtureBackends:
    def __init__(self) -> None:
        self.values: dict[str, FixtureIrisTransport] = {}

    def __call__(self, env: IrisEnvironment) -> FixtureIrisTransport:
        key = digest(env)
        if key not in self.values:
            self.values[key] = FixtureIrisTransport(
                env, shift=env.model_lineage.startswith("shifted")
            )
        return self.values[key]


class DeclaredDirector:
    """Engineering control. Clearly distinct from real LLM hypothesis generation."""

    def identity(self) -> dict[str, Any]:
        return {
            "provider": "declared-director-fixture",
            "implementation": file_digest(Path(__file__)),
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        context = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        settings = context["existing_settings"]
        design = (
            [
                ("exit", "exit", {"take_profit_bps": 30}),
                ("heuristic", "heuristic", {"heuristic_cancel_bps": 60}),
            ]
            if len(settings) == 1
            else [("entry", "entry", {"trading_cutoff_delta": 0.0001})]
        )
        experiments = [
            fixture_trial(label, family, **changes)
            .model_copy(
                update={
                    "origin": "agent_proposed",
                    "evidence_refs": tuple(context["visible_evidence_ids"]),
                }
            )
            .model_dump(mode="json")
            for label, family, changes in design
        ]
        return {
            "answer": {
                "kind": "ResearchDirections",
                "schema_version": 1,
                "context_id": context["context_id"],
                "analysis_summary": "Declared engineering control using observed artificial evidence",
                "experiments": experiments,
                "capability_requests": [],
                "stop_reason": None,
            },
            "error": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "cost": Cost().model_dump(),
        }


def run_fixture(
    store: Store,
    mode: Literal["research", "dream"],
    *,
    agent: Literal["fixture", "codex"] = "fixture",
    executable: str = "codex",
) -> dict[str, Any]:
    import uuid

    folder = store.root / "fixture-inputs" / uuid.uuid4().hex
    env = fixture_environment(folder)
    backends = FixtureBackends()
    config = AgentConfig(
        executable=executable,
        reserve_tokens=65536,
        max_calls=2 if mode == "research" else 1,
        timeout_seconds=180,
    )
    provider: ResearchProvider = CodexProvider(config) if agent == "codex" else DeclaredDirector()
    if mode == "research":
        engine = ResearchProgram(
            store, provider_factory=lambda _: provider, transport_factory=backends
        )
        pid = engine.create(
            ResearchProgramSpec(
                queue=fixture_plan(env, "quadratic").queue,
                directions=(
                    "Explore entry, exit and heuristic settings; improve the artificial objective after analyzing results",
                ),
                max_direction_batches=2,
                agent=config,
            )
        )
        for _ in range(80):
            if engine.advance(pid)["status"] == "CLOSED":
                break
        else:
            raise IntegrityError("finite artificial research acceptance did not close")
        report = program_report(store, pid)
        identity = pid
        verification = report["program_verification"]
    else:
        queue = IrisResearchQueue(store, backends)

        def collect(family: str, index: int, unseen: bool) -> str:
            plan = fixture_plan(env, family, index, unseen=unseen)
            qid = queue.create(plan.queue)
            for t in plan.trials:
                queue.enqueue(qid, t)
            for _ in range(8):
                queue.tick(qid)
            return seal_world(store, qid, family)

        dev = tuple(
            collect(f, i, False) for f in ["quadratic", "piecewise", "saturating"] for i in range(2)
        )
        held = tuple(
            collect(f, i, True) for f in ["cubic", "logarithmic", "hinge"] for i in range(2)
        )
        dream = HermesDream(store, provider_factory=lambda _: provider, transport_factory=backends)
        identity = dream.create(
            HermesDreamSpec(
                development=dev,
                heldout=held,
                fresh=(fixture_plan(env, "exponential", unseen=True),),
                canary=(fixture_plan(env, "shifted", unseen=True),),
                developer=agent,
                experiments_per_world=1,
                agent=config,
            )
        )
        for _ in range(80):
            value = dream.advance(identity)
            if value.get("kind") == "HermesDreamReport":
                break
        else:
            raise IntegrityError("finite artificial Dream acceptance did not close")
        report = value
        verification = verify_hermes_dream(store, identity)
    destination = store.root / "reports" / (identity + ".json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_bytes(canonical(report) + b"\n")
    return {
        "status": report["status"],
        "id": identity,
        "scope": "SIMULATED_FIXTURE",
        "agent": agent,
        "verification": verification,
        "report": str(destination),
        "remote_iris_submissions": 0,
        "artificial_executions": sum(len(b.submissions) for b in backends.values.values()),
    }
