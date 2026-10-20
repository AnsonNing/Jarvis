"""Durable, evidence-directed continuation across finite reviewed research sessions."""

from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.campaign import source_identity
from jarvis.contracts import Budget, IntegrityError, Record
from jarvis.identity import digest
from jarvis.iris_lab_session import IrisLabSession, IrisLabSessionSpec
from jarvis.lab_contracts import LabSpec
from jarvis.research_lab import lab_report, payloads
from jarvis.research_roles import strict_schema
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


class MissionDecision(Record):
    context_id: str
    action: Literal["continue", "stop"]
    template_index: int = Field(ge=0)
    directions: tuple[str, ...] = Field(max_length=6)
    reason: str = Field(min_length=1, max_length=5000)
    evidence_refs: tuple[str, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def continuation(self) -> "MissionDecision":
        if self.action == "continue" and not self.directions:
            raise ValueError("continue requires explicit research directions")
        return self


class IrisMissionSpec(Record):
    kind: Literal["IrisMissionSpec"] = "IrisMissionSpec"
    initial: IrisLabSessionSpec
    continuation_templates: tuple[LabSpec, ...] = Field(min_length=1, max_length=8)
    controller: AgentConfig
    owner_instruction: str = Field(min_length=1, max_length=6000)
    budget: Budget
    predecessor_task: str | None = None

    @model_validator(mode="after")
    def controls(self) -> "IrisMissionSpec":
        for template in self.continuation_templates:
            if not any(
                template == stage
                for stage, phase in zip(self.initial.stages, self.initial.phases, strict=True)
                if phase == "development"
            ):
                raise ValueError("continuation templates must be declared development tasks")
        if (
            self.controller.model != self.initial.model
            or self.controller.reasoning_effort != self.initial.reasoning_effort
        ):
            raise ValueError("mission controller must use the same declared model and effort")
        return self


class IrisResearchMission:
    def __init__(self, store: Store, sessions: IrisLabSession | None = None):
        self.store, self.sessions = store, sessions or IrisLabSession(store)

    def create(self, spec: IrisMissionSpec, *, budget_owner: str | None = None) -> str:
        if spec.predecessor_task and any(
            self.store.resource_totals_tree(spec.predecessor_task)["pending"].values()
        ):
            raise IntegrityError("prior task has unsettled work")
        return self.store.create(
            {
                "kind": "IrisResearchMission",
                "spec": spec.model_dump(mode="json"),
                "world_id": digest(spec),
                "source_identity": source_identity(),
                "provider": self.sessions.lab.provider_factory(spec.controller).identity(),
                "task": {"budget": spec.budget.model_dump()},
                **({"budget_owner": budget_owner} if budget_owner else {}),
            }
        )

    def advance(self, owner: str) -> dict[str, Any]:
        with self.store.campaign_lock(owner, scope="iris-mission"):
            if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                return {"mission": owner, "status": self.store.get_control(owner)}
            m = self.store.manifest(owner)
            if m["source_identity"] != source_identity():
                raise IntegrityError("mission runtime changed")
            if self.store.status(owner) in {"CLOSED", "NEEDS_RECONCILIATION"}:
                return self.report(owner)
            spec = IrisMissionSpec.model_validate(m["spec"])
            waves = payloads(self.store, owner, "MISSION_SESSION_CREATED")
            if not waves:
                session = self.sessions.create(spec.initial, budget_owner=owner)
                self.store.append(
                    owner,
                    "MISSION_SESSION_CREATED",
                    {"index": 0, "session": session, "reason": "Owner-declared initial coverage"},
                )
                return {"mission": owner, "session": session, "status": "SESSION_CREATED"}
            session = waves[-1]["session"]
            if self.store.status(session) != "CLOSED":
                result = self.sessions.advance(session)
                status = result["status"]
                if status in {
                    "NEEDS_RECONCILIATION",
                    "SMOKE_NEEDS_ATTENTION",
                    "BUDGET_EXHAUSTED",
                    "LAB_NEEDS_ATTENTION",
                }:
                    self.store.set_status(owner, "NEEDS_RECONCILIATION")
                return {
                    **result,
                    "mission": owner,
                    "wave": len(waves) - 1,
                    **(
                        {"status": "SESSION_COMPLETED", "child_status": status}
                        if status == "CLOSED"
                        else {}
                    ),
                }
            index = len(waves)
            requested = next(
                (
                    p
                    for p in payloads(self.store, owner, "MISSION_REVIEW_REQUESTED")
                    if p["index"] == index
                ),
                None,
            )
            histories = []
            queues: list[str] = []
            experiences: list[str] = []
            for wave in waves:
                for link in payloads(self.store, wave["session"], "SESSION_STAGE_CREATED"):
                    report = lab_report(self.store, link["episode"])
                    histories.append(
                        {
                            "episode": link["episode"],
                            "program": report["program"],
                            "usage": report["full_usage"],
                        }
                    )
                    queues.extend(
                        p["queue_id"]
                        for p in payloads(
                            self.store,
                            payloads(self.store, link["episode"], "LAB_PROGRAM_CREATED")[0][
                                "program_id"
                            ],
                            "RESEARCH_QUEUE_ATTACHED",
                        )
                    )
                    experiences.extend(p["experience_ref"] for p in report["experience"])
            if requested is None:
                context = {
                    "goal": spec.owner_instruction,
                    "wave": index,
                    "histories": histories,
                    "visible_evidence_ids": list(
                        dict.fromkeys(
                            r["result_ref"] for h in histories for r in h["program"]["experiments"]
                        )
                    ),
                    "templates": [
                        {
                            "index": i,
                            "family": t.family,
                            "side": t.program.queue.environment.side,
                            "directions": t.program.directions,
                        }
                        for i, t in enumerate(spec.continuation_templates)
                    ],
                    "usage": self.store.resource_totals_tree(owner),
                    "boundary": "Choose useful next exposed-development research, or stop with specific evidence of exhausted directions/plateau or unavailable capability. A finite session ending, one negative candidate, or lack of independent Dream promotion data is not alone a task-wide stop. Preserve strict PL/PR/MDD criteria and all models/evaluator/budgets. Prefer minimal discriminating hypotheses and do not repeat candidates. No indefinite quota consumption without new value.",
                }
                schema = strict_schema(MissionDecision)
                schema["properties"]["context_id"]["enum"] = [digest(context)]
                schema["properties"]["template_index"]["maximum"] = (
                    len(spec.continuation_templates) - 1
                )
                schema["properties"]["evidence_refs"]["items"]["enum"] = context[
                    "visible_evidence_ids"
                ]
                requested = {
                    "index": index,
                    "context_ref": self.store.artifacts.put(context),
                    "schema_ref": self.store.artifacts.put(schema),
                }
                self.store.append(owner, "MISSION_REVIEW_REQUESTED", requested)
            provider = self.sessions.lab.provider_factory(spec.controller)
            if provider.identity() != m["provider"]:
                raise IntegrityError("mission controller provider changed")
            from jarvis.bounded_roles import evidence_role

            key = f"mission-review:{index}"
            answer = evidence_role(
                self.store,
                owner,
                key,
                "mission_controller",
                self.store.artifacts.get(requested["context_ref"]),
                MissionDecision,
                provider,
                spec.controller,
                "Decide the next research direction from completed measurements and independent critique. Distinguish stopping one batch from completing the mission; do not invent evidence or relax constraints. Retain all negative findings. Give a concrete stop reason when no useful admissible continuation exists.",
            )
            assert isinstance(answer, MissionDecision)
            paid = next(
                p
                for p in payloads(self.store, owner, "LAB_ROLE_RECORDED")
                if (p["key"] == key or p["key"].startswith(key + ":repair:"))
                and p["answer_ref"] == digest(answer)
            )
            if not any(
                p["index"] == index
                for p in payloads(self.store, owner, "MISSION_DECISION_RECORDED")
            ):
                self.store.append(
                    owner,
                    "MISSION_DECISION_RECORDED",
                    {
                        "index": index,
                        "answer_ref": self.store.artifacts.put(answer),
                        "receipt_ref": paid["receipt_ref"],
                        "context_ref": paid["context_ref"],
                    },
                )
            if answer.action == "stop":
                self.store.append(
                    owner, "MISSION_COMPLETED", {"reason": answer.reason, "policy_promotion": False}
                )
                self.store.set_status(owner, "CLOSED")
                return self.report(owner)
            if not answer.directions:
                raise IntegrityError("continuation needs explicit research directions")
            template = spec.continuation_templates[answer.template_index]
            compatible = [
                q
                for q in queues
                if self.store.manifest(q)["spec"]["environment"]
                == template.program.queue.environment.model_dump(mode="json")
            ]
            follow = template.model_copy(
                update={
                    "task_id": f"mission-wave-{index}-{template.family}"[:100],
                    "experience_refs": tuple(
                        dict.fromkeys([*template.experience_refs, *experiences])
                    )[-32:],
                    "program": template.program.model_copy(
                        update={
                            "directions": answer.directions,
                            "queue": template.program.queue.model_copy(
                                update={
                                    "prior_queues": tuple(
                                        dict.fromkeys(
                                            [*template.program.queue.prior_queues, *compatible]
                                        )
                                    )[-64:]
                                }
                            ),
                        }
                    ),
                }
            )
            next_spec = spec.initial.model_copy(
                update={
                    "stages": (follow,),
                    "phases": ("development",),
                    "prior_integration_queues": tuple(
                        q
                        for q in spec.initial.prior_integration_queues
                        if self.store.manifest(q)["spec"]["environment"]["side"]
                        == template.program.queue.environment.side
                    ),
                    "propose_capability": False,
                    "shadow_review": True,
                }
            )
            child = self.sessions.create(next_spec, budget_owner=owner)
            self.store.append(
                owner,
                "MISSION_SESSION_CREATED",
                {
                    "index": index,
                    "session": child,
                    "reason": answer.reason,
                    "decision_ref": self.store.artifacts.put(answer),
                },
            )
            return {
                "mission": owner,
                "session": child,
                "status": "CONTINUATION_CREATED",
                "wave": index,
            }

    def report(self, owner: str) -> dict[str, Any]:
        m = self.store.manifest(owner)
        if m["world_id"] != digest(m["spec"]):
            raise IntegrityError("mission declaration changed")
        links = payloads(self.store, owner, "MISSION_SESSION_CREATED")
        if [p["index"] for p in links] != list(range(len(links))):
            raise IntegrityError("mission session lineage changed")
        for link in links:
            if self.store.manifest(link["session"]).get("budget_owner") != owner:
                raise IntegrityError("session escaped the mission resource budget")
            self.sessions.report(link["session"])
        if self.store.status(owner) == "CLOSED" and (
            not links
            or any(self.store.status(p["session"]) != "CLOSED" for p in links)
            or any(self.store.resource_totals_tree(owner)["pending"].values())
            or not payloads(self.store, owner, "MISSION_COMPLETED")
        ):
            raise IntegrityError("closed mission has incomplete sessions or unknown work")
        return {
            "mission": owner,
            "status": self.store.status(owner),
            "sessions": links,
            "usage": self.store.resource_totals_tree(owner),
            "completion": payloads(self.store, owner, "MISSION_COMPLETED"),
        }
