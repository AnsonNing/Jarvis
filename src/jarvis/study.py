"""Multi-round study, evidence-gated policy versions, canary and rollback."""

from typing import Any, Literal

from jarvis.branch_replay import aggregate
from jarvis.campaign import code_provenance, source_identity
from jarvis.contracts import IntegrityError, JarvisError
from jarvis.dream import DreamEngine, better, verify_round
from jarvis.exploration import ExplorationPolicy, incumbent
from jarvis.fresh import verify_fresh
from jarvis.identity import digest
from jarvis.pool import DreamProtocol, HistoryPool, PoolBuilder
from jarvis.providers import ResearchProvider
from jarvis.researcher import AgentConfig
from jarvis.storage import Store
from jarvis.suite import Fixture


class PolicyRegistry:
    def __init__(self, store: Store):
        self.store = store

    def create(self) -> str:
        return self.store.create(
            {
                "kind": "ResearchPolicyRegistry",
                "scope": "synthetic_only",
                "initial_policy": incumbent().model_dump(mode="json"),
            }
        )

    def active(self, registry: str) -> tuple[ExplorationPolicy, ExplorationPolicy | None]:
        manifest = self.store.manifest(registry)
        if manifest["kind"] != "ResearchPolicyRegistry":
            raise IntegrityError("not a research policy registry")
        current = ExplorationPolicy.model_validate(manifest["initial_policy"])
        previous = None
        for event in self.store.events(registry):
            payload = event["payload"]
            if event["event_type"] == "POLICY_PROMOTED":
                if payload["previous_id"] != current.identity:
                    raise IntegrityError("policy version ancestry changed")
                checked = verify_round(self.store, payload["round_id"])
                report = self.store.artifacts.get(checked["report_ref"])
                proposed = ExplorationPolicy.model_validate(
                    self.store.artifacts.get(report["policy_ref"])
                )
                if (
                    checked["decision"] != "APPROVED_FOR_RESEARCH"
                    or proposed.identity != payload["policy_id"]
                    or report["incumbent_id"] != current.identity
                ):
                    raise IntegrityError("policy promotion lacks matching evidence")
                previous, current = current, proposed
            if event["event_type"] == "POLICY_ROLLED_BACK":
                canary = self.store.artifacts.get(payload["canary_ref"])
                if (
                    current.identity != payload["deprecated_id"]
                    or previous is None
                    or previous.identity != payload["restored_id"]
                    or canary["safe"]
                ):
                    raise IntegrityError("invalid rollback ancestry/evidence")
                verify_canary(self.store, payload["canary_ref"])
                current, previous = previous, None
        return current, previous

    def apply(self, registry: str, round_id: str) -> str:
        with self.store.campaign_lock(registry):
            checked = verify_round(self.store, round_id)
            report = self.store.artifacts.get(checked["report_ref"])
            events = self.store.events(registry)
            if any(
                e["event_type"] in {"POLICY_PROMOTED", "POLICY_RETAINED"}
                and e["payload"]["round_id"] == round_id
                for e in events
            ):
                return self.active(registry)[0].identity
            current, _ = self.active(registry)
            if report["incumbent_id"] != current.identity:
                raise JarvisError("stale incumbent; do not apply this round")
            if report["status"] == "APPROVED_FOR_RESEARCH":
                self.store.append(
                    registry,
                    "POLICY_PROMOTED",
                    {
                        "round_id": round_id,
                        "report_ref": checked["report_ref"],
                        "policy_id": report["finalist_id"],
                        "previous_id": current.identity,
                        "approval_scope": "synthetic_research_only",
                    },
                )
            else:
                self.store.append(
                    registry,
                    "POLICY_RETAINED",
                    {
                        "round_id": round_id,
                        "report_ref": checked["report_ref"],
                        "policy_id": current.identity,
                    },
                )
            return self.active(registry)[0].identity

    def rollback(self, registry: str, canary_ref: str) -> str:
        with self.store.campaign_lock(registry):
            result = verify_canary(self.store, canary_ref)
            if any(
                e["event_type"] == "POLICY_ROLLED_BACK" and e["payload"]["canary_ref"] == canary_ref
                for e in self.store.events(registry)
            ):
                return self.active(registry)[0].identity
            current, previous = self.active(registry)
            if result["registry_id"] != registry or result["active_id"] != current.identity:
                raise JarvisError("canary belongs to another active policy version")
            if not result["safe"]:
                if previous is None or previous.identity != result["fallback_id"]:
                    raise IntegrityError("rollback fallback unavailable")
                self.store.append(
                    registry,
                    "POLICY_ROLLED_BACK",
                    {
                        "canary_ref": canary_ref,
                        "deprecated_id": current.identity,
                        "restored_id": previous.identity,
                        "reason": "Observed fresh canary regression",
                    },
                )
            return self.active(registry)[0].identity


def run_canary(store: Store, canary: str) -> str:
    with store.campaign_lock(canary):
        manifest = store.manifest(canary)
        existing = [e for e in store.events(canary) if e["event_type"] == "CANARY_COMPLETED"]
        if existing:
            ref = str(existing[0]["payload"]["result_ref"])
            verify_canary(store, ref)
            return ref
        active = ExplorationPolicy.model_validate(manifest["active"])
        fallback = ExplorationPolicy.model_validate(manifest["fallback"])
        pool = HistoryPool.model_validate(store.artifacts.get(manifest["pool_ref"]))
        protocol = DreamProtocol.model_validate(
            {**pool.protocol.model_dump(mode="json"), "fresh": manifest["fixtures"]}
        )
        if not store.claim_policy_families(
            canary, ["suite-family:1:" + f.family for f in protocol.fresh], "canary"
        ):
            raise JarvisError("canary families already exposed; new independent cohort required")
        result = DreamEngine(store)._fresh(
            canary, pool.model_copy(update={"protocol": protocol}), fallback, active
        )
        ref = store.artifacts.put(
            {
                "kind": "PolicyCanary",
                "registry_id": manifest["registry_id"],
                "canary_id": canary,
                "active_id": active.identity,
                "fallback_id": fallback.identity,
                "validation": result,
                "safe": better(result["finalist"], result["incumbent"], strict=False),
            }
        )
        store.append(canary, "CANARY_COMPLETED", {"result_ref": ref})
        store.set_status(canary, "CLOSED")
        return ref


def verify_canary(store: Store, ref: str) -> dict[str, Any]:
    result = store.artifacts.get(ref)
    manifest = store.manifest(result["canary_id"])
    events = store.events(result["canary_id"])
    if not any(
        e["event_type"] == "CANARY_COMPLETED" and e["payload"]["result_ref"] == ref for e in events
    ):
        raise IntegrityError("canary result not sealed")
    pairs = result["validation"]["pairs"]
    if {r["fixture_id"] for r in pairs} != {
        Fixture.model_validate(f).identity for f in manifest["fixtures"]
    } or len(pairs) != len(manifest["fixtures"]):
        raise IntegrityError("canary fixture cohort changed")
    for pair in pairs:
        for arm, policy_key in (("incumbent", "fallback_id"), ("finalist", "active_id")):
            checked = verify_fresh(store, pair[arm]["result_ref"])
            if (
                checked["policy_id"] != result[policy_key]
                or checked["success"] != pair[arm]["success"]
                or checked["cost"] != pair[arm]["cost"]
            ):
                raise IntegrityError("canary arm evidence mismatch")
    for arm in ("incumbent", "finalist"):
        expected = aggregate(
            [
                {
                    "fixture_id": r["fixture_id"],
                    "family": r["family"],
                    "success": r[arm]["success"],
                    "cost": r[arm]["cost"],
                    "status": "COMPLETE"
                    if r[arm]["execution_status"] == "SUCCEEDED"
                    else r[arm]["execution_status"],
                }
                for r in pairs
            ]
        )
        if digest(expected) != digest(result["validation"][arm]):
            raise IntegrityError("canary summary differs from paid executions")
    if result["registry_id"] != manifest["registry_id"] or result["safe"] != better(
        result["validation"]["finalist"], result["validation"]["incumbent"], strict=False
    ):
        raise IntegrityError("canary gate mismatch")
    return result


class Study:
    def __init__(self, store: Store, provider: ResearchProvider | None = None):
        self.store, self.provider = store, provider

    def create(
        self,
        *,
        developer: Literal["fixture", "codex"] = "fixture",
        config: AgentConfig | None = None,
    ) -> str:
        registry = PolicyRegistry(self.store).create()
        return self.store.create(
            {
                "kind": "DreamStudy",
                "developer": developer,
                "config": (config or AgentConfig(max_calls=1)).model_dump(mode="json"),
                "registry_id": registry,
                "protocol": DreamProtocol().model_dump(mode="json"),
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
                "plan": [
                    "coverage_gap",
                    "collect_missing_branches",
                    "plateau_check",
                    "fresh_shift_canary",
                ],
            }
        )

    def run(self, study: str, *, fault: str | None = None) -> str:
        with self.store.campaign_lock(study):
            manifest = self.store.manifest(study)
            if manifest["source_identity"] != source_identity():
                raise IntegrityError("study source changed; new study required")
            prior = [e for e in self.store.events(study) if e["event_type"] == "STUDY_COMPLETED"]
            if prior:
                return str(prior[0]["payload"]["report_ref"])
            registry = manifest["registry_id"]
            protocol = DreamProtocol.model_validate(manifest["protocol"])
            records = []
            pool_ref = None
            for index, label in enumerate(manifest["plan"][:3]):
                completed = [
                    e["payload"]
                    for e in self.store.events(study)
                    if e["event_type"] == "ROUND_RECORDED" and e["payload"]["index"] == index
                ]
                if completed:
                    records.append(completed[0])
                    pool_ref = completed[0]["pool_ref"]
                    continue
                mapped = [
                    e["payload"]
                    for e in self.store.events(study)
                    if e["event_type"] == "ROUND_REGISTERED" and e["payload"]["index"] == index
                ]
                if mapped:
                    rid = mapped[0]["round_id"]
                    pool_ref = mapped[0]["pool_ref"]
                else:
                    if index < 2:
                        mapped_pool = [
                            e["payload"]
                            for e in self.store.events(study)
                            if e["event_type"] == "POOL_REGISTERED"
                            and e["payload"]["index"] == index
                        ]
                        if mapped_pool:
                            pcid = mapped_pool[0]["collection_id"]
                        else:
                            pcid = PoolBuilder(self.store).create(
                                protocol, omit=("diagnose",) if index == 0 else ()
                            )
                            self.store.append(
                                study, "POOL_REGISTERED", {"index": index, "collection_id": pcid}
                            )
                        pool_ref = PoolBuilder(self.store).run(pcid)
                    assert pool_ref is not None
                    current, _ = PolicyRegistry(self.store).active(registry)
                    rid = DreamEngine(self.store, self.provider).create(
                        pool_ref,
                        developer=manifest["developer"],
                        config=AgentConfig.model_validate(manifest["config"]),
                        current=current,
                    )
                    self.store.append(
                        study,
                        "ROUND_REGISTERED",
                        {"index": index, "round_id": rid, "pool_ref": pool_ref},
                    )
                report_ref = DreamEngine(self.store, self.provider).run(rid)
                active = PolicyRegistry(self.store).apply(registry, rid)
                report = self.store.artifacts.get(report_ref)
                reflection = {
                    "index": index,
                    "stage": label,
                    "round_id": rid,
                    "pool_ref": pool_ref,
                    "report_ref": report_ref,
                    "status": report["status"],
                    "reason": report["reason"],
                    "active_policy_id": active,
                    "next_action": "Execute new collections for missing branches"
                    if index == 0
                    else "Check another development proposal round; reuse of validation families cannot authorize promotion"
                    if index == 1
                    else "Run a previously unseen condition-shift canary",
                }
                self.store.append(study, "ROUND_RECORDED", reflection)
                records.append(reflection)
                if fault == "after_round":
                    raise JarvisError("injected study interruption after recorded round")
            current, previous = PolicyRegistry(self.store).active(registry)
            canary_ref = None
            existing_canary = [
                e["payload"]
                for e in self.store.events(study)
                if e["event_type"] == "CANARY_REGISTERED"
            ]
            if previous is not None or existing_canary:
                mapped = [
                    e["payload"]
                    for e in self.store.events(study)
                    if e["event_type"] == "CANARY_REGISTERED"
                ]
                if mapped:
                    canary = mapped[0]["canary_id"]
                else:
                    assert previous is not None
                    canary = self.store.create(
                        {
                            "kind": "PolicyCanary",
                            "registry_id": registry,
                            "pool_ref": pool_ref,
                            "active": current.model_dump(mode="json"),
                            "fallback": previous.model_dump(mode="json"),
                            "fixtures": [
                                Fixture(family="spurious_proxy", variant=v).model_dump(mode="json")
                                for v in range(2)
                            ],
                        }
                    )
                    self.store.append(study, "CANARY_REGISTERED", {"canary_id": canary})
                canary_ref = run_canary(self.store, canary)
                PolicyRegistry(self.store).rollback(registry, canary_ref)
            active_policy, _ = PolicyRegistry(self.store).active(registry)
            report = {
                "kind": "DreamStudyReport",
                "study_id": study,
                "scope": "SYNTHETIC_ONLY",
                "status": "COMPLETED",
                "rounds": records,
                "canary_ref": canary_ref,
                "registry_id": registry,
                "final_active_policy_id": active_policy.identity,
                "conclusion": "Coverage collection enabled a valid historical and fresh-fixture comparison. Plateau or failed validation retains the incumbent. A new shifted family can still regress and trigger rollback.",
                "limitations": [
                    "Three declared engineering rounds; policy fixtures or real developer are labeled per round",
                    "No general research, capability, Hermes or trading improvement claim",
                    "Additional independent families are required for further promotion after exposure",
                ],
            }
            ref = self.store.artifacts.put(report)
            self.store.append(study, "STUDY_COMPLETED", {"report_ref": ref})
            self.store.set_status(study, "CLOSED")
            return ref
