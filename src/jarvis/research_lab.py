"""On-demand specialists -> causal support -> existing Director/Critic program."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

from jarvis.campaign import source_identity
from jarvis.contracts import AdmissionError, BudgetExceeded, IntegrityError, JarvisError, Record
from jarvis.identity import digest
from jarvis.iris import IrisTransport, LocalIrisTransport
from jarvis.lab_audit import audit_scope, audited
from jarvis.lab_contracts import (
    CapabilityChoice,
    CapabilityProposal,
    ExperienceProposal,
    ExpertOpinion,
    HumanInput,
    LabSpec,
    ResearchSupport,
    SourceDocument,
    TeamPlan,
)
from jarvis.lab_memory import (
    capability_definition,
    create_capability,
    make_experience,
    pin_board,
    retrieve,
)
from jarvis.lab_tools import (
    CrossrefSearch,
    GitHubImplementationSearch,
    SourceSearch,
    execute_capability,
    query_documents,
    run_tool,
    verify_tools,
)
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.research_program import ResearchProgram, ResearchProgramSpec
from jarvis.research_program import program_report as raw_program_report
from jarvis.research_roles import RoleCaller, strict_schema, verify_roles
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


def payloads(store: Store, owner: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in store.events(owner) if e["event_type"] == kind]


def program_report(store: Store, owner: str) -> dict[str, Any]:
    return audited(store, "program", owner, lambda: raw_program_report(store, owner))


class ResearchLab:
    def __init__(
        self,
        store: Store,
        *,
        provider_factory: Callable[[AgentConfig], ResearchProvider] = CodexProvider,
        critic_provider_factory: Callable[[AgentConfig], ResearchProvider] | None = None,
        transport_factory: Callable[..., IrisTransport] = LocalIrisTransport,
        search: SourceSearch | None = None,
    ):
        self.store, self.provider_factory = store, provider_factory
        self.program = ResearchProgram(
            store,
            provider_factory=provider_factory,
            critic_provider_factory=critic_provider_factory,
            transport_factory=transport_factory,
        )
        self.search = search or CrossrefSearch()
        self._injected_search = search is not None

    def search_for(self, spec: LabSpec) -> SourceSearch:
        return (
            GitHubImplementationSearch()
            if not self._injected_search and spec.source_backend == "github"
            else self.search
        )

    def create(self, spec: LabSpec, *, budget_owner: str | None = None) -> str:
        spec = pin_board(self.store, spec)
        for ref in spec.source_refs:
            SourceDocument.model_validate(self.store.artifacts.get(ref))
        for ref in spec.human_refs:
            HumanInput.model_validate(self.store.artifacts.get(ref))
        raw = spec.model_dump(mode="json")
        provider = self.provider_factory(spec.role_config).identity()
        research_providers = {
            "director": self.program.provider_factory(spec.program.agent).identity(),
            "critic": self.program.critic_provider_factory(spec.program.critic).identity()
            if spec.program.critic
            else None,
        }
        if spec.adoption_study:
            from jarvis.lab_evolution import adopt_profile

            if (
                adopt_profile(self.store, spec, spec.adoption_study) != spec
                or self.store.manifest(spec.adoption_study)["provider"] != provider
                or self.store.manifest(spec.adoption_study)["research_providers"]
                != research_providers
            ):
                raise IntegrityError("adopted profile provider/version changed")
        declaration = {
            "spec": raw,
            "provider": provider,
            "search": self.search_for(spec).identity(),
            "research_providers": research_providers,
        }
        return self.store.create(
            {
                "kind": "ResearchLabEpisode",
                **declaration,
                **({"budget_owner": budget_owner} if budget_owner else {}),
                "world_id": digest(declaration),
                "source_identity": source_identity(),
                "task": {"budget": spec.budget.model_dump()},
            }
        )

    def call(
        self, owner: str, key: str, role: str, context: dict[str, Any], model: type[Record]
    ) -> Record:
        spec = LabSpec.model_validate(self.store.manifest(owner)["spec"])
        known = next(
            (p for p in payloads(self.store, owner, "LAB_ROLE_RECORDED") if p["key"] == key), None
        )
        if known:
            return model.model_validate(self.store.artifacts.get(known["answer_ref"]))
        requests = [p for p in payloads(self.store, owner, "LAB_ROLE_REQUESTED") if p["key"] == key]
        schema = strict_schema(model)
        schema["properties"]["context_id"]["enum"] = [digest(context)]
        if not requests:
            self.store.append(
                owner,
                "LAB_ROLE_REQUESTED",
                {
                    "key": key,
                    "role": role,
                    "context_ref": self.store.artifacts.put(context),
                    "schema_ref": self.store.artifacts.put(schema),
                },
            )
            request = {"context_ref": digest(context), "schema_ref": digest(schema)}
        else:
            request = requests[0]
        frozen = self.store.artifacts.get(request["context_ref"])
        provider = self.provider_factory(spec.role_config)
        if provider.identity() != self.store.manifest(owner)["provider"]:
            raise IntegrityError("lab role provider changed")
        response = RoleCaller(self.store, provider, spec.role_config).call(
            owner,
            role,
            frozen,
            self.store.artifacts.get(request["schema_ref"]),
            role_budget=True,
            guidance="Act as the declared "
            + role
            + ". "
            + spec.method.specialist_guidance
            + " Give concise evidence-linked conclusions and counterarguments, not hidden reasoning. Do not output trading settings unless the schema requests them.",
        )
        answer = model.model_validate(response["answer"])
        if answer.context_id != digest(frozen):  # type: ignore[attr-defined]
            raise AdmissionError("stale specialist context")
        if isinstance(answer, ExpertOpinion) and not set(answer.evidence_refs) <= set(
            frozen["visible_evidence_ids"]
        ):
            raise AdmissionError("specialist used unavailable evidence")
        self.store.append(
            owner,
            "LAB_ROLE_RECORDED",
            {
                "key": key,
                "role": role,
                "answer_ref": self.store.artifacts.put(answer),
                "context_ref": request["context_ref"],
                "receipt_ref": response["receipt_ref"],
            },
        )
        return answer

    def root_context(self, owner: str) -> dict[str, Any]:
        spec = LabSpec.model_validate(self.store.manifest(owner)["spec"])
        return {
            "task_id": spec.task_id,
            "family": spec.family,
            "goal": spec.program.queue.goal,
            "method": spec.method.model_dump(mode="json"),
            "visible_evidence_ids": [*spec.source_refs, *spec.human_refs],
            "evidence": [
                {"ref": r, "value": self.store.artifacts.get(r)}
                for r in [*spec.source_refs, *spec.human_refs]
            ],
            "allowed_roles": list(spec.method.specialist_roles),
            "scope": spec.program.queue.environment.period.exposure,
        }

    def advance(self, owner: str) -> dict[str, Any]:
        with self.store.campaign_lock(owner, scope="research-lab"):
            if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                return {"status": self.store.get_control(owner), "episode": owner}
            if self.store.status(owner) in {
                "CLOSED",
                "NEEDS_RECONCILIATION",
                "BUDGET_EXHAUSTED",
                "LAB_NEEDS_ATTENTION",
            }:
                return lab_report(self.store, owner)
            if self.store.manifest(owner)["source_identity"] != source_identity():
                raise IntegrityError("research lab runtime changed")
            try:
                return self._advance(owner)
            except BudgetExceeded:
                self.store.set_status(owner, "BUDGET_EXHAUSTED")
                return {"status": "BUDGET_EXHAUSTED", "episode": owner}
            except (JarvisError, ValueError):
                if self.store.status(owner) != "NEEDS_RECONCILIATION":
                    self.store.set_status(owner, "LAB_NEEDS_ATTENTION")
                raise

    def _advance(self, owner: str) -> dict[str, Any]:
        spec = LabSpec.model_validate(self.store.manifest(owner)["spec"])
        context = self.root_context(owner)
        stages = payloads(self.store, owner, "LAB_ACTION_COMPLETED")
        for action in spec.policy.actions:
            if any(p["action"] == action for p in stages):
                continue
            value: Any
            if action == "retrieve":
                value = run_tool(
                    self.store,
                    owner,
                    action,
                    {"refs": spec.experience_refs},
                    lambda: retrieve(
                        self.store, spec, before=self.store.manifest(owner)["created_at"]
                    ),
                )
            elif action == "search":
                docs = [
                    SourceDocument.model_validate(self.store.artifacts.get(r))
                    for r in spec.source_refs
                ]
                if spec.allow_external_search:
                    search = self.search_for(spec)
                    if search.identity() != self.store.manifest(owner)["search"]:
                        raise IntegrityError("source adapter changed")
                    raw = run_tool(
                        self.store,
                        owner,
                        action,
                        {
                            "public_question": spec.public_search_question,
                            "adapter": search.identity(),
                        },
                        partial(query_documents, search, spec.public_search_question or ""),
                    )
                    docs.extend(SourceDocument.model_validate(s) for s in raw)
                value = {
                    "documents": [
                        {"ref": self.store.artifacts.put(d), "document": d.model_dump(mode="json")}
                        for d in docs
                    ],
                    "read_boundary": "metadata/abstract/full-text tagged per document; no applicability verdict inferred",
                }
            elif action == "consult":
                preparation = self.observations(owner)
                consultation = {
                    **context,
                    "preparation": preparation,
                    "visible_evidence_ids": [
                        *context["visible_evidence_ids"],
                        *[row["ref"] for row in preparation],
                    ],
                }
                plan = self.call(owner, "team-plan", "team_director", consultation, TeamPlan)
                assert isinstance(plan, TeamPlan)
                if len(set(plan.roles)) != len(plan.roles) or not set(plan.roles) <= set(
                    spec.method.specialist_roles
                ):
                    raise AdmissionError("team escaped declared role registry")
                # Freeze the SAME initial evidence before parallel specialists start.
                with ThreadPoolExecutor(
                    max_workers=max(1, min(len(plan.roles), spec.budget.max_parallel_jobs))
                ) as pool:
                    futures = [
                        pool.submit(
                            self.call,
                            owner,
                            "initial:" + r,
                            r,
                            {**consultation, "phase": "independent", "expert": r},
                            ExpertOpinion,
                        )
                        for r in plan.roles
                    ]
                    opinions = [f.result().model_dump(mode="json") for f in futures]
                value = {
                    "roles": list(plan.roles),
                    "opinions": opinions,
                    "independent_initial_context": digest(consultation),
                }
            elif action == "discuss":
                # Focused question/answer exchange; bounded to one follow-up per role.
                collected = self.observations(owner)
                initial = next(v["value"] for v in collected if v["action"] == "consult")
                roles = initial["roles"]
                requests = {
                    r: [o for o in initial["opinions"] if o["question_for"] == r] for r in roles
                }
                refs = [v["ref"] for v in collected]
                replies = [
                    self.call(
                        owner,
                        "discussion:" + r,
                        r,
                        {
                            **context,
                            "phase": "discussion",
                            "expert": r,
                            "questions": requests[r],
                            "observations": collected,
                            "visible_evidence_ids": [*context["visible_evidence_ids"], *refs],
                        },
                        ExpertOpinion,
                    ).model_dump(mode="json")
                    for r in roles
                    if requests[r]
                ]
                value = {"replies": replies, "unresolved_disagreement_retained": True}
            elif action == "diagnose":
                if spec.capability_ref and spec.diagnostic_groups:
                    definition = capability_definition(self.store, spec.capability_ref)
                    choice = self.call(
                        owner,
                        "capability-choice",
                        "capability_selector",
                        {
                            **context,
                            "available_capabilities": [
                                {
                                    "ref": spec.capability_ref,
                                    "definition": definition.model_dump(mode="json"),
                                    "status": "EXPERIMENTAL_OR_PROFILE_VALIDATED",
                                }
                            ],
                            "input_groups": list(spec.diagnostic_groups),
                        },
                        CapabilityChoice,
                    )
                    assert isinstance(choice, CapabilityChoice)
                    if choice.capability_ref not in {None, spec.capability_ref}:
                        raise AdmissionError("capability selector requested unavailable tool")
                    if choice.capability_ref is None:
                        value = {"status": "NOT_SELECTED", "reason": choice.reason}
                        self.store.append(
                            owner,
                            "LAB_ACTION_COMPLETED",
                            {
                                "action": action,
                                "value_ref": self.store.artifacts.put(value),
                                "root_context_id": digest(context),
                            },
                        )
                        return {
                            "status": "LAB_ACTION_COMPLETED",
                            "action": action,
                            "episode": owner,
                        }
                    value = run_tool(
                        self.store,
                        owner,
                        action,
                        {"capability_ref": spec.capability_ref, "groups": spec.diagnostic_groups},
                        partial(execute_capability, definition, spec.diagnostic_groups),
                    )
                else:
                    value = {
                        "status": "REQUIRES_CAPABILITY"
                        if spec.diagnostic_groups
                        else "NOT_REQUESTED"
                    }
            else:
                raise AdmissionError("unknown lab action")
            self.store.append(
                owner,
                "LAB_ACTION_COMPLETED",
                {
                    "action": action,
                    "value_ref": self.store.artifacts.put(value),
                    "root_context_id": digest(context),
                },
            )
            return {"status": "LAB_ACTION_COMPLETED", "action": action, "episode": owner}
        support = payloads(self.store, owner, "LAB_SUPPORT_FROZEN")
        if not support:
            observations = self.observations(owner)
            package = ResearchSupport(
                owner=owner,
                method=spec.method,
                evidence_refs=tuple(
                    [*context["visible_evidence_ids"], *[o["ref"] for o in observations]]
                ),
                observations=tuple(observations),
            )
            self.store.append(
                owner, "LAB_SUPPORT_FROZEN", {"support_ref": self.store.artifacts.put(package)}
            )
        ref = payloads(self.store, owner, "LAB_SUPPORT_FROZEN")[0]["support_ref"]
        links = payloads(self.store, owner, "LAB_PROGRAM_CREATED")
        if not links:
            current = {
                "director": self.program.provider_factory(spec.program.agent).identity(),
                "critic": self.program.critic_provider_factory(spec.program.critic).identity()
                if spec.program.critic
                else None,
            }
            if current != self.store.manifest(owner)["research_providers"]:
                raise IntegrityError("program provider changed since lab declaration")
            pid = self.program.create(
                spec.program.model_copy(update={"support_ref": ref}), budget_owner=owner
            )
            self.store.append(owner, "LAB_PROGRAM_CREATED", {"program_id": pid, "support_ref": ref})
        pid = payloads(self.store, owner, "LAB_PROGRAM_CREATED")[0]["program_id"]
        status = self.program.advance(pid)["status"]
        if status != "CLOSED":
            if status in {
                "NEEDS_RECONCILIATION",
                "CRITIC_NEEDS_ATTENTION",
                "BUDGET_EXHAUSTED",
                "PAUSED",
                "CANCELLED",
            }:
                raise JarvisError("child research requires attention: " + status)
            return {"status": status, "episode": owner, "program_id": pid}
        report = program_report(self.store, pid)
        if not payloads(self.store, owner, "LAB_EXPERIENCE_CREATED"):
            actual_refs = [r["result_ref"] for r in report["experiments"]]
            proposal = self.call(
                owner,
                "curate",
                "curator",
                {
                    **context,
                    "visible_evidence_ids": actual_refs,
                    "experiments": report["experiments"],
                    "baseline_comparisons": report["baseline_comparisons"],
                },
                ExperienceProposal,
            )
            assert isinstance(proposal, ExperienceProposal)
            exp_ref = make_experience(self.store, owner, pid, proposal)
            self.store.append(owner, "LAB_EXPERIENCE_CREATED", {"experience_ref": exp_ref})
        self.store.set_status(owner, "CLOSED")
        return lab_report(self.store, owner)

    def observations(self, owner: str) -> list[dict[str, Any]]:
        return [
            {
                "action": p["action"],
                "ref": p["value_ref"],
                "value": self.store.artifacts.get(p["value_ref"]),
            }
            for p in payloads(self.store, owner, "LAB_ACTION_COMPLETED")
        ]

    def resume_receipts(self, owner: str) -> dict[str, Any]:
        """Reconcile only known receipts; never clear unknown work or owner stops."""
        with self.store.campaign_lock(owner, scope="research-lab"):
            if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                raise JarvisError("owner stop requires explicit control change")
            pending = [
                j
                for member in self.store.resource_members(owner)
                for j in self.store.jobs(member)
                if not j["finalized"]
            ]
            if not pending or any(
                not (self.store.read_receipt(j["id"]) or {}).get("cost") for j in pending
            ):
                raise JarvisError("no completely known receipts; reconciliation required")
            self.store.append(owner, "LAB_RECEIPT_RESUME", {"jobs": [j["id"] for j in pending]})
            self.store.set_status(owner, "READY")
        return self.advance(owner)

    def propose_capability(self, owner: str) -> str:
        with self.store.campaign_lock(owner, scope="research-lab"):
            if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                raise JarvisError("owner stop forbids capability proposal")
            if self.store.status(owner) in {
                "NEEDS_RECONCILIATION",
                "BUDGET_EXHAUSTED",
                "LAB_NEEDS_ATTENTION",
            }:
                raise JarvisError("resolve episode attention before capability proposal")
            if self.store.manifest(owner)["source_identity"] != source_identity():
                raise IntegrityError("research lab runtime changed")
            return self._propose_capability(owner)

    def _propose_capability(self, owner: str) -> str:
        spec = LabSpec.model_validate(self.store.manifest(owner)["spec"])
        context = {
            **self.root_context(owner),
            "capability_boundary": "Only group_mean_argmax closed diagnostic recipe; no generated code, imports or evaluator changes",
        }
        proposal = self.call(
            owner, "capability-proposal", "capability_developer", context, CapabilityProposal
        )
        assert isinstance(proposal, CapabilityProposal)
        if not set(proposal.capability.source_refs) <= set(spec.source_refs):
            raise AdmissionError("capability escaped declared source evidence")
        ref = create_capability(self.store, proposal.capability, owner)
        if not payloads(self.store, owner, "LAB_CAPABILITY_PROPOSED"):
            self.store.append(owner, "LAB_CAPABILITY_PROPOSED", {"capability_ref": ref})
        return ref


def lab_report(store: Store, owner: str) -> dict[str, Any]:
    with audit_scope():
        return audited(store, "episode", owner, lambda: _lab_report(store, owner))


def _lab_report(store: Store, owner: str) -> dict[str, Any]:
    verification = verify_lab(store, owner)
    programs = payloads(store, owner, "LAB_PROGRAM_CREATED")
    report = program_report(store, programs[0]["program_id"]) if programs else None
    return {
        "kind": "ResearchLabReport",
        "episode": owner,
        "status": store.status(owner),
        "verification": verification,
        "actions": payloads(store, owner, "LAB_ACTION_COMPLETED"),
        "opinions": payloads(store, owner, "LAB_ROLE_RECORDED"),
        "program": report,
        "experience": payloads(store, owner, "LAB_EXPERIENCE_CREATED"),
        "capabilities": payloads(store, owner, "LAB_CAPABILITY_PROPOSED"),
        "full_usage": store.resource_totals_tree(owner),
        "budget": store.manifest(owner)["task"]["budget"],
        "remote_compute_queue_and_money": "UNAVAILABLE",
        "hermes_research": "NOT_STARTED"
        if LabSpec.model_validate(
            store.manifest(owner)["spec"]
        ).program.queue.environment.result_origin
        == "SIMULATED_FIXTURE"
        else "DECLARED_ADAPTER_RESEARCH",
    }


def verify_lab(store: Store, owner: str) -> dict[str, Any]:
    m = store.manifest(owner)
    spec = LabSpec.model_validate(m["spec"])
    for ref in [
        *spec.source_refs,
        *spec.human_refs,
        *spec.experience_refs,
        *([spec.capability_ref] if spec.capability_ref else []),
    ]:
        store.artifacts.get(ref)
    if pin_board(store, spec) != spec:
        raise IntegrityError("research board snapshot changed")
    if m["world_id"] != digest(
        {k: m[k] for k in ["spec", "provider", "search", "research_providers"]}
    ):
        raise IntegrityError("lab declaration changed")
    verify_roles(store, owner)
    verify_tools(store, owner)
    completed = {
        e["payload"]["receipt_ref"]: e
        for e in store.events(owner)
        if e["event_type"] == "ROLE_COMPLETED"
    }
    available = set((*spec.source_refs, *spec.human_refs))
    requests: dict[str, dict[str, Any]] = {}
    seen = set()
    actions: list[str] = []
    observations: list[dict[str, Any]] = []
    support_ref = None
    for e in store.events(owner):
        p, kind = e["payload"], e["event_type"]
        if kind == "LAB_ROLE_REQUESTED":
            context = store.artifacts.get(p["context_ref"])
            # Curator can see only completed results; other specialists only prior board evidence.
            visible = set(context["visible_evidence_ids"])
            if p["role"] == "curator":
                links = payloads(store, owner, "LAB_PROGRAM_CREATED")
                actual = {
                    r["result_ref"]
                    for r in program_report(store, links[0]["program_id"])["experiments"]
                }
                if visible != actual:
                    raise IntegrityError("curator saw unrecorded results")
            elif not visible <= available:
                raise IntegrityError("specialist used future/unavailable evidence")
            if p["key"] in requests:
                raise IntegrityError("duplicate specialist request")
            requests[p["key"]] = p
        elif kind == "LAB_ROLE_RECORDED":
            if p["key"] in seen or p["key"] not in requests or p["receipt_ref"] not in completed:
                raise IntegrityError("specialist lacks unique paid/declared receipt")
            r = requests[p["key"]]
            receipt = store.artifacts.get(p["receipt_ref"])
            output_models: dict[str, type[Record]] = {
                "team_director": TeamPlan,
                "curator": ExperienceProposal,
                "capability_developer": CapabilityProposal,
                "capability_selector": CapabilityChoice,
            }
            output_model = output_models.get(p["role"], ExpertOpinion)
            if (
                p["context_ref"] != r["context_ref"]
                or digest(output_model.model_validate(receipt["answer"])) != p["answer_ref"]
                or store.artifacts.get(p["answer_ref"])
                != output_model.model_validate(receipt["answer"]).model_dump(mode="json")
                or receipt["answer"]["context_id"] != p["context_ref"]
            ):
                raise IntegrityError("specialist answer changed")
            seen.add(p["key"])
        elif kind == "LAB_ACTION_COMPLETED":
            if (
                support_ref is not None
                or len(actions) >= len(spec.policy.actions)
                or actions != list(spec.policy.actions[: len(actions)])
                or p["action"] != spec.policy.actions[len(actions)]
            ):
                raise IntegrityError("lab action escaped pinned policy")
            actions.append(p["action"])
            available.add(p["value_ref"])
            observations.append(
                {
                    "action": p["action"],
                    "ref": p["value_ref"],
                    "value": store.artifacts.get(p["value_ref"]),
                }
            )
        elif kind == "LAB_SUPPORT_FROZEN":
            value = ResearchSupport.model_validate(store.artifacts.get(p["support_ref"]))
            if (
                support_ref is not None
                or actions != list(spec.policy.actions)
                or value.owner != owner
                or value.method != spec.method
                or set(value.evidence_refs) != available
                or list(value.observations) != observations
            ):
                raise IntegrityError("support omitted evidence or changed method")
            support_ref = p["support_ref"]
        elif kind == "LAB_PROGRAM_CREATED":
            declared = spec.program.model_copy(update={"support_ref": p["support_ref"]})
            if (
                p["support_ref"] != support_ref
                or ResearchProgramSpec.model_validate(store.manifest(p["program_id"])["spec"])
                != declared
                or store.manifest(p["program_id"]).get("budget_owner") != owner
            ):
                raise IntegrityError("lab program escaped fixed controls/shared budget")
            program_report(store, p["program_id"])
    if store.status(owner) == "CLOSED" and (
        any(store.resource_totals_tree(owner)["pending"].values())
        or not payloads(store, owner, "LAB_EXPERIENCE_CREATED")
    ):
        raise IntegrityError("closed lab has unknown work or no experience")
    return {"status": "VERIFIED", "external_calls": 0, "specialist_calls": len(seen)}
