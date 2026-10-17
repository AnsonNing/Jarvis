"""New online fixture executions, frozen candidate and one-shot locked evaluation."""

import time
from typing import Any

from jarvis.campaign import source_identity
from jarvis.contracts import Cost, IntegrityError, JarvisError
from jarvis.discovery import Collector, after_state, fits, root_state
from jarvis.exploration import ExplorationPolicy, choose
from jarvis.identity import digest
from jarvis.policy import admit
from jarvis.storage import Store
from jarvis.suite import SuiteAdapter


class FreshRunner:
    def __init__(self, store: Store):
        self.store = store

    def run(self, campaign: str, *, fault: str | None = None) -> str:
        with self.store.campaign_lock(campaign):
            manifest = self.store.manifest(campaign)
            if (
                manifest["kind"] != "FreshPolicyCampaign"
                or manifest["source_identity"] != source_identity()
            ):
                raise IntegrityError("fresh runtime/source mismatch")
            policy = ExplorationPolicy.model_validate(manifest["policy"])
            if policy.identity != manifest["policy_id"]:
                raise IntegrityError("fresh policy/compiler changed")
            prior = [e for e in self.store.events(campaign) if e["event_type"] == "FRESH_RESULT"]
            if prior:
                ref = str(prior[0]["payload"]["result_ref"])
                verify_fresh(self.store, ref)
                return ref
            collector = Collector(self.store)
            for job in self.store.jobs(campaign):
                if job["finalized"] or self.store.artifacts.get(job["request_ref"]).get("formal"):
                    continue
                if self.store.read_receipt(job["id"]) is not None:
                    collector._settle(campaign, job)
                elif job["status"] == "QUEUED":
                    collector._execute(campaign, job, fault)
                else:
                    raise JarvisError("unknown fresh job; reconciliation required")
            while not any(e["event_type"] == "FRESH_FROZEN" for e in self.store.events(campaign)):
                events = [
                    e for e in self.store.events(campaign) if e["event_type"] == "NODE_COMPLETED"
                ]
                state = (
                    after_state(self.store, campaign, events[-1]["payload"], events[-1]["sequence"])
                    if events
                    else root_state(self.store, campaign)
                )
                decision = choose(policy, state, 0.9)
                admit(decision, state)
                action = next(
                    a for a in state.available_actions if a.action_id == decision.action_id
                )
                if action.action_type == "STOP" or not fits(state, action.estimated_cost):
                    scored = [
                        o
                        for o in state.observations
                        if o.status == "SUCCEEDED" and "objective_score" in o.metrics
                    ]
                    best = (
                        max(scored, key=lambda o: o.metrics["objective_score"]) if scored else None
                    )
                    self.store.append(
                        campaign,
                        "FRESH_FROZEN",
                        {
                            "candidate": best.action_id if best else None,
                            "development_value": best.metrics["objective_score"] if best else None,
                            "state_ref": self.store.artifacts.put(state),
                            "termination": "STOP"
                            if action.action_type == "STOP"
                            else "BUDGET_EXHAUSTED",
                        },
                    )
                    if fault == "after_freeze":
                        raise JarvisError("injected fresh interruption after freeze")
                    break
                request = {
                    "before_ref": self.store.artifacts.put(state),
                    "state": state.model_dump(mode="json"),
                    "world_id": manifest["world_id"],
                    "action": action.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                }
                jid = self.store.reserve(campaign, request, action.estimated_cost)
                job = next(j for j in self.store.jobs(campaign) if j["id"] == jid)
                collector._execute(campaign, job, fault)
            frozen = next(
                e for e in self.store.events(campaign) if e["event_type"] == "FRESH_FROZEN"
            )
            candidate = frozen["payload"]["candidate"]
            result_events = [
                e for e in self.store.events(campaign) if e["event_type"] == "LOCKED_COMPLETED"
            ]
            if candidate and not result_events:
                jobs = [
                    j
                    for j in self.store.jobs(campaign)
                    if self.store.artifacts.get(j["request_ref"]).get("formal")
                ]
                if jobs:
                    job = jobs[0]
                else:
                    jid = self.store.reserve(
                        campaign,
                        {
                            "formal": True,
                            "candidate": candidate,
                            "frozen_event": frozen["event_hash"],
                            "world_id": manifest["world_id"],
                        },
                        Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=1),
                    )
                    job = next(j for j in self.store.jobs(campaign) if j["id"] == jid)
                receipt = self.store.read_receipt(job["id"])
                if receipt is None:
                    if job["status"] != "QUEUED":
                        raise JarvisError("unknown locked evaluation; reconciliation required")
                    self.store.start_job(job["id"])
                    from jarvis.suite import Fixture

                    started = time.monotonic()
                    obs = SuiteAdapter(Fixture.model_validate(manifest["fixture"])).execute(
                        candidate, formal=True
                    )
                    receipt = {
                        "job_id": job["id"],
                        "request_ref": job["request_ref"],
                        "world_id": manifest["world_id"],
                        "observation": obs.model_dump(mode="json"),
                        "observation_ref": self.store.artifacts.put(obs),
                        "cost": Cost(
                            actions=1,
                            tool_calls=1,
                            compute_units=1,
                            wall_seconds=time.monotonic() - started,
                        ).model_dump(mode="json"),
                    }
                    self.store.write_receipt(job["id"], receipt)
                    if fault == "formal_after_receipt":
                        raise JarvisError("injected formal receipt interruption")
                self.store.finish_auxiliary_job(
                    job["id"], "LOCKED_COMPLETED", {"scope": "locked_synthetic"}
                )
                result_events = [
                    e for e in self.store.events(campaign) if e["event_type"] == "LOCKED_COMPLETED"
                ]
            formal_ref = result_events[0]["payload"]["receipt_ref"] if result_events else None
            formal = self.store.artifacts.get(formal_ref) if formal_ref else None
            value = formal["observation"]["metrics"].get("objective_score") if formal else None
            result = {
                "kind": "FreshPolicyResult",
                "scope": "FRESH_SYNTHETIC_ONLY",
                "campaign_id": campaign,
                "fixture": manifest["fixture"],
                "policy_id": policy.identity,
                "candidate": candidate,
                "locked_receipt_ref": formal_ref,
                "value": value,
                "success": bool(value is not None and value >= 0.9),
                "formal_status": "EVALUATED" if formal else "NOT_RUN",
                "cost": self.store.resource_totals(campaign)["actual"],
            }
            ref = self.store.artifacts.put(result)
            self.store.append(campaign, "FRESH_RESULT", {"result_ref": ref})
            self.store.set_status(campaign, "CLOSED")
            verify_fresh(self.store, ref)
            return ref


def verify_fresh(store: Store, ref: str) -> dict[str, Any]:
    result = store.artifacts.get(ref)
    campaign = result["campaign_id"]
    manifest = store.manifest(campaign)
    policy = ExplorationPolicy.model_validate(manifest["policy"])
    if (
        policy.identity != result["policy_id"]
        or policy.identity != manifest["policy_id"]
        or result["fixture"] != manifest["fixture"]
    ):
        raise IntegrityError("fresh result policy/fixture mismatch")
    state = root_state(store, campaign)
    frozen = None
    formal = None
    jobs = {j["id"]: j for j in store.jobs(campaign)}
    seen = set()
    for event in store.events(campaign):
        payload = event["payload"]
        if event["event_type"] == "NODE_COMPLETED":
            request = store.artifacts.get(payload["request_ref"])
            receipt = store.artifacts.get(payload["receipt_ref"])
            if (
                frozen
                or request["state"] != state.model_dump(mode="json")
                or request["decision"] != choose(policy, state, 0.9).model_dump(mode="json")
            ):
                raise IntegrityError("fresh decision contains hindsight or changed policy")
            action = next(
                a
                for a in state.available_actions
                if a.action_id == request["decision"]["action_id"]
            )
            if request["action"] != action.model_dump(mode="json") or not fits(
                state, action.estimated_cost
            ):
                raise IntegrityError("fresh action not admitted")
            if (
                receipt["observation"] != store.artifacts.get(receipt["observation_ref"])
                or receipt["observation"]["action_id"] != action.action_id
            ):
                raise IntegrityError("fresh observation mismatch")
            seen.add(receipt["job_id"])
            state = after_state(store, campaign, payload, event["sequence"])
        if event["event_type"] == "FRESH_FROZEN":
            if frozen or store.artifacts.get(payload["state_ref"]) != state.model_dump(mode="json"):
                raise IntegrityError("invalid fresh candidate freeze")
            scored = [
                o
                for o in state.observations
                if o.status == "SUCCEEDED" and "objective_score" in o.metrics
            ]
            best = (
                max(scored, key=lambda o: o.metrics["objective_score"]).action_id
                if scored
                else None
            )
            if best != payload["candidate"] or best != result["candidate"]:
                raise IntegrityError("fresh best candidate mismatch")
            frozen = event
        if event["event_type"] == "LOCKED_COMPLETED":
            if (
                frozen is None
                or formal is not None
                or payload["receipt_ref"] != result["locked_receipt_ref"]
            ):
                raise IntegrityError("locked result before freeze or duplicate reveal")
            formal = store.artifacts.get(payload["receipt_ref"])
            request = store.artifacts.get(payload["request_ref"])
            if (
                request["candidate"] != result["candidate"]
                or request["frozen_event"] != frozen["event_hash"]
                or formal["observation"] != store.artifacts.get(formal["observation_ref"])
            ):
                raise IntegrityError("locked candidate or evidence mismatch")
            seen.add(formal["job_id"])
    if (
        not frozen
        or seen != set(jobs)
        or any(not j["finalized"] for j in jobs.values())
        or result["cost"] != store.resource_totals(campaign)["actual"]
    ):
        raise IntegrityError("fresh evidence/job ledger mismatch")
    for job in jobs.values():
        receipt = store.artifacts.get(job["receipt_ref"])
        if (
            receipt["job_id"] != job["id"]
            or receipt["request_ref"] != job["request_ref"]
            or receipt["world_id"] != manifest["world_id"]
            or Cost.model_validate_json(job["actual"]) != Cost.model_validate(receipt["cost"])
        ):
            raise IntegrityError("fresh settled receipt mismatch")
    value = formal["observation"]["metrics"].get("objective_score") if formal else None
    if result["value"] != value or result["success"] != bool(value is not None and value >= 0.9):
        raise IntegrityError("fresh outcome differs from locked evidence")
    if digest(result) != ref:
        raise IntegrityError("invalid fresh result reference")
    return result
