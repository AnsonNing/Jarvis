"""Adaptive question/action loop on the existing roles, ledger, skills and backtester."""

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.bounded_roles import evidence_role
from jarvis.campaign import code_provenance, environment, source_identity
from jarvis.contracts import AdmissionError, Budget, BudgetExceeded, IntegrityError, Record
from jarvis.identity import digest
from jarvis.lab_contracts import ExpertOpinion
from jarvis.lab_tools import run_tool, verify_tools
from jarvis.providers import CodexProvider
from jarvis.research_contract import (
    ActionReview,
    BuildSkillAction,
    ConsultAction,
    DiagnoseAction,
    ExperimentAction,
    InspectAction,
    ResearchContract,
    ResearchDecision,
    SkillAgentVersion,
    StopAction,
)
from jarvis.research_lab import payloads
from jarvis.research_program import ResearchProgram, ResearchProgramSpec, program_report
from jarvis.research_roles import verify_roles
from jarvis.research_skills import SkillRegistry
from jarvis.researcher import AgentConfig
from jarvis.skill_builder import SkillBuilder, git
from jarvis.storage import Store
from jarvis.trading_diagnostics import DiagnosticInput, DiagnosticSnapshot, inventory


class MechanismResearchSpec(Record):
    kind: Literal["MechanismResearchSpec"] = "MechanismResearchSpec"
    contract_ref: str
    dataset_refs: tuple[str, ...] = Field(min_length=1, max_length=16)
    registry: str
    config: AgentConfig = AgentConfig(
        model="gpt-6.1-sol",
        reasoning_effort="high",
        max_calls=32,
        reserve_tokens=65536,
        timeout_seconds=300,
    )
    budget: Budget = Budget(
        max_actions=100,
        max_tool_calls=100,
        max_llm_tokens=2000000,
        max_wall_seconds=14400,
        max_compute_units=100,
        max_parallel_jobs=2,
    )
    program: ResearchProgramSpec | None = None
    builder_repository: str | None = None
    max_decisions: int = Field(default=20, ge=1, le=100)
    predecessor: str | None = None
    prior_evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def role_identity(self) -> "MechanismResearchSpec":
        if not self.config.model or not self.config.reasoning_effort:
            raise ValueError("all workflow roles require an explicit model and effort")
        if self.program:
            for role in (self.program.agent, self.program.critic):
                if role is None or (role.model, role.reasoning_effort) != (
                    self.config.model,
                    self.config.reasoning_effort,
                ):
                    raise ValueError(
                        "backtest Director/Critic must use the same declared model/effort"
                    )
        return self


def questions(store: Store, owner: str) -> list[dict[str, Any]]:
    values = [p["question"] for p in payloads(store, owner, "RESEARCH_QUESTION_OPENED")]
    return [
        {
            **q,
            "assessments": [
                a
                for p in payloads(store, owner, "RESEARCH_ACTION_REVIEWED")
                for a in store.artifacts.get(p["review_ref"])["assessments"]
                if a["hypothesis_id"] in {h["hypothesis_id"] for h in q["hypotheses"]}
            ],
        }
        for q in values
    ]


def owner_control(store: Store, owner: str) -> str | None:
    while owner:
        command = store.get_control(owner)
        if command in {"PAUSED", "CANCELLED"}:
            return command
        owner = store.manifest(owner).get("budget_owner", "")
    return None


def validate_experiment_contract(
    contract: ResearchContract, program: ResearchProgramSpec | None
) -> None:
    if program is None:
        return
    from jarvis.research_acceptance import ResearchAcceptance

    if program.acceptance != ResearchAcceptance.model_validate(contract.acceptance):
        raise AdmissionError("experiment acceptance must equal the frozen owner contract")
    env = program.queue.environment
    if env.result_origin != "SIMULATED_FIXTURE" and not contract.allow_observed_experiments:
        raise AdmissionError("observed backtests are not authorized by this contract")
    if env.evaluator_sha256 != contract.evaluator_id:
        raise AdmissionError("experiment evaluator mismatch")
    if contract.experiment_environment_id != digest(env):
        raise AdmissionError(
            "experiment environment must be explicitly pinned by the owner contract"
        )


def validate_decision(answer: Record, context: dict[str, Any]) -> None:
    assert isinstance(answer, ResearchDecision)
    action = answer.action
    visible = set(context["visible_evidence_ids"])
    known = {q["question_id"]: q for q in context["questions"]}
    if answer.question:
        q = answer.question
        old_hypotheses = {h["hypothesis_id"] for old in known.values() for h in old["hypotheses"]}
        if (
            q.question_id in known
            or (q.parent_id and q.parent_id not in known)
            or any(
                h.hypothesis_id in old_hypotheses or not set(h.evidence_refs) <= visible
                for h in q.hypotheses
            )
        ):
            raise ValueError("question identity, parent or evidence escaped the current tree")
        known[q.question_id] = q.model_dump(mode="json")
    if (
        action.question_id not in known
        or not set(action.evidence_refs) <= visible
        or not set(action.hypothesis_ids)
        <= {h["hypothesis_id"] for h in known[action.question_id]["hypotheses"]}
    ):
        raise ValueError("action must address declared competing hypotheses and visible evidence")
    inputs = (
        action.input_refs
        if isinstance(action, InspectAction)
        else (
            (action.baseline_ref, action.candidate_ref)
            if isinstance(action, DiagnoseAction)
            else ()
        )
    )
    if not set(inputs) <= set(context["dataset_refs"]):
        raise ValueError("action escaped admitted input snapshots")


class MechanismResearch:
    def __init__(
        self,
        store: Store,
        *,
        provider_factory: Any = CodexProvider,
        critic_factory: Any = None,
        builder_factory: Any = None,
        program: ResearchProgram | None = None,
    ):
        self.store = store
        self.provider_factory = provider_factory
        self.critic_factory = critic_factory or provider_factory
        self.builder_factory = builder_factory or provider_factory
        self.program = program or ResearchProgram(store)

    def create(self, spec: MechanismResearchSpec, *, budget_owner: str | None = None) -> str:
        contract = ResearchContract.model_validate(self.store.artifacts.get(spec.contract_ref))
        for ref in contract.baseline_refs:
            self.store.artifacts.get(ref)
        snapshots = [
            DiagnosticSnapshot.model_validate(self.store.artifacts.get(ref))
            for ref in spec.dataset_refs
        ]
        if any(
            s.dataset_id != contract.dataset_id
            or s.evaluator_id != contract.evaluator_id
            or s.exposure != contract.exposure
            for s in snapshots
        ):
            raise AdmissionError(
                "input snapshot identity or exposure escaped the research contract"
            )
        for ref in spec.prior_evidence_refs:
            self.store.artifacts.get(ref)
        if spec.predecessor:
            parent = self.store.manifest(spec.predecessor)
            if (
                parent["kind"] != "MechanismResearchSession"
                or parent["spec"]["contract_ref"] != spec.contract_ref
                or self.store.status(spec.predecessor) != "CLOSED"
                or any(self.store.resource_totals_tree(spec.predecessor)["pending"].values())
            ):
                raise AdmissionError(
                    "successor needs the same contract and a settled closed predecessor"
                )
        validate_experiment_contract(contract, spec.program)
        registry = SkillRegistry(self.store, spec.registry)
        snapshot = registry.snapshot()
        snapshot_ref = self.store.artifacts.put(snapshot)
        runtime_environment = environment()
        sandbox_identity = registry.sandbox.identity()
        provider = self.provider_factory(spec.config).identity()
        roles = {
            "director": provider,
            "critic": self.critic_factory(spec.config).identity(),
            "builder": self.builder_factory(spec.config).identity(),
        }
        previous_ref = (
            self.store.manifest(spec.predecessor)["agent_version_ref"] if spec.predecessor else None
        )
        version = SkillAgentVersion(
            contract_ref=spec.contract_ref,
            registry_snapshot_ref=snapshot_ref,
            skill_refs=tuple(snapshot["skills"]),
            predecessor_ref=previous_ref,
            provider_identity={
                **{k: digest(v) for k, v in roles.items()},
                "model": spec.config.model or "",
                "effort": spec.config.reasoning_effort or "",
            },
        )
        commit = (
            git(Path(spec.builder_repository), "rev-parse", "HEAD")
            if spec.builder_repository
            else None
        )
        return self.store.create(
            {
                "kind": "MechanismResearchSession",
                "spec": spec.model_dump(mode="json"),
                "world_id": digest(
                    {
                        "spec": spec.model_dump(mode="json"),
                        "agent_version": version.model_dump(mode="json"),
                        "builder_commit": commit,
                        "environment": runtime_environment,
                        "sandbox": sandbox_identity,
                    }
                ),
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
                "environment": runtime_environment,
                "sandbox": sandbox_identity,
                "agent_version_ref": self.store.artifacts.put(version),
                "providers": roles,
                "builder_commit": commit,
                "task": {"budget": spec.budget.model_dump()},
                **({"budget_owner": budget_owner} if budget_owner else {}),
            }
        )

    def context(self, owner: str) -> dict[str, Any]:
        m = self.store.manifest(owner)
        spec = MechanismResearchSpec.model_validate(m["spec"])
        version = SkillAgentVersion.model_validate(self.store.artifacts.get(m["agent_version_ref"]))
        outcomes = payloads(self.store, owner, "RESEARCH_ACTION_COMPLETED")
        contract = ResearchContract.model_validate(self.store.artifacts.get(spec.contract_ref))
        input_manifests = [
            {
                "ref": ref,
                **DiagnosticSnapshot.model_validate(self.store.artifacts.get(ref)).model_dump(
                    mode="json", exclude={"rows"}
                ),
                "available_rows": len(self.store.artifacts.get(ref)["rows"]),
            }
            for ref in spec.dataset_refs
        ]
        visible = list(
            dict.fromkeys(
                [
                    spec.contract_ref,
                    *contract.baseline_refs,
                    *spec.dataset_refs,
                    *spec.prior_evidence_refs,
                    *[o["result_ref"] for o in outcomes],
                ]
            )
        )
        return {
            "research_contract": contract.model_dump(mode="json"),
            "contract_ref": spec.contract_ref,
            "agent_version_ref": m["agent_version_ref"],
            "dataset_refs": list(spec.dataset_refs),
            "baseline_evidence": [
                {"ref": ref, "value": self.store.artifacts.get(ref)}
                for ref in contract.baseline_refs
            ],
            "input_manifests": input_manifests,
            "experiment_envelope": {
                "environment_id": digest(spec.program.queue.environment),
                "model_lineage": spec.program.queue.environment.model_lineage,
                "side": spec.program.queue.environment.side,
                "period": spec.program.queue.environment.period.model_dump(mode="json"),
                "accepted_inputs": spec.program.queue.environment.accepted_inputs,
                "declared_bindings": [
                    b.model_dump(mode="json", exclude={"region", "symbol"})
                    for b in spec.program.queue.environment.bindings
                ],
                "scope": "Declared adapter bindings; actual baseline values and capability checks remain mandatory in the reviewed experiment harness",
            }
            if spec.program
            else None,
            "domain": DiagnosticSnapshot.model_validate(
                self.store.artifacts.get(spec.dataset_refs[0])
            ).domain,
            "questions": questions(self.store, owner),
            "visible_evidence_ids": visible,
            "history": [
                {**o, "observation": self.store.artifacts.get(o["result_ref"])} for o in outcomes
            ],
            "prior_evidence": [
                {"ref": r, "value": self.store.artifacts.get(r)} for r in spec.prior_evidence_refs
            ],
            "skills": [
                {"ref": r, "manifest": self.store.artifacts.get(r)} for r in version.skill_refs
            ],
            "available_actions": [
                "INSPECT",
                "DIAGNOSE",
                "CONSULT",
                "BUILD_SKILL",
                "EXPERIMENT",
                "STOP",
            ],
            "experiment_available": spec.program is not None,
            "building_available": contract.allow_skill_building
            and spec.builder_repository is not None,
            "resources": self.store.resource_totals_tree(owner),
            "boundary": "Choose an informative next action; no backtest is required. Candidate adoption constraints do not prohibit negative diagnostic experiments. Missing observations remain unavailable. New registry entries require a successor AgentVersion.",
        }

    def _role(
        self,
        owner: str,
        key: str,
        role: str,
        context: dict[str, Any],
        model: type[Record],
        validator: Any = None,
    ) -> Record:
        spec = MechanismResearchSpec.model_validate(self.store.manifest(owner)["spec"])
        factory = self.critic_factory if role == "workflow_critic" else self.provider_factory
        identity = "critic" if role == "workflow_critic" else "director"
        provider = factory(spec.config)
        if provider.identity() != self.store.manifest(owner)["providers"][identity]:
            raise IntegrityError("workflow provider changed")
        return evidence_role(
            self.store,
            owner,
            key,
            role,
            context,
            model,
            provider,
            spec.config,
            "Preserve complete research intent and evidence scope; prefer discriminating observations over uninformative parameter enumeration.",
            validate=validator,
        )

    def advance(self, owner: str) -> dict[str, Any]:
        with self.store.campaign_lock(owner, scope="mechanism-research"):
            if owner_control(self.store, owner):
                return {"status": owner_control(self.store, owner)}
            if self.store.status(owner) in {"CLOSED", "BUDGET_EXHAUSTED", "NEEDS_RECONCILIATION"}:
                return {"status": self.store.status(owner)}
            m = self.store.manifest(owner)
            if m["source_identity"] != source_identity():
                raise IntegrityError("research runtime changed; declare a successor instead")
            if m["environment"] != environment():
                raise IntegrityError("research dependencies changed; declare a successor instead")
            spec = MechanismResearchSpec.model_validate(m["spec"])
            try:
                return self._advance(owner, spec)
            except BudgetExceeded as exc:
                self.store.append(owner, "RESEARCH_PARTIAL_STOP", {"reason": str(exc)})
                self.store.set_status(owner, "BUDGET_EXHAUSTED")
                return {"status": "BUDGET_EXHAUSTED"}

    def _advance(self, owner: str, spec: MechanismResearchSpec) -> dict[str, Any]:
        decisions = payloads(self.store, owner, "RESEARCH_ACTION_SELECTED")
        results = payloads(self.store, owner, "RESEARCH_ACTION_COMPLETED")
        reviews = payloads(self.store, owner, "RESEARCH_ACTION_REVIEWED")
        index = len(results)
        if len(reviews) < len(results):
            outcome = results[-1]
            context = {
                **self.context(owner),
                "action": self.store.artifacts.get(decisions[-1]["decision_ref"]),
                "result_ref": outcome["result_ref"],
                "result": self.store.artifacts.get(outcome["result_ref"]),
            }

            def check(value: Record, frozen: dict[str, Any]) -> None:
                assert isinstance(value, ActionReview)
                allowed = set(frozen["visible_evidence_ids"])
                hypothesis_ids = {
                    h["hypothesis_id"] for q in frozen["questions"] for h in q["hypotheses"]
                }
                if any(
                    a.hypothesis_id not in hypothesis_ids
                    or not set(a.evidence_refs) <= allowed
                    or frozen["result_ref"] not in a.evidence_refs
                    for a in value.assessments
                ):
                    raise ValueError(
                        "Critic assessment escaped the observed result or hypothesis tree"
                    )

            review = self._role(
                owner, f"review:{index - 1}", "workflow_critic", context, ActionReview, check
            )
            self.store.append(
                owner,
                "RESEARCH_ACTION_REVIEWED",
                {"index": index - 1, "review_ref": self.store.artifacts.put(review)},
            )
            last = ResearchDecision.model_validate(
                self.store.artifacts.get(decisions[-1]["decision_ref"])
            )
            if isinstance(last.action, StopAction) and last.action.scope == "session":
                self.store.append(
                    owner,
                    "RESEARCH_SESSION_STOPPED",
                    {
                        "conclusion": last.action.conclusion,
                        "reason": last.action.rationale,
                        "result_ref": outcome["result_ref"],
                    },
                )
                self.store.set_status(owner, "CLOSED")
                return {"status": "CLOSED"}
            return {"status": "REVIEWED", "result_ref": outcome["result_ref"]}
        if len(decisions) == len(results):
            if index >= spec.max_decisions:
                self.store.set_status(owner, "BUDGET_EXHAUSTED")
                return {"status": "BUDGET_EXHAUSTED", "reason": "declared decision bound"}
            context = self.context(owner)
            decision = self._role(
                owner,
                f"decision:{index}",
                "workflow_director",
                context,
                ResearchDecision,
                validate_decision,
            )
            assert isinstance(decision, ResearchDecision)
            if decision.question:
                existing = [
                    p["question"]
                    for p in payloads(self.store, owner, "RESEARCH_QUESTION_OPENED")
                    if p["question"]["question_id"] == decision.question.question_id
                ]
                if existing and existing[0] != decision.question.model_dump(mode="json"):
                    raise IntegrityError("question identity cannot change on resume")
                if not existing:
                    self.store.append(
                        owner,
                        "RESEARCH_QUESTION_OPENED",
                        {"question": decision.question.model_dump(mode="json")},
                    )
            role_record = [
                p
                for p in payloads(self.store, owner, "LAB_ROLE_RECORDED")
                if p["answer_ref"] == digest(decision)
            ][-1]
            self.store.append(
                owner,
                "RESEARCH_ACTION_SELECTED",
                {
                    "index": index,
                    "decision_ref": self.store.artifacts.put(decision),
                    "context_ref": role_record["context_ref"],
                },
            )
            return {"status": "ACTION_SELECTED", "action": decision.action.kind}
        selected = decisions[-1]
        decision = ResearchDecision.model_validate(
            self.store.artifacts.get(selected["decision_ref"])
        )
        value = self._execute(owner, spec, decision, selected["index"])
        if value.get("status") == "WAITING_FOR_EXPERIMENT":
            return value
        result_ref = self.store.artifacts.put(
            {
                "kind": "ResearchActionObservation",
                "action": decision.action.kind,
                "question_id": decision.action.question_id,
                "value": value,
                "agent_version_ref": self.store.manifest(owner)["agent_version_ref"],
            }
        )
        self.store.append(
            owner,
            "RESEARCH_ACTION_COMPLETED",
            {
                "index": selected["index"],
                "result_ref": result_ref,
                "decision_ref": selected["decision_ref"],
            },
        )
        return {
            "status": "ACTION_COMPLETED",
            "result_ref": result_ref,
            "action": decision.action.kind,
        }

    def _execute(
        self, owner: str, spec: MechanismResearchSpec, decision: ResearchDecision, index: int
    ) -> dict[str, Any]:
        action = decision.action
        context = self.context(owner)
        if isinstance(action, InspectAction):

            def inspect() -> dict[str, Any]:
                values = [
                    DiagnosticSnapshot.model_validate(self.store.artifacts.get(r))
                    for r in action.input_refs
                ]
                return {
                    "status": "OBSERVED",
                    "inventories": [
                        inventory(DiagnosticInput(baseline=v, candidate=v)).model_dump(mode="json")
                        for v in values
                    ],
                }

            return run_tool(
                self.store, owner, f"inspect:{index}", action.model_dump(mode="json"), inspect
            )
        if isinstance(action, DiagnoseAction):
            version = SkillAgentVersion.model_validate(
                self.store.artifacts.get(self.store.manifest(owner)["agent_version_ref"])
            )
            refs = [
                r
                for r in version.skill_refs
                if self.store.artifacts.get(r)["name"] == action.skill_name
            ]
            if not refs:
                return {
                    "status": "UNAVAILABLE",
                    "capability_gap": action.skill_name,
                    "reason": "No approved compatible skill in pinned AgentVersion",
                }
            data = DiagnosticInput(
                baseline=DiagnosticSnapshot.model_validate(
                    self.store.artifacts.get(action.baseline_ref)
                ),
                candidate=DiagnosticSnapshot.model_validate(
                    self.store.artifacts.get(action.candidate_ref)
                ),
            )
            return SkillRegistry(self.store, spec.registry).dispatch(owner, refs[-1], data)
        if isinstance(action, ConsultAction):
            opinion = self._role(
                owner,
                f"consult:{index}",
                "workflow_director",
                {**context, "expert": action.specialist, "question": action.question},
                ExpertOpinion,
            )
            return {
                "status": "ADVISORY",
                "expert": action.specialist,
                "opinion": opinion.model_dump(mode="json"),
                "measured_result": False,
            }
        if isinstance(action, BuildSkillAction):
            if not context["research_contract"]["allow_skill_building"]:
                return {"status": "UNAVAILABLE", "reason": "contract does not allow skill building"}
            if (
                self.builder_factory(spec.config).identity()
                != self.store.manifest(owner)["providers"]["builder"]
            ):
                raise IntegrityError("builder provider changed")
            return SkillBuilder(
                self.store, SkillRegistry(self.store, spec.registry), self.builder_factory
            ).build(
                owner,
                action,
                context,
                spec.config,
                Path(spec.builder_repository) if spec.builder_repository else None,
                self.store.manifest(owner)["builder_commit"],
            )
        if isinstance(action, ExperimentAction):
            if not spec.program:
                return {"status": "UNAVAILABLE", "reason": "no declared experiment adapter"}
            links = [
                p
                for p in payloads(self.store, owner, "WORKFLOW_PROGRAM_CREATED")
                if p["index"] == index
            ]
            if not links:
                program = self.program.create(
                    spec.program.model_copy(
                        update={
                            "directions": (action.direction,),
                            "research_contract_ref": spec.contract_ref,
                            "bounded_role_repairs": True,
                        }
                    ),
                    budget_owner=owner,
                )
                self.store.append(
                    owner, "WORKFLOW_PROGRAM_CREATED", {"index": index, "program": program}
                )
            else:
                program = links[0]["program"]
            result = self.program.advance(program)
            if result["status"] == "CLOSED":
                return {
                    "status": "OBSERVED",
                    "program": program,
                    "report": program_report(self.store, program),
                }
            if result["status"] in {
                "NEEDS_RECONCILIATION",
                "CRITIC_NEEDS_ATTENTION",
                "BUDGET_EXHAUSTED",
            }:
                self.store.set_status(owner, "NEEDS_RECONCILIATION")
            return {"status": "WAITING_FOR_EXPERIMENT", "program": program, "operation": result}
        assert isinstance(action, StopAction)
        return {
            "status": "STOPPED",
            "scope": action.scope,
            "conclusion": action.conclusion,
            "reason": action.rationale,
        }

    def successor(self, owner: str) -> str:
        with self.store.campaign_lock(owner, scope="workflow-successor"):
            return self._successor(owner)

    def _successor(self, owner: str) -> str:
        spec = MechanismResearchSpec.model_validate(self.store.manifest(owner)["spec"])
        if owner_control(self.store, owner):
            raise AdmissionError("owner stop cannot create a successor")
        prior = payloads(self.store, owner, "RESEARCH_SUCCESSOR_CREATED")
        if prior:
            return prior[0]["successor"]
        # Recover the durable child if interruption occurred just before the link event.
        children = [
            member
            for member in self.store.resource_members(owner)
            if member != owner
            and self.store.manifest(member).get("kind") == "MechanismResearchSession"
            and self.store.manifest(member)["spec"].get("predecessor") == owner
        ]
        if len(children) > 1:
            raise IntegrityError("multiple successors require reconciliation")
        if children:
            self.store.append(owner, "RESEARCH_SUCCESSOR_CREATED", {"successor": children[0]})
            return children[0]
        inherited = tuple(
            dict.fromkeys(
                [
                    *spec.prior_evidence_refs,
                    *[
                        p["result_ref"]
                        for p in payloads(self.store, owner, "RESEARCH_ACTION_COMPLETED")
                    ],
                ]
            )
        )
        child = self.create(
            spec.model_copy(update={"predecessor": owner, "prior_evidence_refs": inherited}),
            budget_owner=owner,
        )
        self.store.append(owner, "RESEARCH_SUCCESSOR_CREATED", {"successor": child})
        return child


def workflow_report(store: Store, owner: str) -> dict[str, Any]:
    store.events(owner)
    m = store.manifest(owner)
    spec = MechanismResearchSpec.model_validate(m["spec"])
    version = SkillAgentVersion.model_validate(store.artifacts.get(m["agent_version_ref"]))
    snapshot = store.artifacts.get(version.registry_snapshot_ref)
    SkillRegistry(store, spec.registry).verify_snapshot(snapshot)
    if tuple(snapshot["skills"]) != version.skill_refs or version.contract_ref != spec.contract_ref:
        raise IntegrityError("AgentVersion lost its pinned contract/registry")
    contract = ResearchContract.model_validate(store.artifacts.get(spec.contract_ref))
    visible = list(
        dict.fromkeys(
            [
                spec.contract_ref,
                *contract.baseline_refs,
                *spec.dataset_refs,
                *spec.prior_evidence_refs,
            ]
        )
    )
    role_requests = {}
    role_answers = {}
    for event in store.events(owner):
        p = event["payload"]
        if event["event_type"] == "LAB_ROLE_REQUESTED":
            context = store.artifacts.get(p["context_ref"])
            if (
                context.get("research_contract") != contract.model_dump(mode="json")
                or context["visible_evidence_ids"] != visible
            ):
                raise IntegrityError("role lost complete intent or used future evidence")
            role_requests[p["context_ref"]] = p
        elif event["event_type"] == "LAB_ROLE_RECORDED":
            if p["context_ref"] not in role_requests:
                raise IntegrityError("role answer precedes its request")
            role_answers[p["answer_ref"]] = p
        elif event["event_type"] == "RESEARCH_ACTION_SELECTED":
            if (
                p["decision_ref"] not in role_answers
                or role_answers[p["decision_ref"]]["context_ref"] != p["context_ref"]
            ):
                raise IntegrityError("decision lacks its causal role receipt")
        elif event["event_type"] == "RESEARCH_ACTION_COMPLETED":
            visible.append(p["result_ref"])
        elif event["event_type"] == "RESEARCH_ACTION_REVIEWED":
            if p["review_ref"] not in role_answers:
                raise IntegrityError("review lacks its separate role receipt")
    selected = payloads(store, owner, "RESEARCH_ACTION_SELECTED")
    for p in selected:
        context = store.artifacts.get(p["context_ref"])
        decision = ResearchDecision.model_validate(store.artifacts.get(p["decision_ref"]))
        if decision.context_id != digest(context):
            raise IntegrityError("decision context changed")
        validate_decision(decision, context)
    outcomes = payloads(store, owner, "RESEARCH_ACTION_COMPLETED")
    for value in outcomes:
        store.artifacts.get(value["result_ref"])
        if value["decision_ref"] != selected[value["index"]]["decision_ref"]:
            raise IntegrityError("action result lineage changed")
    for p in payloads(store, owner, "RESEARCH_ACTION_REVIEWED"):
        ActionReview.model_validate(store.artifacts.get(p["review_ref"]))
    verify_roles(store, owner)
    verify_tools(store, owner)
    for p in payloads(store, owner, "WORKFLOW_PROGRAM_CREATED"):
        program_report(store, p["program"])
    return {
        "status": store.status(owner),
        "owner": owner,
        "contract_ref": m["spec"]["contract_ref"],
        "agent_version_ref": m["agent_version_ref"],
        "questions": questions(store, owner),
        "decisions": selected,
        "outcomes": outcomes,
        "usage": store.resource_totals_tree(owner),
        "registry_setup_usage": store.resource_totals(spec.registry),
        "registry_setup_cost_scope": "Separate shared registry ledger; include its provisioning cost explicitly in any future utility comparison",
        "validation": "VERIFIED",
        "external_calls": 0,
        "boundary": "Descriptive evidence and fidelity do not establish causal mechanisms, skill utility or Dream policy gains",
    }


def replay_workflow(store: Store, owner: str, actions: list[str] | None = None) -> dict[str, Any]:
    report = workflow_report(store, owner)
    recorded = [p["decision_ref"] for p in report["decisions"]]
    requested = recorded if actions is None else actions
    for index, ref in enumerate(requested):
        if (
            index >= len(recorded)
            or recorded[index] != ref
            or index >= len(report["outcomes"])
            or report["outcomes"][index]["index"] != index
        ):
            return {"status": "INSUFFICIENT_COVERAGE", "at": index, "external_calls": 0}
    return {
        "status": "COMPLETE",
        "world_id": store.manifest(owner)["world_id"],
        "decisions": requested,
        "outcomes": report["outcomes"][: len(requested)],
        "historical_usage": report["usage"],
        "external_calls": 0,
    }
