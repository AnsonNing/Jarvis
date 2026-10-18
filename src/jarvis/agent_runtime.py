"""Durable LLM decision jobs with paid rejection and receipt-first recovery."""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from jarvis.contracts import (
    ActionSpec,
    AdmissionError,
    BudgetExceeded,
    Cost,
    IntegrityError,
    JarvisError,
    PolicyDecision,
    ResearchState,
    ResearchTask,
)
from jarvis.new_research import ResearchTurnV2
from jarvis.providers import ResearchProvider
from jarvis.researcher import (
    AgentConfig,
    ResearchTurn,
    admit_turn,
    parse_turn,
    prompt_context,
    render_prompt,
    structured_schema,
)
from jarvis.storage import Store, utcnow


class AgentNeedsReconciliation(JarvisError):
    pass


class AgentInterrupted(BaseException):
    """Test-only coordinator interruption after a completed provider receipt."""


@dataclass(frozen=True)
class ResearchChoice:
    state: ResearchState
    decision: PolicyDecision
    action: ActionSpec
    turn: ResearchTurn | ResearchTurnV2
    job_id: str
    request_id: str


class AgentRuntime:
    def __init__(self, store: Store, config: AgentConfig, provider: ResearchProvider):
        self.store, self.config, self.provider = store, config, provider

    def _agent_jobs(self, campaign_id: str) -> list[dict[str, Any]]:
        return [
            j
            for j in self.store.jobs(campaign_id)
            if self.store.artifacts.get(j["request_ref"]).get("agent_call")
        ]

    def _completion(self, campaign_id: str, job: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (
                e["payload"]
                for e in self.store.events(campaign_id)
                if e["event_type"] == "AGENT_COMPLETED" and e["payload"]["job_id"] == job["id"]
            ),
            None,
        )

    def _used(self, campaign_id: str) -> set[str]:
        used = set()
        for job in self.store.jobs(campaign_id):
            request = self.store.artifacts.get(job["request_ref"])
            if request.get("agent_job_id"):
                used.add(request["agent_job_id"])
        for event in self.store.events(campaign_id):
            if event["event_type"] == "DECISION" and event["payload"].get("agent_job_id"):
                used.add(event["payload"]["agent_job_id"])
        return used

    def _settle(self, campaign_id: str, job: dict[str, Any]) -> None:
        receipt = self.store.read_receipt(job["id"])
        if receipt is None or receipt.get("cost") is None:
            raise AgentNeedsReconciliation(
                "provider completion or usage unknown; no automatic resubmission"
            )
        raw_usage = receipt.get("usage") or {}
        if (
            any(
                type(raw_usage.get(k)) is not int or raw_usage[k] < 0
                for k in ["input_tokens", "output_tokens"]
            )
            or receipt["cost"]["llm_tokens"]
            != raw_usage["input_tokens"] + raw_usage["output_tokens"]
        ):
            raise AgentNeedsReconciliation(
                "provider cost does not reconcile to observed token usage"
            )
        request = self.store.artifacts.get(job["request_ref"])
        rejection = receipt.get("error")
        turn_ref = None
        if not rejection:
            try:
                turn = parse_turn(receipt["answer"])
                state = ResearchState.model_validate(request["state"])
                _, action = admit_turn(turn, state, request["request_id"])
                for previous in self.store.jobs(campaign_id):
                    previous_request = self.store.artifacts.get(previous["request_ref"])
                    if not previous_request.get("agent_call") and previous_request.get("action"):
                        prior_action = previous_request["action"]
                        if (
                            prior_action["tool_id"] == action.tool_id
                            and prior_action["arguments"] == action.arguments
                        ):
                            raise AdmissionError(
                                "DUPLICATE_EXPERIMENT: identical intervention already dispatched"
                            )
                turn_ref = self.store.artifacts.put(turn)
            except (ValidationError, AdmissionError) as exc:
                rejection = "INVALID_PROPOSAL: " + str(exc)[:1200]
        self.store.finish_agent_job(job["id"], turn_ref=turn_ref, rejection=rejection)

    def _dispatch(self, campaign_id: str, job: dict[str, Any], fault: str | None) -> None:
        request = self.store.artifacts.get(job["request_ref"])
        self.store.start_job(job["id"])
        if fault == "agent_after_start":
            raise AgentInterrupted()
        try:
            response = self.provider.complete(
                request["prompt"],
                request["output_schema"],
                self.store.root / "provider-requests" / job["id"],
            )
        except Exception as exc:
            response = {
                "answer": None,
                "cost": None,
                "usage": None,
                "error": "PROVIDER_INTERRUPTED: " + type(exc).__name__,
            }
        receipt = {
            **response,
            "schema_version": 1,
            "job_id": job["id"],
            "request_ref": job["request_ref"],
            "world_id": request["world_id"],
            "completed_at": utcnow(),
        }
        self.store.write_receipt(job["id"], receipt)
        if fault == "agent_after_receipt":
            raise AgentInterrupted()
        self._settle(campaign_id, job)

    def choose(
        self,
        campaign_id: str,
        task: ResearchTask,
        state: ResearchState,
        *,
        fault: str | None = None,
    ) -> ResearchChoice:
        used = self._used(campaign_id)
        for job in self._agent_jobs(campaign_id):
            if job["finalized"] or job["id"] in used:
                continue
            if self.store.read_receipt(job["id"]) is not None:
                self._settle(campaign_id, job)
            elif job["status"] == "QUEUED":
                self._dispatch(campaign_id, job, fault)
            else:
                raise AgentNeedsReconciliation("in-flight agent job requires reconciliation")
        repairs = 0
        while True:
            jobs = self._agent_jobs(campaign_id)
            available = []
            previous_turns = []
            rejection = None
            for job in jobs:
                if (
                    job["actual"]
                    and Cost.model_validate_json(job["actual"]).llm_tokens
                    > self.config.reserve_tokens
                ):
                    raise BudgetExceeded("observed LLM token overrun; further dispatch stopped")
                completion = self._completion(campaign_id, job)
                if completion is None:
                    continue
                if completion.get("turn_ref"):
                    turn = parse_turn(self.store.artifacts.get(completion["turn_ref"]))
                    if job["id"] not in used:
                        available.append((job, turn))
                    else:
                        previous_turns.append(turn.model_dump(mode="json"))
                elif job["id"] not in used:
                    request = self.store.artifacts.get(job["request_ref"])
                    if request["state"]["parent_node_id"] == state.parent_node_id:
                        rejection = completion["rejection"]
                        repairs += 1
            if available:
                job, turn = available[0]
                request = self.store.artifacts.get(job["request_ref"])
                saved_state = ResearchState.model_validate(request["state"])
                if saved_state.visible_evidence_ids != state.visible_evidence_ids:
                    raise IntegrityError("unconsumed agent proposal has stale evidence")
                actual = Cost.model_validate_json(job["actual"])
                if (
                    actual.llm_tokens > self.config.reserve_tokens
                    or self.store.usage(campaign_id)["llm_tokens"] > task.budget.max_llm_tokens
                ):
                    raise BudgetExceeded("observed LLM token overrun; further dispatch stopped")
                decision, action = admit_turn(turn, saved_state, request["request_id"])
                return ResearchChoice(
                    saved_state, decision, action, turn, job["id"], request["request_id"]
                )
            if rejection and (
                repairs > self.config.max_repairs
                or not rejection.startswith(("INVALID_PROPOSAL", "INVALID_STRUCTURED_RESPONSE"))
            ):
                raise AdmissionError("agent proposal rejected: " + rejection)
            if len(jobs) >= self.config.max_calls:
                raise BudgetExceeded("max LLM calls reached")
            current_usage = self.store.usage(campaign_id)
            state = ResearchState.model_validate(
                {
                    **state.model_dump(mode="json"),
                    "sequence": len(self.store.events(campaign_id)),
                    "remaining_budget": {
                        key: max(0, limit - current_usage[key])
                        for key, limit in task.budget.limits().items()
                    },
                }
            )
            context = prompt_context(task, state, previous_turns, rejection)
            request = {
                "schema_version": 1,
                "agent_call": True,
                "state": state.model_dump(mode="json"),
                "world_id": state.world_id,
                "request_id": context["request_id"],
                "context": context,
                "prompt": render_prompt(context),
                "output_schema": structured_schema(state, context["request_id"]),
            }
            job_id = self.store.reserve(
                campaign_id,
                request,
                Cost(
                    llm_tokens=self.config.reserve_tokens, wall_seconds=self.config.timeout_seconds
                ),
            )
            if fault == "agent_after_reserve":
                raise AgentInterrupted()
            job = next(j for j in self.store.jobs(campaign_id) if j["id"] == job_id)
            self._dispatch(campaign_id, job, fault)
            # Fresh state identity remains the one actually sent to the provider.
            # Budget admission is independently rechecked before every dispatch.
            repairs = 0
