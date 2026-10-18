"""Explicit, scope-bound policy adoption for NEW reviewed research programs."""

from typing import Any

from jarvis.campaign import source_identity
from jarvis.contracts import Budget, IntegrityError
from jarvis.hermes_policy import HermesPolicy
from jarvis.identity import digest
from jarvis.program_costs import compare_full_programs, protocol_key
from jarvis.storage import Store


class ReviewedPolicyRegistry:
    def __init__(self, store: Store):
        self.store = store

    def create(self, policy: HermesPolicy, template: Any) -> str:
        value = {
            "policy": policy.model_dump(mode="json"),
            "protocol_id": protocol_key(template),
            "result_origin": template.queue.environment.result_origin,
            "source_identity": source_identity(),
        }
        return self.store.create(
            {
                "kind": "ReviewedPolicyRegistry",
                **value,
                "world_id": digest(value),
                "task": {"budget": Budget().model_dump()},
            }
        )

    def active(
        self, registry: str, template: Any, *, at_time: str | None = None
    ) -> tuple[HermesPolicy, str]:
        m = self.store.manifest(registry)
        if (
            m["kind"] != "ReviewedPolicyRegistry"
            or m["protocol_id"] != protocol_key(template)
            or m["result_origin"] != template.queue.environment.result_origin
            or (at_time is None and m["source_identity"] != source_identity())
        ):
            raise IntegrityError("policy registry scope/protocol/runtime mismatch")
        current = HermesPolicy.model_validate(m["policy"])
        if m["world_id"] != digest(
            {k: m[k] for k in ["policy", "protocol_id", "result_origin", "source_identity"]}
        ):
            raise IntegrityError("registry genesis changed")
        version = digest(m)
        for e in self.store.events(registry):
            if at_time is not None and e["recorded_at_utc"] > at_time:
                continue
            if e["event_type"] not in {"REVIEWED_POLICY_ACTIVATED", "REVIEWED_POLICY_ROLLED_BACK"}:
                continue
            p = e["payload"]
            if p["previous_policy_id"] != digest(current):
                raise IntegrityError("policy lineage changed")
            proof = self.store.artifacts.get(p["proof_ref"])
            verify_adoption_proof(self.store, proof)
            current = HermesPolicy.model_validate(p["policy"])
            expected = (
                proof["candidate"]
                if e["event_type"] == "REVIEWED_POLICY_ACTIVATED"
                else proof["incumbent"]
            )
            if current != HermesPolicy.model_validate(expected):
                raise IntegrityError("adopted policy differs from verified comparison")
            version = e["event_hash"]
        return current, version

    def change(
        self, registry: str, template: Any, proof: dict[str, Any], *, rollback: bool = False
    ) -> None:
        with self.store.campaign_lock(registry):
            verify_adoption_proof(self.store, proof)
            current, _ = self.active(registry, template)
            policy = HermesPolicy.model_validate(
                proof["incumbent"] if rollback else proof["candidate"]
            )
            if current == policy:
                return
            if (
                proof["protocol_id"] != protocol_key(template)
                or proof["result_origin"] != template.queue.environment.result_origin
            ):
                raise IntegrityError("adoption evidence belongs to another protocol/scope")
            self.store.append(
                registry,
                "REVIEWED_POLICY_ROLLED_BACK" if rollback else "REVIEWED_POLICY_ACTIVATED",
                {
                    "previous_policy_id": digest(current),
                    "policy": policy.model_dump(mode="json"),
                    "proof_ref": self.store.artifacts.put(proof),
                },
            )


def verify_adoption_proof(store: Store, proof: dict[str, Any]) -> None:
    from jarvis.hermes_policy import compare_policies

    if proof["kind"] not in {"ReviewedPolicyFreshProof", "ReviewedPolicyRollbackProof"}:
        raise IntegrityError("unrecognized policy admission proof")
    from jarvis.reviewed_dream import ReviewedDreamSpec

    m = store.manifest(proof["study_id"])
    if m["kind"] != "ReviewedDreamLoop":
        raise IntegrityError("policy proof has no declared Dream study")
    declaration = ReviewedDreamSpec.model_validate(m["spec"])
    events = store.events(proof["study_id"])
    frozen = [e for e in events if e["event_type"] == "REVIEWED_FINALIST_FROZEN"]
    phase = "fresh" if proof["kind"] == "ReviewedPolicyFreshProof" else "canary"
    phases = [
        store.artifacts.get(e["payload"]["result_ref"])
        for e in events
        if e["event_type"] == "REVIEWED_PAIRED_PHASE_COMPLETED" and e["payload"]["phase"] == phase
    ]
    heldout = tuple(
        e["payload"]["world_ref"]
        for e in events
        if e["event_type"] == "REVIEWED_HISTORY_COLLECTED"
        and e["payload"]["key"].startswith("heldout:")
    )
    if (
        len(frozen) != 1
        or frozen[0]["payload"]["policy_ref"] != proof["finalist_ref"]
        or store.artifacts.get(proof["finalist_ref"]) != proof["candidate"]
        or declaration.incumbent.model_dump(mode="json") != proof["incumbent"]
        or len(phases) != 1
        or phases[0]["pairs"] != proof["pairs"]
        or heldout != tuple(proof["heldout"])
        or proof["catalog_budget"] != declaration.catalog_budget
    ):
        raise IntegrityError("policy proof escaped its frozen finalist/cohorts")
    for pair in proof["pairs"]:
        if any(
            store.manifest(pair[arm])["created_at"] < frozen[0]["recorded_at_utc"]
            for arm in ["incumbent", "candidate"]
        ):
            raise IntegrityError("fresh policy arm predates the frozen finalist")
    from jarvis.research_program import ResearchProgramSpec

    tasks = declaration.fresh if phase == "fresh" else declaration.canary
    if len(tasks) != len(proof["pairs"]):
        raise IntegrityError("adoption proof omitted declared tasks")
    for task, pair in zip(tasks, proof["pairs"], strict=True):
        for arm in ["incumbent", "candidate"]:
            expected = task.program.model_copy(
                update={"research_policy": HermesPolicy.model_validate(proof[arm])}
            )
            if ResearchProgramSpec.model_validate(store.manifest(pair[arm])["spec"]) != expected:
                raise IntegrityError("adoption proof arm escaped its declared task")
    pairs = [
        compare_full_programs(store, p["incumbent"], p["candidate"], strict=False)
        for p in proof["pairs"]
    ]
    if not pairs:
        raise IntegrityError("empty policy comparison proof")
    for pair in pairs:
        for arm in ["incumbent", "candidate"]:
            value = pair["arms"][arm]
            if value["policy"] != proof[arm] or value["protocol_id"] != proof["protocol_id"]:
                raise IntegrityError("policy proof changed its arms or protocol")
            m = store.manifest(value["program_id"])
            if m["spec"]["queue"]["environment"]["result_origin"] != proof["result_origin"]:
                raise IntegrityError(
                    "synthetic policy evidence cannot authorize an observed backend"
                )
    if proof["kind"] == "ReviewedPolicyFreshProof":
        if not all(p["nonregression"] for p in pairs) or not any(p["strict_gain"] for p in pairs):
            raise IntegrityError("no full-cost fresh policy improvement")
        candidate, incumbent = [
            HermesPolicy.model_validate(proof[a]) for a in ["candidate", "incumbent"]
        ]
        held = compare_policies(
            store,
            tuple(proof["heldout"]),
            candidate,
            incumbent,
            proof["catalog_budget"],
            strict=False,
        )
        if not held["accepted"]:
            raise IntegrityError("policy lacks independent heldout acceptance")
    elif all(p["nonregression"] for p in pairs):
        raise IntegrityError("rollback proof has no measured regression")
