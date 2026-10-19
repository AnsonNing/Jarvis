"""Prospective method/capability/policy improvement with frozen validation cohorts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.campaign import source_identity
from jarvis.contracts import Budget, IntegrityError, JarvisError, Record
from jarvis.hermes_dream import cohort_key, history_key
from jarvis.identity import digest
from jarvis.lab_audit import audit_scope, audited
from jarvis.lab_contracts import (
    LabPolicy,
    LabSpec,
    MethodCandidates,
    PolicyCandidates,
    ResearchMethod,
)
from jarvis.lab_memory import capability_definition, pin_board
from jarvis.research_lab import ResearchLab, lab_report, payloads
from jarvis.research_roles import RoleCaller, strict_schema, verify_roles
from jarvis.researcher import AgentConfig
from jarvis.storage import Store

Axis = Literal["method", "policy", "capability"]
ComparisonAxis = Literal["method", "policy", "capability", "memory"]


class LabEvolutionSpec(Record):
    kind: Literal["LabEvolutionSpec"] = "LabEvolutionSpec"
    axis: Axis
    development: tuple[LabSpec, ...] = Field(min_length=3, max_length=12)
    heldout: tuple[LabSpec, ...] = Field(min_length=1, max_length=8)
    fresh: tuple[LabSpec, ...] = Field(min_length=1, max_length=8)
    canary: tuple[LabSpec, ...] = Field(min_length=1, max_length=8)
    continuation: tuple[LabSpec, LabSpec]
    candidate_capability: str | None = None
    developer: AgentConfig
    budget: Budget

    @model_validator(mode="after")
    def independent(self) -> "LabEvolutionSpec":
        if len({s.family for s in self.development}) < 3:
            raise ValueError("three distinct development families required")
        previous: set[str] = set()
        histories = set()
        template = self.development[0]
        for phase, tasks in [
            ("development", self.development),
            ("heldout", self.heldout),
            ("fresh", self.fresh),
            ("canary", self.canary),
        ]:
            current = set()
            for task in tasks:
                env = task.program.queue.environment
                value = {"environment": env.model_dump(mode="json"), "family_id": task.family}
                key = cohort_key(value)
                if key in previous:
                    raise ValueError("validation cohorts overlap")
                if phase != "development" and env.period.exposure != "sealed_unseen":
                    raise ValueError("validation task must be unopened")
                if (
                    task.method != template.method
                    or task.policy != template.policy
                    or task.capability_ref != template.capability_ref
                    or protocol(task) != protocol(template)
                ):
                    raise ValueError(
                        "evolution must isolate one axis with a fixed research protocol"
                    )
                current.add(key)
                if phase in {"development", "heldout"}:
                    histories.add(history_key(value))
            previous |= current
        if self.axis == "policy" and len(histories) < 10:
            raise ValueError("extended Dream requires ten distinct development/heldout histories")
        if self.axis == "capability" and self.candidate_capability is None:
            raise ValueError("capability evolution requires a fidelity-tested candidate")
        if any(
            s.program.queue.environment.period.exposure != "development_exposed"
            or protocol(s) != protocol(template)
            for s in self.continuation
        ):
            raise ValueError("continuation must use the same development protocol")
        return self


def protocol(spec: LabSpec) -> str:
    env = spec.program.queue.environment
    return digest(
        {
            "budget": spec.budget.model_dump(mode="json"),
            "roles": spec.role_config.model_dump(mode="json"),
            "director": spec.program.agent.model_dump(mode="json"),
            "critic": spec.program.critic.model_dump(mode="json") if spec.program.critic else None,
            "execution_budget": spec.program.execution_budget.model_dump(mode="json")
            if spec.program.execution_budget
            else None,
            "directions": spec.program.directions,
            "rounds": spec.program.max_direction_batches,
            "source": env.template_sha256,
            "evaluator": env.evaluator_sha256,
            "result_origin": env.result_origin,
            "side": env.side,
            "external_search": spec.allow_external_search,
            "source_backend": spec.source_backend,
            "observed_backend_permission": spec.allow_observed_backend,
        }
    )


def variant(spec: LabSpec, axis: Axis) -> Any:
    if axis == "method":
        return spec.method.model_dump(mode="json")
    if axis == "policy":
        return spec.policy.model_dump(mode="json")
    return spec.capability_ref


def with_variant(spec: LabSpec, axis: Axis, value: Any) -> LabSpec:
    update = (
        {"method": ResearchMethod.model_validate(value)}
        if axis == "method"
        else {"policy": LabPolicy.model_validate(value)}
        if axis == "policy"
        else {"capability_ref": value}
    )
    return LabSpec.model_validate(spec.model_copy(update=update).model_dump())


def episode_summary(store: Store, owner: str) -> dict[str, Any]:
    report = lab_report(store, owner)
    events = store.events(owner)
    elapsed = (
        datetime.fromisoformat(events[-1]["recorded_at_utc"])
        - datetime.fromisoformat(events[0]["recorded_at_utc"])
    ).total_seconds()
    usage = report["full_usage"]
    budget = Budget.model_validate(report["budget"])
    within = elapsed <= budget.max_wall_seconds and all(
        usage["actual"][k] + usage["pending"][k] <= limit + 1e-9
        for k, limit in budget.limits().items()
    )
    best = report["program"]["best_development_candidate"] if report["program"] else None
    return {
        "episode": owner,
        "complete": report["status"] == "CLOSED" and not any(usage["pending"].values()) and within,
        "within_budget": within,
        "usage": usage,
        "best": best,
        "local_elapsed_seconds": elapsed,
    }


def compare_episodes(
    store: Store, incumbent: str, candidate: str, axis: ComparisonAxis
) -> dict[str, Any]:
    a, b = [store.manifest(x) for x in [incumbent, candidate]]
    excluded = {
        "method"
        if axis == "method"
        else "policy"
        if axis == "policy"
        else "experience_refs"
        if axis == "memory"
        else "capability_ref"
    }
    if (
        a["provider"] != b["provider"]
        or a["research_providers"] != b["research_providers"]
        or a["search"] != b["search"]
        or {k: v for k, v in a["spec"].items() if k not in excluded}
        != {k: v for k, v in b["spec"].items() if k not in excluded}
    ):
        raise IntegrityError(
            "comparison changed task/provider/assets/budget or a second improvement axis"
        )
    arms = [episode_summary(store, x) for x in [incumbent, candidate]]
    nonreg = (
        all(x["complete"] and x["best"] for x in arms)
        and arms[1]["best"]["result"]["entry_only_pl"] >= arms[0]["best"]["result"]["entry_only_pl"]
    )
    better = (
        nonreg
        and arms[1]["best"]["result"]["entry_only_pl"] > arms[0]["best"]["result"]["entry_only_pl"]
    )
    costs = [x["usage"]["actual"] for x in arms]
    cheaper = (
        nonreg
        and all(costs[1][k] <= costs[0][k] for k in costs[0])
        and any(
            costs[1][k] < costs[0][k]
            for k in ["llm_tokens", "tool_calls", "compute_units", "actions"]
        )
    )
    return {
        "arms": arms,
        "nonregression": bool(nonreg),
        "strict_gain": bool(better or cheaper),
        "axis": axis,
        "cost_scope": "complete roles/tools/compiles/baseline/backend; policy development and collection separately in study total",
    }


class LabEvolution:
    def __init__(self, store: Store, lab: ResearchLab):
        self.store, self.lab = store, lab

    def create(self, spec: LabEvolutionSpec) -> str:
        spec = LabEvolutionSpec.model_validate(
            spec.model_copy(
                update={
                    name: tuple(pin_board(self.store, s) for s in getattr(spec, name))
                    for name in ["development", "heldout", "fresh", "canary", "continuation"]
                }
            ).model_dump()
        )
        raw = spec.model_dump(mode="json")
        provider = self.lab.provider_factory(spec.developer).identity()
        research_providers = {
            "director": self.lab.program.provider_factory(
                spec.development[0].program.agent
            ).identity(),
            "critic": self.lab.program.critic_provider_factory(
                spec.development[0].program.critic
            ).identity()
            if spec.development[0].program.critic
            else None,
        }
        return self.store.create(
            {
                "kind": "LabEvolutionStudy",
                "spec": raw,
                "provider": provider,
                "research_providers": research_providers,
                "world_id": digest(
                    {"spec": raw, "provider": provider, "research_providers": research_providers}
                ),
                "source_identity": source_identity(),
                "task": {"budget": spec.budget.model_dump()},
            }
        )

    def episode(self, owner: str, key: str, spec: LabSpec) -> str:
        links = [
            p for p in payloads(self.store, owner, "EVOLUTION_EPISODE_CREATED") if p["key"] == key
        ]
        if links:
            return str(links[0]["episode"])
        child = self.lab.create(spec, budget_owner=owner)
        self.store.append(
            owner,
            "EVOLUTION_EPISODE_CREATED",
            {"key": key, "episode": child, "spec_ref": self.store.artifacts.put(spec)},
        )
        return child

    def finish_one(self, episode: str) -> bool:
        if self.store.status(episode) == "CLOSED":
            return True
        status = self.lab.advance(episode)["status"]
        if status in {
            "LAB_NEEDS_ATTENTION",
            "BUDGET_EXHAUSTED",
            "NEEDS_RECONCILIATION",
            "PAUSED",
            "CANCELLED",
        }:
            raise JarvisError("lab evolution child needs attention: " + status)
        return status == "CLOSED"

    def propose(
        self, owner: str, spec: LabEvolutionSpec, histories: list[str], cycle: int
    ) -> list[Any]:
        known = [
            p for p in payloads(self.store, owner, "EVOLUTION_CANDIDATES") if p["cycle"] == cycle
        ]
        if known:
            return self.store.artifacts.get(known[0]["candidates_ref"])
        context = {
            "axis": spec.axis,
            "cycle": cycle,
            "incumbent": variant(spec.development[0], spec.axis),
            "development_only": [
                {
                    "episode": p,
                    "summary": episode_summary(self.store, p),
                    "experience": payloads(self.store, p, "LAB_EXPERIENCE_CREATED"),
                }
                for p in histories
            ],
            "constraint": "Change only declared method fields or closed action policy. No evaluator, permissions, budget, models or hidden-validation access. New contexts require actual online programs, not guessed replay.",
        }
        values: list[Any]
        if (
            cycle == 2
            and payloads(self.store, owner, "EVOLUTION_ACTIVATED")
            and not payloads(self.store, owner, "EVOLUTION_ROLLED_BACK")
        ):
            context["incumbent"] = self.store.artifacts.get(
                payloads(self.store, owner, "EVOLUTION_ACTIVATED")[0]["variant_ref"]
            )
        if spec.axis == "capability":
            capability_definition(self.store, spec.candidate_capability or "")
            values = [spec.candidate_capability]
            receipt_ref = None
        else:
            model = MethodCandidates if spec.axis == "method" else PolicyCandidates
            provider = self.lab.provider_factory(spec.developer)
            if provider.identity() != self.store.manifest(owner)["provider"]:
                raise IntegrityError("improvement provider changed")
            response = RoleCaller(self.store, provider, spec.developer).call(
                owner,
                "method_developer" if spec.axis == "method" else "lab_policy_developer",
                context,
                strict_schema(model),
                role_budget=True,
                guidance="Propose bounded research-method or research-action changes from development histories only; cite failure mechanisms; do not change trusted controls.",
            )
            answer = model.model_validate(response["answer"])
            if answer.context_id != digest(context):
                raise IntegrityError("stale improvement proposal")
            values = [
                x.model_dump(mode="json")
                for x in (
                    answer.methods if isinstance(answer, MethodCandidates) else answer.policies
                )
            ]
            receipt_ref = response["receipt_ref"]
        self.store.append(
            owner,
            "EVOLUTION_CANDIDATES",
            {
                "cycle": cycle,
                "history": histories,
                "context_ref": self.store.artifacts.put(context),
                "candidates_ref": self.store.artifacts.put(values),
                "receipt_ref": receipt_ref,
            },
        )
        return values

    def pairs(
        self,
        owner: str,
        spec: LabEvolutionSpec,
        phase: str,
        tasks: tuple[LabSpec, ...],
        value: Any,
        *,
        tag: str = "",
    ) -> dict[str, Any] | None:
        key = phase + tag
        known = [p for p in payloads(self.store, owner, "EVOLUTION_PHASE") if p["key"] == key]
        if known:
            return self.store.artifacts.get(known[0]["result_ref"])
        cohorts = [
            cohort_key(
                {
                    "environment": s.program.queue.environment.model_dump(mode="json"),
                    "family_id": s.family,
                }
            )
            for s in tasks
        ]
        if not self.store.claim_policy_families(owner, cohorts, phase):
            raise IntegrityError("validation family already exposed; no retry/rename")
        pairs = []
        for i, task in enumerate(tasks):
            cached = [
                p
                for p in payloads(self.store, owner, "EVOLUTION_PAIR_MEASURED")
                if p["key"] == f"{key}:{i}"
            ]
            if cached:
                pairs.append(self.store.artifacts.get(cached[0]["pair_ref"]))
                continue
            for arm in ["incumbent", "candidate"]:
                declared = task if arm == "incumbent" else with_variant(task, spec.axis, value)
                episode = self.episode(owner, f"{key}:{i}:{arm}", declared)
                if not self.finish_one(episode):
                    return None
            links = {
                p["key"]: p["episode"]
                for p in payloads(self.store, owner, "EVOLUTION_EPISODE_CREATED")
            }
            a, b = [links[f"{key}:{i}:{arm}"] for arm in ["incumbent", "candidate"]]
            pair = {
                "incumbent": a,
                "candidate": b,
                "comparison": compare_episodes(self.store, a, b, spec.axis),
            }
            self.store.append(
                owner,
                "EVOLUTION_PAIR_MEASURED",
                {"key": f"{key}:{i}", "pair_ref": self.store.artifacts.put(pair)},
            )
            pairs.append(pair)
        result = {
            "phase": phase,
            "pairs": pairs,
            "nonregression": all(p["comparison"]["nonregression"] for p in pairs),
            "strict_gain": any(p["comparison"]["strict_gain"] for p in pairs),
        }
        self.store.append(
            owner, "EVOLUTION_PHASE", {"key": key, "result_ref": self.store.artifacts.put(result)}
        )
        return result

    def advance(self, owner: str) -> dict[str, Any]:
        with self.store.campaign_lock(owner, scope="lab-evolution"):
            if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                return {"status": self.store.get_control(owner), "study": owner}
            if self.store.status(owner) in {"CLOSED", "NEEDS_RECONCILIATION"}:
                return evolution_report(self.store, owner)
            if self.store.manifest(owner)["source_identity"] != source_identity():
                raise IntegrityError("evolution runtime changed")
            try:
                return self._advance(owner)
            except (JarvisError, ValueError):
                self.store.set_status(owner, "NEEDS_RECONCILIATION")
                raise

    def _advance(self, owner: str) -> dict[str, Any]:
        spec = LabEvolutionSpec.model_validate(self.store.manifest(owner)["spec"])
        histories = []
        for i, task in enumerate(spec.development):
            episode = self.episode(owner, f"collection:{i}", task)
            if not self.finish_one(episode):
                return {"status": "COLLECTING_DEVELOPMENT", "study": owner}
            histories.append(episode)
        # Collect heldout histories behind the evidence boundary, before policy
        # generation. The developer receives ONLY development episodes.
        if spec.axis == "policy":
            for i, task in enumerate(spec.heldout):
                episode = self.episode(owner, f"blind-heldout:{i}", task)
                if not self.finish_one(episode):
                    return {"status": "COLLECTING_BLIND_HELDOUT", "study": owner}
        candidates = self.propose(owner, spec, histories, 1)
        frozen = payloads(self.store, owner, "EVOLUTION_FINALIST")
        if not frozen:
            eligible = []
            for i, value in enumerate(candidates):
                result = self.pairs(owner, spec, "development", spec.development, value, tag=str(i))
                if result is None:
                    return {"status": "PROBING_METHODS", "study": owner}
                if result["nonregression"] and result["strict_gain"]:
                    eligible.append((value, result))
            if not eligible:
                return self.close(owner, "NO_DEVELOPMENT_GAIN")
            chosen = min(
                eligible,
                key=lambda p: (
                    -sum(x["comparison"]["strict_gain"] for x in p[1]["pairs"]),
                    digest(p[0]),
                ),
            )[0]
            self.store.append(
                owner, "EVOLUTION_FINALIST", {"variant_ref": self.store.artifacts.put(chosen)}
            )
        chosen = self.store.artifacts.get(
            payloads(self.store, owner, "EVOLUTION_FINALIST")[0]["variant_ref"]
        )
        for phase, tasks in [("heldout", spec.heldout), ("fresh", spec.fresh)]:
            result = self.pairs(owner, spec, phase, tasks, chosen)
            if result is None:
                return {"status": "VALIDATING_" + phase.upper(), "study": owner}
            if not result["nonregression"] or (phase == "fresh" and not result["strict_gain"]):
                return self.close(owner, "REJECTED_" + phase.upper())
        if not payloads(self.store, owner, "EVOLUTION_ACTIVATED"):
            self.store.append(
                owner,
                "EVOLUTION_ACTIVATED",
                {"variant_ref": digest(chosen), "scope": protocol(spec.development[0])},
            )
        first = self.episode(
            owner, "continuation:0", with_variant(spec.continuation[0], spec.axis, chosen)
        )
        if not self.finish_one(first):
            return {"status": "CONTINUING_ADOPTED_VERSION", "study": owner}
        canary = self.pairs(owner, spec, "canary", spec.canary, chosen)
        if canary is None:
            return {"status": "VALIDATING_CANARY", "study": owner}
        if not canary["nonregression"] and not payloads(self.store, owner, "EVOLUTION_ROLLED_BACK"):
            self.store.append(
                owner,
                "EVOLUTION_ROLLED_BACK",
                {
                    "variant_ref": digest(variant(spec.development[0], spec.axis)),
                    "reason": "MEASURED_CANARY_REGRESSION",
                },
            )
        next_value = chosen if canary["nonregression"] else variant(spec.development[0], spec.axis)
        second = self.episode(
            owner, "continuation:1", with_variant(spec.continuation[1], spec.axis, next_value)
        )
        if not self.finish_one(second):
            return {"status": "CONTINUING_AFTER_CANARY", "study": owner}
        self.propose(owner, spec, [*histories, first, second], 2)
        if not payloads(self.store, owner, "EVOLUTION_NEXT_CYCLE_STOPPED"):
            self.store.append(
                owner,
                "EVOLUTION_NEXT_CYCLE_STOPPED",
                {
                    "reason": "NEW_INDEPENDENT_VALIDATION_REQUIRED",
                    "new_history": [first, second],
                    "old_validation_reused": False,
                },
            )
        return self.close(
            owner, "APPROVED_FOR_RESEARCH" if canary["nonregression"] else "ROLLED_BACK"
        )

    def close(self, owner: str, outcome: str) -> dict[str, Any]:
        self.store.append(owner, "EVOLUTION_COMPLETED", {"outcome": outcome})
        self.store.set_status(owner, "CLOSED")
        return evolution_report(self.store, owner)


def verify_evolution(store: Store, owner: str) -> dict[str, Any]:
    m = store.manifest(owner)
    spec = LabEvolutionSpec.model_validate(m["spec"])
    if m["kind"] != "LabEvolutionStudy" or m["world_id"] != digest(
        {
            "spec": m["spec"],
            "provider": m["provider"],
            "research_providers": m["research_providers"],
        }
    ):
        raise IntegrityError("evolution declaration changed")
    verify_roles(store, owner)
    links = payloads(store, owner, "EVOLUTION_EPISODE_CREATED")
    by_key = {p["key"]: p for p in links}
    if len(by_key) != len(links):
        raise IntegrityError("duplicate evolution arm")
    for index, task in enumerate(spec.development):
        if (
            f"collection:{index}" in by_key
            and LabSpec.model_validate(
                store.manifest(by_key[f"collection:{index}"]["episode"])["spec"]
            )
            != task
        ):
            raise IntegrityError("development collection changed task")
    proposals = payloads(store, owner, "EVOLUTION_CANDIDATES")
    if len({p["cycle"] for p in proposals}) != len(proposals):
        raise IntegrityError("duplicate improvement cycle")
    role_events = {
        e["payload"]["receipt_ref"]: e
        for e in store.events(owner)
        if e["event_type"] == "ROLE_COMPLETED"
    }
    role_jobs = {j["id"]: j for j in store.jobs(owner)}
    for proposal in proposals:
        expected_history = [
            by_key[f"collection:{i}"]["episode"] for i in range(len(spec.development))
        ]
        if proposal["cycle"] == 2:
            expected_history += [by_key[f"continuation:{i}"]["episode"] for i in range(2)]
        context = store.artifacts.get(proposal["context_ref"])
        if context["axis"] != spec.axis or context["cycle"] != proposal["cycle"]:
            raise IntegrityError("improvement context changed its axis/cycle")
        if (
            proposal["history"] != expected_history
            or [p["episode"] for p in context["development_only"]] != expected_history
        ):
            raise IntegrityError("developer saw validation/future or omitted new history")
        for row in context["development_only"]:
            if row["summary"] != episode_summary(store, row["episode"]) or row[
                "experience"
            ] != payloads(store, row["episode"], "LAB_EXPERIENCE_CREATED"):
                raise IntegrityError("developer history differs from observed evidence")
        values = store.artifacts.get(proposal["candidates_ref"])
        if spec.axis == "capability":
            if values != [spec.candidate_capability]:
                raise IntegrityError("substituted capability candidate")
            capability_definition(store, spec.candidate_capability or "")
        else:
            event = role_events.get(proposal["receipt_ref"])
            if event is None:
                raise IntegrityError("improvement lacks provider receipt")
            request = store.artifacts.get(role_jobs[event["payload"]["job_id"]]["request_ref"])
            receipt = store.artifacts.get(proposal["receipt_ref"])
            answer = (
                MethodCandidates if spec.axis == "method" else PolicyCandidates
            ).model_validate(receipt["answer"])
            actual = [
                x.model_dump(mode="json")
                for x in (
                    answer.methods if isinstance(answer, MethodCandidates) else answer.policies
                )
            ]
            if (
                request["context"] != context
                or answer.context_id != proposal["context_ref"]
                or actual != values
                or request["provider"] != m["provider"]
                or request["config"] != spec.developer.model_dump(mode="json")
            ):
                raise IntegrityError("method/policy candidate escaped its observed declaration")
    frozen = [e for e in store.events(owner) if e["event_type"] == "EVOLUTION_FINALIST"]
    if len(frozen) > 1:
        raise IntegrityError("multiple frozen finalists")
    chosen = store.artifacts.get(frozen[0]["payload"]["variant_ref"]) if frozen else None
    phases = {}
    for event in store.events(owner):
        if event["event_type"] != "EVOLUTION_PHASE":
            continue
        p = event["payload"]
        if p["key"] in phases:
            raise IntegrityError("duplicate evolution phase")
        result = store.artifacts.get(p["result_ref"])
        phase = result["phase"]
        tasks = getattr(spec, phase)
        if phase != "development" and (not frozen or event["sequence"] <= frozen[0]["sequence"]):
            raise IntegrityError("validation exposed before finalist freeze")
        candidate = (
            chosen
            if phase != "development"
            else store.artifacts.get(
                payloads(store, owner, "EVOLUTION_CANDIDATES")[0]["candidates_ref"]
            )[int(p["key"].removeprefix("development"))]
        )
        if len(result["pairs"]) != len(tasks):
            raise IntegrityError("comparison omitted tasks")
        actual = []
        for i, (task, pair) in enumerate(zip(tasks, result["pairs"], strict=True)):
            for arm in ["incumbent", "candidate"]:
                expected = task if arm == "incumbent" else with_variant(task, spec.axis, candidate)
                if (
                    by_key[f"{p['key']}:{i}:{arm}"]["episode"] != pair[arm]
                    or LabSpec.model_validate(store.manifest(pair[arm])["spec"]) != expected
                ):
                    raise IntegrityError("evolution arm escaped its declared task")
                if (
                    phase != "development"
                    and store.manifest(pair[arm])["created_at"] < frozen[0]["recorded_at_utc"]
                ):
                    raise IntegrityError("validation arm predates frozen finalist")
            comparison = compare_episodes(store, pair["incumbent"], pair["candidate"], spec.axis)
            if comparison != pair["comparison"]:
                raise IntegrityError("comparison differs from actual cost/result receipts")
            actual.append(comparison)
        if result["nonregression"] != all(p["nonregression"] for p in actual) or result[
            "strict_gain"
        ] != any(p["strict_gain"] for p in actual):
            raise IntegrityError("phase verdict changed")
        phases[p["key"]] = result
    if frozen:
        candidates = store.artifacts.get(proposals[0]["candidates_ref"])
        eligible: list[tuple[Any, dict[str, Any]]] = []
        for i, value in enumerate(candidates):
            phase_value = phases.get("development" + str(i))
            if (
                phase_value is not None
                and phase_value["nonregression"]
                and phase_value["strict_gain"]
            ):
                eligible.append((value, phase_value))
        expected_variant = (
            min(
                eligible,
                key=lambda p: (
                    -sum(x["comparison"]["strict_gain"] for x in p[1]["pairs"]),
                    digest(p[0]),
                ),
            )[0]
            if eligible
            else None
        )
        if chosen != expected_variant:
            raise IntegrityError("finalist differs from actual development selection")
    for row in links:
        if store.manifest(row["episode"]).get("budget_owner") != owner or store.manifest(
            row["episode"]
        )["spec"] != store.artifacts.get(row["spec_ref"]):
            raise IntegrityError("evolution descendant escaped cost lineage")
    done = payloads(store, owner, "EVOLUTION_COMPLETED")
    if done and done[0]["outcome"] in {"APPROVED_FOR_RESEARCH", "ROLLED_BACK"}:
        if (
            not all(k in phases for k in ["heldout", "fresh", "canary"])
            or not phases["heldout"]["nonregression"]
            or not phases["fresh"]["nonregression"]
            or not phases["fresh"]["strict_gain"]
        ):
            raise IntegrityError("promotion lacks full independent validation")
        desired = "APPROVED_FOR_RESEARCH" if phases["canary"]["nonregression"] else "ROLLED_BACK"
        if done[0]["outcome"] != desired or not payloads(store, owner, "EVOLUTION_ACTIVATED"):
            raise IntegrityError("adoption/rollback differs from canary")
        for index, value in enumerate(
            [
                chosen,
                chosen
                if desired == "APPROVED_FOR_RESEARCH"
                else variant(spec.development[0], spec.axis),
            ]
        ):
            child = by_key[f"continuation:{index}"]["episode"]
            if (
                LabSpec.model_validate(store.manifest(child)["spec"])
                != with_variant(spec.continuation[index], spec.axis, value)
                or lab_report(store, child)["status"] != "CLOSED"
            ):
                raise IntegrityError("new research did not actually adopt the verified variant")
        if len(payloads(store, owner, "EVOLUTION_CANDIDATES")) != 2 or not payloads(
            store, owner, "EVOLUTION_NEXT_CYCLE_STOPPED"
        ):
            raise IntegrityError("improvement loop omitted new-history feedback")
    if store.status(owner) == "CLOSED" and (
        len(done) != 1 or any(store.resource_totals_tree(owner)["pending"].values())
    ):
        raise IntegrityError("closed evolution has unknown work")
    return {"status": "VERIFIED", "external_calls": 0, "axis": spec.axis, "episodes": len(links)}


def evolution_report(store: Store, owner: str) -> dict[str, Any]:
    with audit_scope():
        return audited(store, "evolution", owner, lambda: _evolution_report(store, owner))


def _evolution_report(store: Store, owner: str) -> dict[str, Any]:
    verification = verify_evolution(store, owner)
    done = payloads(store, owner, "EVOLUTION_COMPLETED")
    return {
        "kind": "LabEvolutionReport",
        "study": owner,
        "status": store.status(owner),
        "outcome": done[0]["outcome"] if done else None,
        "verification": verification,
        "phases": payloads(store, owner, "EVOLUTION_PHASE"),
        "full_usage": store.resource_totals_tree(owner),
        "development_overhead": store.resource_totals(owner),
        "continuation": [
            p
            for p in payloads(store, owner, "EVOLUTION_EPISODE_CREATED")
            if p["key"].startswith("continuation:")
        ],
        "next_cycle": payloads(store, owner, "EVOLUTION_NEXT_CYCLE_STOPPED"),
        "hermes_research": "NOT_STARTED"
        if LabEvolutionSpec.model_validate(store.manifest(owner)["spec"])
        .development[0]
        .program.queue.environment.result_origin
        == "SIMULATED_FIXTURE"
        else "DECLARED_ADAPTER_RESEARCH",
    }


def adopt_profile(store: Store, spec: LabSpec, study: str) -> LabSpec:
    report = evolution_report(store, study)
    declaration = LabEvolutionSpec.model_validate(store.manifest(study)["spec"])
    template = declaration.development[0]
    axes: tuple[Axis, ...] = ("method", "policy", "capability")
    if spec.adoption_study not in {None, study} or any(
        variant(spec, axis) != variant(template, axis) for axis in axes if axis != declaration.axis
    ):
        raise IntegrityError("combined improvement axes require separate joint validation")
    if (
        report["status"] != "CLOSED"
        or report["outcome"] not in {"APPROVED_FOR_RESEARCH", "ROLLED_BACK"}
        or protocol(spec) != protocol(declaration.development[0])
        or store.manifest(study)["source_identity"] != source_identity()
    ):
        raise IntegrityError("profile is not validated for this runtime/research protocol")
    finalist = payloads(store, study, "EVOLUTION_FINALIST")[0]["variant_ref"]
    value = (
        store.artifacts.get(finalist)
        if report["outcome"] == "APPROVED_FOR_RESEARCH"
        else variant(declaration.development[0], declaration.axis)
    )
    return with_variant(spec, declaration.axis, value).model_copy(update={"adoption_study": study})


def replay_lab(store: Store, episode: str, policy: LabPolicy) -> dict[str, Any]:
    """Exact recorded action prefix only; changed contexts are not counterfactual evidence."""
    spec = LabSpec.model_validate(store.manifest(episode)["spec"])
    lab_report(store, episode)
    trace: list[dict[str, Any]] = []
    recorded = payloads(store, episode, "LAB_ACTION_COMPLETED")
    for index, action in enumerate(policy.actions):
        if (
            index >= len(recorded)
            or index >= len(spec.policy.actions)
            or action != spec.policy.actions[index]
        ):
            return {
                "status": "INSUFFICIENT_COVERAGE",
                "unknown_action": action,
                "trace": trace,
                "external_calls": 0,
            }
        row = recorded[index]
        trace.append(row)
    if policy.actions != spec.policy.actions:
        return {
            "status": "INSUFFICIENT_COVERAGE",
            "unknown_action": "changed Director context",
            "trace": trace,
            "external_calls": 0,
        }
    return {
        "status": "COMPLETE",
        "trace": trace,
        "summary": episode_summary(store, episode),
        "external_calls": 0,
    }
