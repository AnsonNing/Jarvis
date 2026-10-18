"""Hermes Dream-RSI: exact histories -> one finalist -> heldout -> fresh -> canary.

Real exposed periods can develop policies but can never authorize promotion.
Fresh execution is asynchronous and uses the same bounded Iris queue contract.
"""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.campaign import code_provenance, source_identity
from jarvis.contracts import Budget, IntegrityError, Record
from jarvis.hermes_policy import (
    HermesPolicy,
    HermesWorldPlan,
    choose_task,
    compare_policies,
    q0,
    replay_hermes_world,
    seal_world,
    verify_world,
)
from jarvis.identity import digest
from jarvis.iris import IrisTransport, LocalIrisTransport
from jarvis.iris_contracts import Family, IrisExperiment, IrisQueueSpec
from jarvis.providers import CodexProvider, ResearchProvider
from jarvis.research_queue import IrisResearchQueue, queue_tasks
from jarvis.research_roles import RoleCaller, strict_schema, verify_roles
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


class HermesPolicyBatch(Record):
    kind: Literal["HermesPolicyProposals"] = "HermesPolicyProposals"
    context_id: str
    proposals: tuple[HermesPolicy, ...] = Field(min_length=2, max_length=5)


class HermesDreamSpec(Record):
    kind: Literal["HermesDreamSpec"] = "HermesDreamSpec"
    development: tuple[str, ...] = Field(min_length=1)
    heldout: tuple[str, ...] = ()
    fresh: tuple[HermesWorldPlan, ...] = ()
    canary: tuple[HermesWorldPlan, ...] = ()
    incumbent: HermesPolicy = q0()
    experiments_per_world: int = Field(default=2, ge=1, le=8, strict=True)
    minimum_histories: int = Field(default=10, ge=10, le=100, strict=True)
    minimum_families: int = Field(default=3, ge=3, le=20, strict=True)
    developer: Literal["codex", "fixture"] = "codex"
    agent: AgentConfig = AgentConfig(reserve_tokens=65536, max_calls=1)

    @model_validator(mode="after")
    def unique_refs(self) -> "HermesDreamSpec":
        refs = (*self.development, *self.heldout)
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate or overlapping world references")
        return self


def cohort_key(world: dict[str, Any]) -> str:
    env = world["environment"]
    if env["result_origin"] == "SIMULATED_FIXTURE":
        return digest({"domain": "fixture", "family": world["family_id"]})
    # Renaming the group/model/queue cannot make an already read market period new.
    return digest(
        {"domain": "iris-hermes", "branch": env["branch"], "dates": env["period"]["trading_dates"]}
    )


def history_key(world: dict[str, Any]) -> str:
    env = world["environment"]
    if env["result_origin"] == "SIMULATED_FIXTURE":
        return digest({"family": world["family_id"], "lineage": env["model_lineage"]})
    return digest(
        {
            "source": env["template_sha256"],
            "dates": env["period"]["trading_dates"],
            "side": env["side"],
        }
    )


def policy_rank(c: dict[str, Any]) -> tuple[int, float, str]:
    pairs = c["comparison"]["pairs"]
    return (
        -sum(
            p["candidate"]["best_entry_only_pl"] > p["incumbent"]["best_entry_only_pl"]
            for p in pairs
        ),
        sum(p["candidate"]["historical_cost"]["tool_calls"] for p in pairs),
        digest(c["policy"]),
    )


def development_context(store: Store, spec: HermesDreamSpec) -> dict[str, Any]:
    baseline = compare_policies(
        store,
        spec.development,
        spec.incumbent,
        spec.incumbent,
        spec.experiments_per_world,
        strict=False,
    )
    return {
        "role": "policy_developer",
        "goal": "Find higher entry-only PL per world under the same experiment budget; retain every risk result",
        "incumbent": spec.incumbent.model_dump(mode="json"),
        "baseline": baseline,
        "development_only": [
            {
                "world_ref": ref,
                "branches": [
                    {
                        "experiment": store.artifacts.get(t["experiment_ref"]),
                        "observed_result": store.artifacts.get(t["result_ref"])
                        if t["result_ref"]
                        else None,
                    }
                    for t in verify_world(store, ref)["trials"]
                ],
            }
            for ref in spec.development
        ],
        "policy_contract": "Priority and after-loss priority must contain entry,cancel,timing,heuristic,exit,joint exactly once; stop_after_no_gain 1..8. No other code/predicates. No heldout access.",
        "experiments_per_world": spec.experiments_per_world,
    }


def fixture_candidates() -> tuple[HermesPolicy, ...]:
    exit_first: tuple[Family, ...] = ("exit", "entry", "cancel", "timing", "heuristic", "joint")
    timing_first: tuple[Family, ...] = ("timing", "entry", "cancel", "heuristic", "exit", "joint")
    return (
        HermesPolicy(
            name="fixture-exit-first",
            priority=exit_first,
            after_loss_priority=exit_first,
            rationale="Declared positive engineering control; not trading knowledge",
            origin="declared_fixture",
        ),
        HermesPolicy(
            name="fixture-timing-first",
            priority=timing_first,
            after_loss_priority=timing_first,
            rationale="Declared alternative control",
            origin="declared_fixture",
        ),
    )


class HermesDream:
    def __init__(
        self,
        store: Store,
        *,
        provider_factory: Callable[[AgentConfig], ResearchProvider] = CodexProvider,
        transport_factory: Callable[..., IrisTransport] = LocalIrisTransport,
    ):
        self.store = store
        self.provider_factory = provider_factory
        self.queue = IrisResearchQueue(store, transport_factory)

    def create(self, spec: HermesDreamSpec) -> str:
        cohorts = []
        for refs in [spec.development, spec.heldout]:
            cohorts.append([verify_world(self.store, r) for r in refs])
        keys = [{cohort_key(w) for w in c} for c in cohorts]
        if keys[0] & keys[1]:
            raise IntegrityError("correlated task groups overlap across policy cohorts")
        for world in cohorts[1]:
            if world["environment"]["period"]["exposure"] != "sealed_unseen":
                raise IntegrityError("exposed results cannot be policy-heldout")
        # Real market cohorts must also be date-disjoint, including partial overlap.
        dev_dates = {
            d
            for w in cohorts[0]
            if w["environment"]["result_origin"] == "OBSERVED_IRIS_BACKTEST"
            for d in w["environment"]["period"]["trading_dates"]
        }
        for world in cohorts[1]:
            if world["environment"][
                "result_origin"
            ] == "OBSERVED_IRIS_BACKTEST" and dev_dates & set(
                world["environment"]["period"]["trading_dates"]
            ):
                raise IntegrityError("market dates overlap across policy cohorts")
        all_keys = keys[0] | keys[1]
        previous_dates = dev_dates | {
            d
            for w in cohorts[1]
            if w["environment"]["result_origin"] == "OBSERVED_IRIS_BACKTEST"
            for d in w["environment"]["period"]["trading_dates"]
        }
        for cohort in [spec.fresh, spec.canary]:
            fresh_keys = set()
            fresh_dates: set[str] = set()
            for plan in cohort:
                if plan.queue.environment.period.exposure != "sealed_unseen":
                    raise IntegrityError("fresh/canary cannot use exposed periods")
                key = cohort_key(
                    {
                        "environment": plan.queue.environment.model_dump(mode="json"),
                        "family_id": plan.family_id,
                    }
                )
                if key in all_keys or key in fresh_keys:
                    raise IntegrityError("fresh/canary family has prior exposure")
                if plan.queue.environment.result_origin == "OBSERVED_IRIS_BACKTEST":
                    dates = {str(d) for d in plan.queue.environment.period.trading_dates}
                    if dates & previous_dates:
                        raise IntegrityError("fresh/canary market dates overlap prior cohorts")
                    fresh_dates.update(dates)
                fresh_keys.add(key)
            all_keys |= fresh_keys
            previous_dates |= fresh_dates
        descriptor = (
            self.provider_factory(spec.agent).identity()
            if spec.developer == "codex"
            else {"provider": "declared-policy-fixture"}
        )
        study = self.store.create(
            {
                "kind": "HermesDreamStudy",
                "spec": spec.model_dump(mode="json"),
                "world_id": digest({"spec": spec.model_dump(mode="json"), "provider": descriptor}),
                "task": {
                    "budget": Budget(max_llm_tokens=100000, max_wall_seconds=600).model_dump()
                },
                "provider": descriptor,
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
                "scope": "research-policy development; never live trading",
            }
        )
        self.store.claim_policy_families(study, sorted(keys[0]), "development")
        return study

    def advance(self, study: str) -> dict[str, Any]:
        with self.store.campaign_lock(study):
            final = [
                e for e in self.store.events(study) if e["event_type"] == "HERMES_DREAM_COMPLETED"
            ]
            if final:
                return self.store.artifacts.get(final[0]["payload"]["report_ref"])
            manifest = self.store.manifest(study)
            if manifest["source_identity"] != source_identity():
                raise IntegrityError("Dream runtime changed")
            spec = HermesDreamSpec.model_validate(manifest["spec"])
            worlds = [verify_world(self.store, r) for r in (*spec.development, *spec.heldout)]
            if (
                len({history_key(w) for w in worlds}) < spec.minimum_histories
                or len({w["family_id"] for w in worlds}) < spec.minimum_families
            ):
                return self._finish(study, spec, "NEEDS_MORE_INDEPENDENT_HISTORIES", spec.incumbent)
            selected = [
                e["payload"]
                for e in self.store.events(study)
                if e["event_type"] == "HERMES_FINALIST_FROZEN"
            ]
        # Role dispatch holds a short owner lock, so it runs outside the coordinator
        # lock. The request is immutable and idempotent across coordinator retries.
        if not selected:
            context = development_context(self.store, spec)
            if spec.developer == "codex":
                provider = self.provider_factory(spec.agent)
                if provider.identity() != manifest["provider"]:
                    raise IntegrityError("policy provider changed")
                schema = strict_schema(HermesPolicyBatch)
                schema["properties"]["context_id"]["enum"] = [digest(context)]
                schema["$defs"]["HermesPolicy"]["properties"]["origin"]["enum"] = ["agent_proposed"]
                response = RoleCaller(self.store, provider, spec.agent).call(
                    study, "policy_developer", context, schema
                )
                batch = HermesPolicyBatch.model_validate(response["answer"])
                if batch.context_id != digest(context) or any(
                    p.origin != "agent_proposed" for p in batch.proposals
                ):
                    raise IntegrityError("policy proposals violate source/context boundary")
                candidates = batch.proposals
            else:
                candidates = fixture_candidates()
            comparisons = [
                {
                    "policy": p.model_dump(mode="json"),
                    "comparison": compare_policies(
                        self.store,
                        spec.development,
                        p,
                        spec.incumbent,
                        spec.experiments_per_world,
                        strict=True,
                    ),
                }
                for p in candidates
            ]
            eligible = [c for c in comparisons if c["comparison"]["accepted"]]
            if not eligible:
                return self._finish(
                    study,
                    spec,
                    "NO_DEMONSTRATED_DEVELOPMENT_GAIN",
                    spec.incumbent,
                    comparisons=comparisons,
                )

            winner = min(eligible, key=policy_rank)
            with self.store.campaign_lock(study):
                if not any(
                    e["event_type"] == "HERMES_FINALIST_FROZEN" for e in self.store.events(study)
                ):
                    self.store.append(
                        study,
                        "HERMES_FINALIST_FROZEN",
                        {
                            "policy_ref": self.store.artifacts.put(winner["policy"]),
                            "development_ref": self.store.artifacts.put(comparisons),
                            "context_id": digest(context),
                        },
                    )
            return {"status": "FINALIST_FROZEN", "study_id": study}
        candidate = HermesPolicy.model_validate(self.store.artifacts.get(selected[0]["policy_ref"]))
        if not spec.heldout:
            return self._finish(
                study, spec, "NO_PROMOTION_MISSING_INDEPENDENT_HELDOUT", spec.incumbent
            )
        held = [
            e["payload"]
            for e in self.store.events(study)
            if e["event_type"] == "HERMES_HELDOUT_REVEALED"
        ]
        if not held:
            keys = [cohort_key(verify_world(self.store, r)) for r in spec.heldout]
            if not self.store.claim_policy_families(study, keys, "heldout"):
                return self._finish(
                    study, spec, "INCONCLUSIVE_HELDOUT_ALREADY_EXPOSED", spec.incumbent
                )
            result = compare_policies(
                self.store,
                spec.heldout,
                candidate,
                spec.incumbent,
                spec.experiments_per_world,
                strict=False,
            )
            self.store.append(
                study,
                "HERMES_HELDOUT_REVEALED",
                {
                    "result_ref": self.store.artifacts.put(result),
                    "finalist_ref": selected[0]["policy_ref"],
                },
            )
            if not result["accepted"]:
                return self._finish(study, spec, "REJECTED_HELDOUT", spec.incumbent)
            return {"status": "HELDOUT_ACCEPTED", "study_id": study}
        if not self.store.artifacts.get(held[0]["result_ref"])["accepted"]:
            return self._finish(study, spec, "REJECTED_HELDOUT", spec.incumbent)
        if not spec.fresh or not spec.canary:
            return self._finish(study, spec, "NO_PROMOTION_MISSING_FRESH_OR_CANARY", spec.incumbent)
        fresh = self._phase(study, spec, "fresh", spec.fresh, candidate)
        if fresh is None:
            return {"status": "WAITING_FRESH_CAMPAIGNS", "study_id": study}
        if not fresh["accepted"]:
            return self._finish(study, spec, "REJECTED_FRESH", spec.incumbent)
        if not any(e["event_type"] == "HERMES_POLICY_ACTIVATED" for e in self.store.events(study)):
            self.store.append(
                study,
                "HERMES_POLICY_ACTIVATED",
                {"policy_ref": selected[0]["policy_ref"], "scope": "bounded research only"},
            )
        canary = self._phase(study, spec, "canary", spec.canary, candidate)
        if canary is None:
            return {"status": "WAITING_CANARY_CAMPAIGNS", "study_id": study}
        if not canary["accepted"]:
            if not any(
                e["event_type"] == "HERMES_POLICY_ROLLED_BACK" for e in self.store.events(study)
            ):
                self.store.append(
                    study,
                    "HERMES_POLICY_ROLLED_BACK",
                    {
                        "incumbent_ref": self.store.artifacts.put(spec.incumbent),
                        "reason": "measured prospective condition-shift regression",
                    },
                )
            return self._finish(study, spec, "ROLLED_BACK", spec.incumbent)
        return self._finish(study, spec, "APPROVED_FOR_RESEARCH", candidate)

    def _phase(
        self,
        study: str,
        spec: HermesDreamSpec,
        phase: str,
        plans: tuple[HermesWorldPlan, ...],
        candidate: HermesPolicy,
    ) -> dict[str, Any] | None:
        done = [
            e["payload"]
            for e in self.store.events(study)
            if e["event_type"] == "HERMES_PROSPECTIVE_VERIFIED" and e["payload"]["phase"] == phase
        ]
        if done:
            return dict(self.store.artifacts.get(done[0]["result_ref"]))
        keys = [
            cohort_key(
                {
                    "environment": p.queue.environment.model_dump(mode="json"),
                    "family_id": p.family_id,
                }
            )
            for p in plans
        ]
        if not self.store.claim_policy_families(study, keys, phase):
            return {"accepted": False, "reason": "prospective family already exposed"}
        pairs = []
        for index, plan in enumerate(plans):
            results = {}
            for arm, policy in [("incumbent", spec.incumbent), ("candidate", candidate)]:
                links = [
                    e["payload"]
                    for e in self.store.events(study)
                    if e["event_type"] == "HERMES_PROSPECTIVE_REGISTERED"
                    and e["payload"]["phase"] == phase
                    and e["payload"]["index"] == index
                    and e["payload"]["arm"] == arm
                ]
                if links:
                    qid = links[0]["queue_id"]
                else:
                    qid = self.queue.create(plan.queue)
                    self.store.append(
                        study,
                        "HERMES_PROSPECTIVE_REGISTERED",
                        {
                            "phase": phase,
                            "index": index,
                            "arm": arm,
                            "queue_id": qid,
                            "policy_ref": self.store.artifacts.put(policy),
                        },
                    )
                for trial in plan.trials:
                    if not any(
                        e["event_type"] == "HERMES_WORLD_SEALED" for e in self.store.events(qid)
                    ):
                        self.queue.enqueue(qid, trial)
                rows = queue_tasks(self.store, qid)
                observed = [
                    {
                        "task_id": e["payload"]["task_id"],
                        "result": self.store.artifacts.get(e["payload"]["result_ref"]),
                    }
                    for e in self.store.events(qid)
                    if e["event_type"] == "IRIS_RESULT_RECORDED"
                ]
                task_id = choose_task(
                    self.store, rows, observed, policy, spec.experiments_per_world
                )
                if any(r["status"] == "RUNNING" for r in rows) or task_id is not None:
                    self.queue.tick(qid, selected_task=task_id)
                    return None
                world_ref = seal_world(self.store, qid, plan.family_id)
                results[arm] = replay_hermes_world(
                    self.store, world_ref, policy, spec.experiments_per_world
                )
            pairs.append(results)
        accepted = all(
            p[a]["status"] == "COMPLETE" for p in pairs for a in ["candidate", "incumbent"]
        ) and all(
            p["candidate"]["best_entry_only_pl"] >= p["incumbent"]["best_entry_only_pl"]
            for p in pairs
        )
        result = {
            "accepted": accepted,
            "pairs": pairs,
            "scope": "prospective policy-arm campaigns; matched catalog and experiment budget",
        }
        self.store.append(
            study,
            "HERMES_PROSPECTIVE_VERIFIED",
            {"phase": phase, "result_ref": self.store.artifacts.put(result)},
        )
        return result

    def _finish(
        self, study: str, spec: HermesDreamSpec, status: str, active: HermesPolicy, **details: Any
    ) -> dict[str, Any]:
        existing = [
            e for e in self.store.events(study) if e["event_type"] == "HERMES_DREAM_COMPLETED"
        ]
        if existing:
            return dict(self.store.artifacts.get(existing[0]["payload"]["report_ref"]))
        value = {
            "kind": "HermesDreamReport",
            "study_id": study,
            "status": status,
            "active_policy": active.model_dump(mode="json"),
            "scope": "SIMULATED_FIXTURE"
            if all(
                verify_world(self.store, r)["environment"]["result_origin"] == "SIMULATED_FIXTURE"
                for r in spec.development
            )
            else "HERMES_RESEARCH_POLICY",
            "primary_metric": "entry_only_pl",
            "risk_policy": "record_all_no_hidden_penalty",
            "deployment": "NOT_RUN",
            "dreaming_usage": self.store.resource_totals(study),
            "details": details,
            "limitations": [
                "Exposed strategy results cannot establish independent validation",
                "Few declared task families do not establish general efficacy",
                "Recorded branch replay is exact only for admitted realized actions; unknown branches require execution",
            ],
        }
        self.store.append(
            study, "HERMES_DREAM_COMPLETED", {"report_ref": self.store.artifacts.put(value)}
        )
        self.store.set_status(study, "CLOSED")
        return value


def hermes_dream_report(store: Store, study: str) -> dict[str, Any]:
    events = store.events(study)
    final = [e for e in events if e["event_type"] == "HERMES_DREAM_COMPLETED"]
    if final:
        verification = verify_hermes_dream(store, study)
        return dict(store.artifacts.get(verification["report_ref"]))
    manifest = store.manifest(study)
    spec = HermesDreamSpec.model_validate(manifest["spec"])
    frozen = [
        e["payload"]["policy_ref"] for e in events if e["event_type"] == "HERMES_FINALIST_FROZEN"
    ]
    return {
        "kind": "HermesDreamProgress",
        "study_id": study,
        "status": store.status(study),
        "active_policy": spec.incumbent.model_dump(mode="json"),
        "finalist_refs": frozen,
        "usage": store.resource_totals(study),
        "role_receipt_verification": verify_roles(store, study),
        "formal_verdict": "NOT_COMPLETE",
        "deployment": "NOT_RUN",
    }


def verify_hermes_dream(store: Store, study: str) -> dict[str, Any]:
    manifest = store.manifest(study)
    spec = HermesDreamSpec.model_validate(manifest["spec"])
    if manifest["world_id"] != digest({"spec": manifest["spec"], "provider": manifest["provider"]}):
        raise IntegrityError("Dream specification changed")
    events = store.events(study)
    for ref in (*spec.development, *spec.heldout):
        verify_world(store, ref)
    verify_roles(store, study)
    frozen = [e for e in events if e["event_type"] == "HERMES_FINALIST_FROZEN"]
    if len(frozen) > 1:
        raise IntegrityError("multiple policy finalists")
    if frozen:
        candidate = HermesPolicy.model_validate(
            store.artifacts.get(frozen[0]["payload"]["policy_ref"])
        )
        if frozen[0]["payload"]["context_id"] != digest(development_context(store, spec)):
            raise IntegrityError("policy developer had future/changed context")
        comparisons = store.artifacts.get(frozen[0]["payload"]["development_ref"])
        eligible = []
        for c in comparisons:
            policy = HermesPolicy.model_validate(c["policy"])
            expected = compare_policies(
                store,
                spec.development,
                policy,
                spec.incumbent,
                spec.experiments_per_world,
                strict=True,
            )
            if expected != c["comparison"]:
                raise IntegrityError("development comparison changed")
            if expected["accepted"]:
                eligible.append(c)
        if not eligible or min(eligible, key=policy_rank)["policy"] != candidate.model_dump(
            mode="json"
        ):
            raise IntegrityError("finalist does not follow frozen development selection")
        if spec.developer == "codex":
            roles = [e for e in events if e["event_type"] == "ROLE_COMPLETED"]
            if len(roles) != 1 or roles[0]["sequence"] >= frozen[0]["sequence"]:
                raise IntegrityError("policy finalist lacks its developer receipt")
            batch = HermesPolicyBatch.model_validate(
                store.artifacts.get(roles[0]["payload"]["answer_ref"])
            )
            if batch.context_id != frozen[0]["payload"]["context_id"] or [
                p.model_dump(mode="json") for p in batch.proposals
            ] != [c["policy"] for c in comparisons]:
                raise IntegrityError("policies differ from developer proposals")
        for e in events:
            if e["event_type"] == "HERMES_HELDOUT_REVEALED":
                if (
                    e["sequence"] <= frozen[0]["sequence"]
                    or e["payload"]["finalist_ref"] != frozen[0]["payload"]["policy_ref"]
                    or store.artifacts.get(e["payload"]["result_ref"])
                    != compare_policies(
                        store,
                        spec.heldout,
                        candidate,
                        spec.incumbent,
                        spec.experiments_per_world,
                        strict=False,
                    )
                ):
                    raise IntegrityError("heldout result or freeze ordering changed")
    verified_phases: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["event_type"] != "HERMES_PROSPECTIVE_VERIFIED":
            continue
        phase = event["payload"]["phase"]
        if not frozen or phase not in {"fresh", "canary"} or phase in verified_phases:
            raise IntegrityError("unexpected prospective phase")
        held = [
            e
            for e in events
            if e["event_type"] == "HERMES_HELDOUT_REVEALED" and e["sequence"] < event["sequence"]
        ]
        if len(held) != 1 or not store.artifacts.get(held[0]["payload"]["result_ref"])["accepted"]:
            raise IntegrityError("prospective phase preceded heldout acceptance")
        plans = spec.fresh if phase == "fresh" else spec.canary
        pairs = []
        for index, plan in enumerate(plans):
            results = {}
            for arm, policy in [("incumbent", spec.incumbent), ("candidate", candidate)]:
                links = [
                    e
                    for e in events
                    if e["event_type"] == "HERMES_PROSPECTIVE_REGISTERED"
                    and e["payload"]["phase"] == phase
                    and e["payload"]["index"] == index
                    and e["payload"]["arm"] == arm
                ]
                if len(links) != 1 or links[0]["sequence"] >= event["sequence"]:
                    raise IntegrityError("prospective arm registration changed")
                p = links[0]["payload"]
                qid = p["queue_id"]
                if IrisQueueSpec.model_validate(
                    store.manifest(qid)["spec"]
                ) != plan.queue or store.artifacts.get(p["policy_ref"]) != policy.model_dump(
                    mode="json"
                ):
                    raise IntegrityError("prospective arm controls changed")
                rows = queue_tasks(store, qid)
                if [
                    IrisExperiment.model_validate(store.artifacts.get(t["experiment_ref"]))
                    for t in rows
                ] != list(plan.trials):
                    raise IntegrityError("prospective experiment catalog changed")
                queue_events = store.events(qid)
                sealed = [e for e in queue_events if e["event_type"] == "HERMES_WORLD_SEALED"]
                if len(sealed) != 1 or sealed[0]["recorded_at_utc"] >= event["recorded_at_utc"]:
                    raise IntegrityError("prospective world was not sealed before verification")
                results[arm] = replay_hermes_world(
                    store, sealed[0]["payload"]["world_ref"], policy, spec.experiments_per_world
                )
                actual_trace = [
                    e["payload"]["task_id"]
                    for e in queue_events
                    if e["event_type"] == "IRIS_RESULT_RECORDED"
                ]
                if actual_trace != results[arm]["trace"]:
                    raise IntegrityError("prospective execution differs from policy or budget")
            pairs.append(results)
        accepted = (
            bool(pairs)
            and all(p[a]["status"] == "COMPLETE" for p in pairs for a in ["candidate", "incumbent"])
            and all(
                p["candidate"]["best_entry_only_pl"] >= p["incumbent"]["best_entry_only_pl"]
                for p in pairs
            )
        )
        result = store.artifacts.get(event["payload"]["result_ref"])
        if result["pairs"] != pairs or result["accepted"] != accepted:
            raise IntegrityError("prospective verdict does not match observed results")
        if phase == "canary" and (
            "fresh" not in verified_phases or not verified_phases["fresh"]["accepted"]
        ):
            raise IntegrityError("canary preceded fresh acceptance")
        verified_phases[phase] = result
    final = [e for e in events if e["event_type"] == "HERMES_DREAM_COMPLETED"]
    if len(final) != 1:
        raise IntegrityError("study incomplete or conflicting reports")
    value = store.artifacts.get(final[0]["payload"]["report_ref"])
    activated = [e for e in events if e["event_type"] == "HERMES_POLICY_ACTIVATED"]
    rollback = [e for e in events if e["event_type"] == "HERMES_POLICY_ROLLED_BACK"]
    if value["status"] in {"APPROVED_FOR_RESEARCH", "ROLLED_BACK"}:
        if (
            not frozen
            or len(activated) != 1
            or "canary" not in verified_phases
            or activated[0]["payload"]["policy_ref"] != frozen[0]["payload"]["policy_ref"]
        ):
            raise IntegrityError("final verdict lacks frozen, activated and verified policy")
        if value["status"] == "APPROVED_FOR_RESEARCH":
            if (
                not verified_phases["canary"]["accepted"]
                or rollback
                or value["active_policy"] != candidate.model_dump(mode="json")
            ):
                raise IntegrityError("approval contradicts prospective verification")
        elif (
            verified_phases["canary"]["accepted"]
            or len(rollback) != 1
            or value["active_policy"] != spec.incumbent.model_dump(mode="json")
        ):
            raise IntegrityError("rollback contradicts prospective verification")
    elif value["active_policy"] != spec.incumbent.model_dump(mode="json") or activated:
        raise IntegrityError("unvalidated policy became active")
    return {
        "status": "VERIFIED",
        "study_id": study,
        "outcome": value["status"],
        "report_ref": final[0]["payload"]["report_ref"],
        "external_calls": 0,
    }
