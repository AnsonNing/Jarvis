"""Reconstruct and verify discovery nodes without opening any domain input."""

from typing import Any

from jarvis.agents import Critique, Finding
from jarvis.contracts import (
    ActionSpec,
    Cost,
    Evaluation,
    IntegrityError,
    Observation,
    PolicyDecision,
    ResearchState,
    ResearchTask,
    WorldSpec,
)
from jarvis.identity import digest
from jarvis.policy import admit
from jarvis.researcher import ResearchTurn, prompt_context, prompt_matches, verify_designed_action
from jarvis.storage import Store


def nodes(store: Store, campaign_id: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in store.events(campaign_id) if e["event_type"] == "NODE_COMPLETED"]


def verify(store: Store, campaign_id: str) -> dict[str, Any]:
    manifest = store.manifest(campaign_id)
    task = ResearchTask.model_validate(manifest["task"])
    world = WorldSpec.model_validate(manifest["world"])
    if (
        task.identity != world.task_identity
        or task.identity != manifest["task_identity"]
        or world.identity != manifest["world_id"]
        or digest(manifest["environment"]) != world.environment_identity
        or world.budget != task.budget
        or world.split_identity != digest(task.split_policy)
    ):
        raise IntegrityError("task/world manifest mismatch")
    events = store.events(campaign_id)
    job_index = {j["id"]: j for j in store.jobs(campaign_id)}
    visible: list[str] = []
    observations: list[dict[str, Any]] = []
    evaluations: list[dict[str, Any]] = []
    parent = None
    snapshots: dict[
        int,
        tuple[tuple[str, ...], tuple[dict[str, Any], ...], tuple[dict[str, Any], ...], str | None],
    ] = {0: ((), (), (), None)}
    reserved: set[str] = set()
    completed: set[str] = set()
    frozen = False
    formal_count = 0
    accepted_agents: dict[str, str] = {}
    used_agents: set[str] = set()
    research_snapshots: dict[int, set[str]] = {0: set()}
    research_digests: set[str] = set()
    for e in events:
        payload = e["payload"]
        if e["event_type"] == "CANDIDATE_FROZEN":
            frozen = True
        if e["event_type"] == "JOB_RESERVED":
            job_id = payload["job_id"]
            if job_id in reserved or job_id not in job_index:
                raise IntegrityError("duplicate or missing job reservation")
            job = job_index[job_id]
            if job["request_ref"] != payload["request_ref"]:
                raise IntegrityError("request reference changed")
            request = store.artifacts.get(job["request_ref"])
            if request["world_id"] != manifest["world_id"]:
                raise IntegrityError("job World mismatch")
            if request.get("formal"):
                if not frozen or task.domain != "synthetic":
                    raise IntegrityError("formal test before freeze or unsupported formal domain")
            else:
                if frozen:
                    raise IntegrityError("development action after candidate freeze")
                prior = ResearchState.model_validate(request["state"])
                if prior.sequence >= e["sequence"] or prior.sequence not in snapshots:
                    raise IntegrityError("decision references a future state")
                known, obs, evs, expected_parent = snapshots[prior.sequence]
                if (
                    prior.visible_evidence_ids != known
                    or tuple(o.model_dump(mode="json") for o in prior.observations) != obs
                    or tuple(v.model_dump(mode="json") for v in prior.evaluations) != evs
                    or prior.parent_node_id != expected_parent
                    or prior.world_id != manifest["world_id"]
                    or prior.campaign_id != campaign_id
                    or prior.selected_policy_id != manifest["policy_id"]
                ):
                    raise IntegrityError("decision contains hindsight or incompatible state")
                if request.get("agent_call"):
                    context = request["context"]
                    if any(
                        digest(t) not in research_snapshots[prior.sequence]
                        for t in context["prior_research"]
                    ):
                        raise IntegrityError("agent context contains future research proposals")
                    expected = prompt_context(
                        task, prior, context["prior_research"], context["repair_reason"]
                    )
                    if (
                        digest(context) != digest(expected)
                        or not prompt_matches(request["prompt"], context)
                        or request["request_id"] != context["request_id"]
                    ):
                        raise IntegrityError(
                            "agent context differs from decision-time visible evidence"
                        )
                else:
                    admit(PolicyDecision.model_validate(request["decision"]), prior)
                    action = ActionSpec.model_validate(request["action"])
                    if request.get("research_turn_ref"):
                        agent_id = request["agent_job_id"]
                        if (
                            accepted_agents.get(agent_id) != request["research_turn_ref"]
                            or agent_id in used_agents
                        ):
                            raise IntegrityError("agent proposal unpaid, unknown or reused")
                        turn = ResearchTurn.model_validate(
                            store.artifacts.get(request["research_turn_ref"])
                        )
                        verify_designed_action(turn, prior, request["agent_request_id"], action)
                        used_agents.add(agent_id)
                    elif action not in prior.available_actions:
                        raise IntegrityError("unadmitted action")
                    if action.split_id != task.split_policy.development_id:
                        raise IntegrityError("unadmitted split")
            reserved.add(job_id)
        if e["event_type"] == "AGENT_COMPLETED":
            job_id = payload["job_id"]
            if job_id not in reserved or job_id in completed:
                raise IntegrityError("unreserved or duplicate agent completion")
            job = job_index[job_id]
            request = store.artifacts.get(job["request_ref"])
            receipt = store.artifacts.get(payload["receipt_ref"])
            if (
                not request.get("agent_call")
                or not job["finalized"]
                or receipt["request_ref"] != job["request_ref"]
                or receipt["job_id"] != job_id
                or receipt["world_id"] != manifest["world_id"]
                or job["receipt_ref"] != payload["receipt_ref"]
                or Cost.model_validate_json(job["actual"]) != Cost.model_validate(receipt["cost"])
            ):
                raise IntegrityError("agent receipt/usage settlement mismatch")
            if payload.get("turn_ref"):
                turn = ResearchTurn.model_validate(store.artifacts.get(payload["turn_ref"]))
                if digest(turn) != digest(receipt["answer"]):
                    raise IntegrityError("agent proposal differs from the observed provider answer")
                accepted_agents[job_id] = payload["turn_ref"]
                research_digests.add(digest(turn))
            completed.add(job_id)
        if e["event_type"] == "DECISION" and payload.get("agent_job_id"):
            agent_id = payload["agent_job_id"]
            if (
                accepted_agents.get(agent_id) != payload["research_turn_ref"]
                or agent_id in used_agents
            ):
                raise IntegrityError("terminal agent proposal unpaid, unknown or reused")
            turn = ResearchTurn.model_validate(store.artifacts.get(payload["research_turn_ref"]))
            prior = ResearchState.model_validate(payload["state"])
            decision = PolicyDecision.model_validate(payload["decision"])
            admit(decision, prior)
            if turn.state_hash != prior.identity or turn.experiment.template_id != "stop":
                raise IntegrityError("invalid terminal agent decision")
            used_agents.add(agent_id)
        if e["event_type"] in {"NODE_COMPLETED", "FORMAL_RESULT"} and "receipt_ref" in payload:
            receipt = store.artifacts.get(payload["receipt_ref"])
            job_id = receipt["job_id"]
            if job_id not in reserved or job_id in completed:
                raise IntegrityError("unreserved or duplicate result")
            job = job_index[job_id]
            request = store.artifacts.get(job["request_ref"])
            if (
                not job["finalized"]
                or job["receipt_ref"] != payload["receipt_ref"]
                or receipt["request_ref"] != job["request_ref"]
                or receipt["world_id"] != manifest["world_id"]
                or Cost.model_validate_json(job["actual"]) != Cost.model_validate(receipt["cost"])
            ):
                raise IntegrityError("receipt/job settlement mismatch")
            observation = Observation.model_validate(receipt["observation"])
            if store.artifacts.get(receipt["observation_ref"]) != observation.model_dump(
                mode="json"
            ):
                raise IntegrityError("observation hash mismatch")
            evaluation = Evaluation.model_validate(store.artifacts.get(payload["evaluation_ref"]))
            if evaluation.evaluator_id != world.evaluator_id or evaluation.evidence_refs != (
                receipt["observation_ref"],
            ):
                raise IntegrityError("invalid evaluator identity or evidence")
            if e["event_type"] == "NODE_COMPLETED":
                if payload.get("critique_ref"):
                    review = Critique.model_validate(store.artifacts.get(payload["critique_ref"]))
                    if review.evidence_refs != (payload["evaluation_ref"],):
                        raise IntegrityError("critique references unrelated evidence")
                if payload.get("finding_ref"):
                    finding = Finding.model_validate(store.artifacts.get(payload["finding_ref"]))
                    if (
                        finding.supporting_evidence != (payload["evaluation_ref"],)
                        or finding.scope != evaluation.scope
                    ):
                        raise IntegrityError("curated finding references incompatible evidence")
                if payload["parent_id"] != request["state"]["parent_node_id"]:
                    raise IntegrityError("invalid causal parent")
                if (
                    observation.action_id != request["action"]["action_id"]
                    or evaluation.action_id != observation.action_id
                ):
                    raise IntegrityError("action/observation/evaluation mismatch")
                visible.extend([receipt["observation_ref"], payload["evaluation_ref"]])
                observations.append(observation.model_dump(mode="json"))
                evaluations.append(evaluation.model_dump(mode="json"))
                parent = payload["node_id"]
            else:
                formal_count += 1
                if not frozen or formal_count > 1 or not request.get("formal"):
                    raise IntegrityError("invalid locked-test lifecycle")
            completed.add(job_id)
        snapshots[e["sequence"]] = (tuple(visible), tuple(observations), tuple(evaluations), parent)
        research_snapshots[e["sequence"]] = set(research_digests)
    if reserved != set(job_index) or completed != {
        j["id"] for j in job_index.values() if j["finalized"]
    }:
        raise IntegrityError("journal/job index mismatch")
    return {
        "status": "VERIFIED",
        "campaign_id": campaign_id,
        "world_id": world.identity,
        "events": len(events),
        "development_nodes": len(observations),
        "jobs": len(job_index),
        "formal_reveals": formal_count,
        "agent_completions": len(
            [
                j
                for j in job_index.values()
                if store.artifacts.get(j["request_ref"]).get("agent_call") and j["finalized"]
            ]
        ),
        "scope": world.scope,
        "usage": store.usage(campaign_id),
    }
