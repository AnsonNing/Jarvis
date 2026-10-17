"""Durable sequential campaign controller and receipt-first recovery.

Only registered adapters run. The initial release executes no generated code.
An uncertain started job is retained for reconciliation, never silently retried.
"""

import importlib.metadata
import json
import platform
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis.adapters import DomainAdapter
from jarvis.agent_runtime import AgentNeedsReconciliation, AgentRuntime
from jarvis.agents import critique, curate, role_records
from jarvis.contracts import (
    ActionSpec,
    AdmissionError,
    BudgetExceeded,
    Cost,
    Evaluation,
    IntegrityError,
    JarvisError,
    Observation,
    ResearchState,
    ResearchTask,
    WorldSpec,
)
from jarvis.evaluation import evaluate, evaluator_identity
from jarvis.identity import digest, file_digest
from jarvis.policy import FixedPolicy, PolicySpec, admit
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.researcher import AgentConfig, policy_identity
from jarvis.storage import Store, utcnow
from jarvis.synthetic import SyntheticAdapter


def source_identity() -> str:
    root = Path(__file__).parent
    return digest({p.name: file_digest(p) for p in sorted(root.glob("*.py"))})


def code_provenance() -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[2]
    result: dict[str, Any] = {"source_identity": source_identity(), "commit": None, "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3
        )
        dirty = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "status",
                "--porcelain",
                "--untracked-files=normal",
                "--",
                "src",
                "pyproject.toml",
                "requirements-lock.txt",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if commit.returncode == 0 and dirty.returncode == 0:
            result.update(commit=commit.stdout.strip(), dirty=bool(dirty.stdout.strip()))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def environment() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "dependencies": dict(
            sorted(
                (d.metadata["Name"], d.version)
                for d in importlib.metadata.distributions()
                if d.metadata["Name"]
            )
        ),
    }


class InjectedInterruption(BaseException):
    """Test-only crash boundary, deliberately not caught as a research failure."""


class CampaignEngine:
    def __init__(
        self,
        store: Store,
        provider_factory: Callable[[AgentConfig], ResearchProvider] | None = None,
    ):
        self.store = store
        self.provider_factory = provider_factory or CodexProvider

    def create(
        self,
        task: ResearchTask,
        adapter: DomainAdapter | None = None,
        *,
        researcher: AgentConfig | None = None,
    ) -> str:
        adapter = adapter or SyntheticAdapter(task.world_ref)
        adapter.validate(task)
        identity = adapter.identity()
        env = environment()
        researcher_manifest = None
        selected_policy = FixedPolicy().identity
        if researcher:
            if task.budget.max_llm_tokens < researcher.reserve_tokens:
                raise AdmissionError("LLM budget cannot cover one declared provider reservation")
            descriptor = self.provider_factory(researcher).identity()
            selected_policy = policy_identity(researcher, descriptor)
            researcher_manifest = {
                "config": researcher.model_dump(mode="json"),
                "provider_identity": descriptor,
            }
        world = WorldSpec(
            task_identity=task.identity,
            domain=task.domain,
            adapter_id=adapter.adapter_id,
            adapter_source=identity["adapter_source"],
            evaluator_id=evaluator_identity(),
            engine_source=source_identity(),
            environment_identity=digest(env),
            dataset_identity=identity["dataset_identity"],
            split_identity=digest(task.split_policy),
            budget=task.budget,
            scope="synthetic_validation_only"
            if task.domain == "synthetic"
            else "existing_dataset_replay_only",
            origin=task.origin,
        )
        return self.store.create(
            {
                "schema_version": 1,
                "task": task.model_dump(mode="json"),
                "task_identity": task.identity,
                "world": world.model_dump(mode="json"),
                "world_id": world.identity,
                "environment": env,
                "code_provenance": code_provenance(),
                "policy": PolicySpec().model_dump(mode="json"),
                "policy_id": selected_policy,
                **({"researcher": researcher_manifest} if researcher_manifest else {}),
                "local_adapter": adapter.local_manifest(),
            }
        )

    def adapter(self, manifest: dict[str, Any]) -> DomainAdapter:
        if manifest["task"]["domain"] == "synthetic":
            return SyntheticAdapter(manifest["task"]["world_ref"])
        from jarvis.hermes import HermesAdapter

        config = manifest["local_adapter"]
        return HermesAdapter(
            Path(config["root"]),
            config["side"],
            Path(config["ledger_root"]) if config.get("ledger_root") else None,
        )

    def verify_world(self, manifest: dict[str, Any], adapter: DomainAdapter) -> ResearchTask:
        task = ResearchTask.model_validate(manifest["task"])
        world = WorldSpec.model_validate(manifest["world"])
        actual = adapter.identity()
        expected_policy = FixedPolicy(PolicySpec.model_validate(manifest["policy"])).identity
        if manifest.get("researcher"):
            config = AgentConfig.model_validate(manifest["researcher"]["config"])
            descriptor = self.provider_factory(config).identity()
            if descriptor != manifest["researcher"]["provider_identity"]:
                raise IntegrityError("frozen provider executable/config changed")
            expected_policy = policy_identity(config, descriptor)
        if (
            world.identity != manifest["world_id"]
            or task.identity != world.task_identity
            or source_identity() != world.engine_source
            or digest(environment()) != world.environment_identity
            or evaluator_identity() != world.evaluator_id
            or actual["adapter_source"] != world.adapter_source
            or actual["dataset_identity"] != world.dataset_identity
            or digest(task.split_policy) != world.split_identity
            or task.budget != world.budget
            or expected_policy != manifest["policy_id"]
        ):
            raise IntegrityError("frozen World/source/environment changed; create a new campaign")
        adapter.validate(task)
        return task

    def state(self, campaign_id: str, adapter: DomainAdapter) -> ResearchState:
        manifest = self.store.manifest(campaign_id)
        task = ResearchTask.model_validate(manifest["task"])
        events = self.store.events(campaign_id)
        observations, evaluations, refs = [], [], []
        parent = None
        for event in events:
            if event["event_type"] == "NODE_COMPLETED":
                payload = event["payload"]
                receipt = self.store.artifacts.get(payload["receipt_ref"])
                observations.append(Observation.model_validate(receipt["observation"]))
                evaluations.append(
                    Evaluation.model_validate(self.store.artifacts.get(payload["evaluation_ref"]))
                )
                refs.extend([receipt["observation_ref"], payload["evaluation_ref"]])
                parent = payload["node_id"]
        used = self.store.usage(campaign_id)
        remaining = {key: max(0, limit - used[key]) for key, limit in task.budget.limits().items()}
        state = ResearchState(
            campaign_id=campaign_id,
            world_id=manifest["world_id"],
            sequence=len(events),
            parent_node_id=parent,
            visible_evidence_ids=tuple(refs),
            observations=tuple(observations),
            evaluations=tuple(evaluations),
            remaining_budget=remaining,
            available_actions=(),
            selected_policy_id=manifest["policy_id"],
        )
        builder = (
            getattr(adapter, "research_frontier", adapter.frontier)
            if manifest.get("researcher")
            else adapter.frontier
        )
        return state.model_copy(update={"available_actions": builder(task, state)})

    def _complete(self, campaign_id: str, job: dict[str, Any], task: ResearchTask) -> bool:
        receipt = self.store.read_receipt(job["id"])
        if receipt is None:
            return False
        request = self.store.artifacts.get(job["request_ref"])
        prior = ResearchState.model_validate(request["state"])
        observation = Observation.model_validate(receipt["observation"])
        if observation.action_id != request["action"]["action_id"]:
            raise IntegrityError("tool response action mismatch")
        if self.store.artifacts.get(receipt["observation_ref"]) != observation.model_dump(
            mode="json"
        ):
            raise IntegrityError("observation reference mismatch")
        base = next(
            (
                o.metrics[task.primary_metric]
                for o in prior.observations
                if task.primary_metric in o.metrics
            ),
            None,
        )
        evaluation = evaluate(task, observation, base, receipt["observation_ref"])
        evaluation_ref = self.store.artifacts.put(evaluation)
        self.store.finish_job(
            job["id"],
            evaluation_ref,
            critique_ref=self.store.artifacts.put(
                critique(evaluation, observation, evaluation_ref)
            ),
            finding_ref=self.store.artifacts.put(curate(evaluation, evaluation_ref)),
        )
        return True

    def _execute(
        self,
        campaign_id: str,
        job: dict[str, Any],
        task: ResearchTask,
        adapter: DomainAdapter,
        fault: str | None,
    ) -> None:
        request = self.store.artifacts.get(job["request_ref"])
        action = ActionSpec.model_validate(request["action"])
        self.store.start_job(job["id"])
        if fault == "after_start":
            raise InjectedInterruption()
        start = time.monotonic()
        try:
            observation = adapter.execute(action)
        except Exception as exc:
            observation = Observation(
                action_id=action.action_id,
                status="FAILED",
                result_origin="SIMULATED" if task.domain == "synthetic" else "OBSERVED",
                findings=("EXECUTION_ERROR", type(exc).__name__, str(exc)),
                limitations=(
                    "Infrastructure or implementation failure is not a negative scientific result",
                ),
            )
        cost = Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=time.monotonic() - start)
        observation_ref = self.store.artifacts.put(observation)
        receipt = {
            "schema_version": 1,
            "job_id": job["id"],
            "request_ref": job["request_ref"],
            "world_id": self.store.manifest(campaign_id)["world_id"],
            "observation_ref": observation_ref,
            "observation": observation.model_dump(mode="json"),
            "cost": cost.model_dump(mode="json"),
            "completed_at": utcnow(),
            "cost_semantics": "tool_calls and compute_units count deterministic adapter invocations; not GPU hours",
        }
        self.store.write_receipt(job["id"], receipt)
        if fault == "after_receipt":
            raise InjectedInterruption()
        self._complete(campaign_id, job, task)

    def _freeze(self, campaign_id: str, task: ResearchTask, state: ResearchState) -> None:
        scored = [
            o
            for o in state.observations
            if task.primary_metric in o.metrics and o.status == "SUCCEEDED"
        ]
        if not scored:
            self.store.append(campaign_id, "CANDIDATE_FROZEN", {"candidate": None})
            return
        sign = 1 if task.metric_direction == "maximize" else -1
        best = max(scored, key=lambda o: sign * o.metrics[task.primary_metric])
        self.store.append(
            campaign_id,
            "CANDIDATE_FROZEN",
            {
                "candidate": best.action_id,
                "observation_ref": self.store.artifacts.put(best),
                "selection_evidence": state.visible_evidence_ids,
            },
        )

    def _close(self, campaign_id: str, task: ResearchTask, adapter: DomainAdapter) -> str:
        events = self.store.events(campaign_id)
        if not any(e["event_type"] == "CANDIDATE_FROZEN" for e in events):
            self._freeze(campaign_id, task, self.state(campaign_id, adapter))
            events = self.store.events(campaign_id)
        if not any(e["event_type"] == "FORMAL_RESULT" for e in events):
            candidate = next(
                e["payload"]["candidate"] for e in events if e["event_type"] == "CANDIDATE_FROZEN"
            )
            # Locked evaluation is itself a reserved, durable job. The first
            # version uses the synthetic-only deterministic formal operation.
            if candidate and task.domain == "synthetic":
                self._formal(campaign_id, task, adapter, candidate)
            else:
                self.store.append(
                    campaign_id,
                    "FORMAL_RESULT",
                    {
                        "status": "NOT_RUN",
                        "reason": "No untouched independent domain test configured",
                    },
                )
        self.store.set_status(campaign_id, "CLOSED")
        return "CLOSED"

    def _formal(
        self, campaign_id: str, task: ResearchTask, adapter: DomainAdapter, candidate: str
    ) -> None:
        # Deterministic synthetic evaluation can be recovered by its sealed
        # receipt. Unknown in-flight execution is never resubmitted.
        jobs = self.store.jobs(campaign_id)
        formal_jobs = [j for j in jobs if self.store.artifacts.get(j["request_ref"]).get("formal")]
        if formal_jobs:
            job = formal_jobs[0]
            receipt = self.store.read_receipt(job["id"])
            if receipt is None:
                self.store.set_status(campaign_id, "NEEDS_RECONCILIATION")
                raise JarvisError("formal job requires reconciliation")
        else:
            request = {
                "formal": True,
                "candidate": candidate,
                "world_id": self.store.manifest(campaign_id)["world_id"],
            }
            try:
                job_id = self.store.reserve(
                    campaign_id,
                    request,
                    Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=10),
                )
            except BudgetExceeded:
                self.store.append(
                    campaign_id,
                    "FORMAL_RESULT",
                    {"status": "NOT_RUN", "reason": "Evaluation budget exhausted"},
                )
                return
            self.store.start_job(job_id)
            started = time.monotonic()
            observation = adapter.formal(candidate)
            if observation is None:
                raise IntegrityError("declared synthetic locked evaluator missing")
            job = next(j for j in self.store.jobs(campaign_id) if j["id"] == job_id)
            receipt = {
                "schema_version": 1,
                "job_id": job_id,
                "request_ref": job["request_ref"],
                "world_id": request["world_id"],
                "observation": observation.model_dump(mode="json"),
                "observation_ref": self.store.artifacts.put(observation),
                "cost": Cost(
                    actions=1,
                    tool_calls=1,
                    compute_units=1,
                    wall_seconds=time.monotonic() - started,
                ).model_dump(),
            }
            self.store.write_receipt(job_id, receipt)
        observation = Observation.model_validate(receipt["observation"])
        evaluation = evaluate(
            task,
            observation,
            observation.metrics["locked_baseline"],
            receipt["observation_ref"],
            formal=True,
        )
        evaluation_ref = self.store.artifacts.put(evaluation)
        # Formal outputs never become development nodes/evidence.
        with self.store.transaction() as conn:
            row = conn.execute("SELECT finalized FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if not row[0]:
                conn.execute(
                    "UPDATE jobs SET status='SUCCEEDED',actual=?,receipt_ref=?,finalized=1 WHERE id=?",
                    (
                        json.dumps(receipt["cost"]),
                        self.store.artifacts.put(receipt),
                        job["id"],
                    ),
                )
                self.store._append(
                    conn,
                    campaign_id,
                    "FORMAL_RESULT",
                    {
                        "status": "EVALUATED",
                        "receipt_ref": self.store.artifacts.put(receipt),
                        "evaluation_ref": evaluation_ref,
                    },
                )

    def run(
        self, campaign_id: str, *, max_steps: int | None = None, fault: str | None = None
    ) -> str:
        if max_steps is not None and max_steps < 0:
            raise AdmissionError("max_steps must be nonnegative")
        with self.store.campaign_lock(campaign_id):
            status = self.store.status(campaign_id)
            if status in {"CLOSED", "CANCELLED", "EXHAUSTED", "FAILED"}:
                return status
            manifest = self.store.manifest(campaign_id)
            adapter = self.adapter(manifest)
            task = self.verify_world(manifest, adapter)
            runtime = None
            if manifest.get("researcher"):
                config = AgentConfig.model_validate(manifest["researcher"]["config"])
                runtime = AgentRuntime(self.store, config, self.provider_factory(config))
            if self.store.get_control(campaign_id) == "CANCELLED":
                self.store.set_status(campaign_id, "CANCELLED")
                return "CANCELLED"
            self.store.control(campaign_id, "RUNNING")
            self.store.set_status(campaign_id, "SEARCHING")
            for job in self.store.jobs(campaign_id):
                if job["finalized"]:
                    continue
                request = self.store.artifacts.get(job["request_ref"])
                if request.get("formal") or request.get("agent_call"):
                    continue
                if self._complete(campaign_id, job, task):
                    continue
                if job["status"] != "QUEUED":
                    self.store.set_status(campaign_id, "NEEDS_RECONCILIATION")
                    return "NEEDS_RECONCILIATION"
                self._execute(campaign_id, job, task, adapter, fault)
            if any(e["event_type"] == "CANDIDATE_FROZEN" for e in self.store.events(campaign_id)):
                return self._close(campaign_id, task, adapter)
            if any(
                e["event_type"] == "DECISION" and e["payload"]["decision"]["action_id"] == "stop"
                for e in self.store.events(campaign_id)
            ):
                return self._close(campaign_id, task, adapter)
            steps = 0
            while True:
                control = self.store.get_control(campaign_id)
                if control in {"PAUSED", "CANCELLED"}:
                    self.store.set_status(campaign_id, control)
                    return control
                if max_steps is not None and steps >= max_steps:
                    self.store.set_status(campaign_id, "PAUSED")
                    return "PAUSED"
                elapsed = (
                    datetime.fromisoformat(utcnow())
                    - datetime.fromisoformat(manifest["created_at"])
                ).total_seconds()
                if elapsed >= task.budget.max_wall_seconds:
                    self.store.set_status(campaign_id, "EXHAUSTED")
                    return "EXHAUSTED"
                state = self.state(campaign_id, adapter)
                choice = None
                if runtime:
                    try:
                        choice = runtime.choose(campaign_id, task, state, fault=fault)
                    except AgentNeedsReconciliation as exc:
                        self.store.append(campaign_id, "AGENT_UNCERTAIN", {"reason": str(exc)})
                        self.store.set_status(campaign_id, "NEEDS_RECONCILIATION")
                        return "NEEDS_RECONCILIATION"
                    except BudgetExceeded as exc:
                        self.store.append(campaign_id, "BUDGET_EXHAUSTED", {"reason": str(exc)})
                        self.store.set_status(campaign_id, "EXHAUSTED")
                        return "EXHAUSTED"
                    except AdmissionError as exc:
                        self.store.append(campaign_id, "AGENT_FAILED", {"reason": str(exc)})
                        self.store.set_status(campaign_id, "FAILED")
                        return "FAILED"
                    state, decision, action = choice.state, choice.decision, choice.action
                else:
                    decision = FixedPolicy().choose(state)
                    admit(decision, state)
                    action = next(
                        a for a in state.available_actions if a.action_id == decision.action_id
                    )
                if action.split_id != task.split_policy.development_id:
                    raise AdmissionError("WRONG_SPLIT")
                if action.action_type == "STOP":
                    self.store.append(
                        campaign_id,
                        "DECISION",
                        {
                            "state": state.model_dump(mode="json"),
                            "decision": decision.model_dump(mode="json"),
                            **(
                                {
                                    "agent_job_id": choice.job_id,
                                    "research_turn_ref": self.store.artifacts.put(choice.turn),
                                }
                                if choice
                                else {}
                            ),
                        },
                    )
                    return self._close(campaign_id, task, adapter)
                request = {
                    "schema_version": 1,
                    "state": state.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                    "action": action.model_dump(mode="json"),
                    "roles": [
                        {
                            "role": "director",
                            "origin": "provider",
                            "next_direction": choice.turn.next_direction,
                            "evidence_refs": choice.turn.evidence_refs,
                        },
                        {
                            "role": "scientist",
                            "origin": "agent_proposed",
                            "hypothesis": action.hypothesis.model_dump(mode="json"),
                        },
                        {
                            "role": "designer",
                            "origin": "agent_proposed",
                            "design": choice.turn.experiment.model_dump(mode="json"),
                        },
                        {
                            "role": "critic",
                            "origin": "provider",
                            "analysis_summary": choice.turn.analysis_summary,
                            "evidence_refs": choice.turn.evidence_refs,
                        },
                    ]
                    if choice
                    else role_records(state, decision),
                    "world_id": manifest["world_id"],
                    **(
                        {
                            "agent_job_id": choice.job_id,
                            "agent_request_id": choice.request_id,
                            "research_turn_ref": self.store.artifacts.put(choice.turn),
                        }
                        if choice
                        else {}
                    ),
                }
                try:
                    job_id = self.store.reserve(campaign_id, request, action.estimated_cost)
                except BudgetExceeded as exc:
                    self.store.append(campaign_id, "BUDGET_EXHAUSTED", {"reason": str(exc)})
                    self.store.set_status(campaign_id, "EXHAUSTED")
                    return "EXHAUSTED"
                if fault == "after_reserve":
                    raise InjectedInterruption()
                job = next(j for j in self.store.jobs(campaign_id) if j["id"] == job_id)
                self._execute(campaign_id, job, task, adapter, fault)
                steps += 1
                completed_receipt = self.store.read_receipt(job_id)
                if completed_receipt is None:
                    raise IntegrityError("completed job lost its receipt")
                if completed_receipt["observation"]["status"] != "SUCCEEDED":
                    self.store.set_status(campaign_id, "FAILED")
                    return "FAILED"
