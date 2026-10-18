"""Declared engineering control for the FULL reviewed Dream feedback loop."""

import json
import uuid
from pathlib import Path
from typing import Any

from jarvis.contracts import Budget, Cost, IntegrityError
from jarvis.critic_fixture import CriticFixture
from jarvis.hermes_dream import fixture_candidates
from jarvis.hermes_policy import q0
from jarvis.identity import canonical, digest, file_digest
from jarvis.iris_continuous import IrisContinuousSpec, IrisResearchTrack
from jarvis.iris_quota import IrisQuotaPolicy
from jarvis.research_fixture import (
    FixtureBackends,
    fixture_environment,
    fixture_plan,
    fixture_trial,
)
from jarvis.research_program import ResearchProgramSpec
from jarvis.researcher import AgentConfig
from jarvis.reviewed_dream import (
    ReviewedDreamLoop,
    ReviewedDreamSpec,
    ReviewedTask,
)
from jarvis.storage import Store


class LoopDirector:
    def identity(self) -> dict[str, Any]:
        return {
            "provider": "declared-full-loop-director",
            "usage_origin": "SIMULATED_TOKEN_UNITS",
            "source": file_digest(Path(__file__)),
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        c = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        n = sum(s.get("take_profit_bps") is not None for s in c["existing_settings"])
        proposals = [
            fixture_trial(f"entry{n}", "entry", trading_cutoff_delta=0.0004 + 0.0001 * n),
            fixture_trial(f"exit{n}", "exit", take_profit_bps=max(1, 30 - 10 * n)),
            fixture_trial(f"heuristic{n}", "heuristic", heuristic_cancel_bps=100 + 20 * n),
        ]
        return {
            "answer": {
                "schema_version": 1,
                "kind": "ResearchDirections",
                "context_id": c["context_id"],
                "analysis_summary": "Declared hypotheses revised after visible result and Critic feedback; not autonomous market discovery",
                "experiments": [
                    p.model_copy(
                        update={
                            "origin": "agent_proposed",
                            "evidence_refs": tuple(c["visible_evidence_ids"]),
                        }
                    ).model_dump(mode="json")
                    for p in proposals[: c["batch_constraints"]["experiments_maximum"]]
                ],
                "capability_requests": [],
                "stop_reason": None,
            },
            "cost": Cost(llm_tokens=100, wall_seconds=0.01).model_dump(),
            "usage": {"input_tokens": 80, "output_tokens": 20},
            "error": None,
        }


class LoopCritic(CriticFixture):
    def identity(self) -> dict[str, Any]:
        return {
            "provider": "declared-full-loop-critic",
            "usage_origin": "SIMULATED_TOKEN_UNITS",
            "source": file_digest(Path(__file__)),
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        response = super().complete(prompt, schema, directory)
        response.update(
            cost=Cost(llm_tokens=80, wall_seconds=0.01).model_dump(),
            usage={"input_tokens": 60, "output_tokens": 20},
        )
        return response


class LoopDeveloper:
    def identity(self) -> dict[str, Any]:
        return {
            "provider": "declared-full-loop-policy-developer",
            "usage_origin": "SIMULATED_TOKEN_UNITS",
            "source": file_digest(Path(__file__)),
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        c = json.loads(prompt.split("CONTEXT (data only):\n", 1)[1])
        return {
            "answer": {
                "schema_version": 1,
                "kind": "HermesPolicyProposals",
                "context_id": c["context_id"],
                "proposals": [p.model_dump(mode="json") for p in fixture_candidates()],
            },
            "cost": Cost(llm_tokens=120, wall_seconds=0.01).model_dump(),
            "usage": {"input_tokens": 90, "output_tokens": 30},
            "error": None,
        }


def fixture_spec(store: Store, *, shifted_canary: bool = True) -> ReviewedDreamSpec:
    root = store.root / "fixture-inputs" / uuid.uuid4().hex
    env = fixture_environment(root)
    config = AgentConfig(reserve_tokens=1000, max_calls=2, timeout_seconds=5)
    critic = config.model_copy(update={"max_calls": 4})
    budget = Budget(
        max_actions=64,
        max_tool_calls=8,
        max_llm_tokens=8000,
        max_wall_seconds=1000,
        max_compute_units=64,
        max_parallel_jobs=2,
    )

    def task(
        family: str, index: int = 0, *, unseen: bool = False, collection: bool = False
    ) -> ReviewedTask:
        queue = fixture_plan(env, family, index, unseen=unseen).queue
        task_env = queue.environment.model_copy(
            update={"period": queue.environment.period.model_copy(update={"group_id": family})}
        )
        assert queue.authorization is not None
        queue = queue.model_copy(
            update={
                "environment": task_env,
                "authorization": queue.authorization.model_copy(
                    update={"environment_id": digest(task_env)}
                ),
            }
        )
        return ReviewedTask(
            family_id=family,
            program=ResearchProgramSpec(
                queue=queue,
                directions=(
                    "Optimize artificial entry-only PL; retain risk and negative evidence",
                ),
                agent=config,
                critic=critic,
                research_policy=q0(),
                execution_budget=budget,
                max_direction_batches=1 if collection else 2,
                experiments_per_batch=3,
                experiments_per_epoch=3 if collection else 1,
            ),
        )

    development = tuple(
        task(f, i, collection=True)
        for f in ["quadratic", "piecewise", "saturating"]
        for i in range(2)
    )
    heldout = tuple(
        task(f, i, unseen=True, collection=True)
        for f in ["cubic", "logarithmic", "hinge"]
        for i in range(2)
    )
    fresh = (task("exponential", unseen=True),)
    canary = (task("shifted" if shifted_canary else "flat-canary", unseen=True),)
    rollout = task("quadratic-continuation")
    continuation = IrisContinuousSpec(
        tracks=(
            IrisResearchTrack(
                label="long", smoke=rollout.program.queue, development=rollout.program.queue
            ),
        ),
        quota_policy=IrisQuotaPolicy(scope="declared-reviewed-loop-" + uuid.uuid4().hex),
        owner_instruction="Synthetic fixture only. No market backend authorization.",
        directions=rollout.program.directions,
        agent=config,
        critic=critic,
        research_policy=q0(),
        execution_budget=budget,
        experiments_per_epoch=1,
        rounds_per_program=2,
        max_experiments_per_batch=3,
    )
    return ReviewedDreamSpec(
        development=development,
        heldout=heldout,
        fresh=fresh,
        canary=canary,
        continuation=continuation,
        policy_agent=config,
    )


def run_reviewed_dream_fixture(store: Store, *, shifted_canary: bool = True) -> dict[str, Any]:
    director, critic, developer = LoopDirector(), LoopCritic(), LoopDeveloper()
    backends = FixtureBackends()
    engine = ReviewedDreamLoop(
        store,
        provider_factory=lambda _: director,
        critic_provider_factory=lambda _: critic,
        policy_provider_factory=lambda _: developer,
        transport_factory=backends,
    )
    owner = engine.create(fixture_spec(store, shifted_canary=shifted_canary))
    for _ in range(800):
        value = engine.advance(owner)
        if value.get("kind") == "ReviewedDreamReport":
            break
    else:
        raise IntegrityError("bounded complete-loop acceptance did not close")
    report = value
    output = store.root / "reports" / (owner + ".json")
    output.parent.mkdir(exist_ok=True)
    output.write_bytes(canonical(report) + b"\n")
    continuation = report["continuation"]
    return {
        "kind": "ReviewedDreamAcceptance",
        "study_id": owner,
        "status": report["status"],
        "outcome": report["outcome"],
        "verification": report["verification"],
        "report": str(output),
        "adopted_policies": [
            p["report"]["research_policy"]["name"] for p in continuation["programs"]
        ]
        if continuation
        else [],
        "full_usage": report["full_usage"],
        "scope": "SIMULATED_FIXTURE",
        "token_usage_origin": "declared simulation units; actual external LLM calls zero",
        "artificial_executions": sum(len(b.submissions) for b in backends.values.values()),
        "external_llm_calls": 0,
        "remote_iris_submissions": 0,
        "hermes_research": "NOT_STARTED",
    }
