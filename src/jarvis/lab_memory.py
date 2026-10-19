"""Scoped, evidence-backed experience; declarative capability registration."""

from typing import Any, Literal

from jarvis.campaign import source_identity
from jarvis.contracts import Budget, IntegrityError, Record
from jarvis.identity import digest
from jarvis.lab_contracts import CapabilityDefinition, ExperienceProposal, LabSpec
from jarvis.lab_tools import fidelity_check, run_tool
from jarvis.research_program import program_report
from jarvis.storage import Store


class ResearchExperience(Record):
    kind: Literal["ResearchExperience"] = "ResearchExperience"
    scope_id: str
    producer: str
    program_id: str
    proposal: ExperienceProposal
    observations: tuple[dict[str, Any], ...]
    exposure: str
    interpretation_status: Literal["PROVISIONAL"] = "PROVISIONAL"
    origin: Literal["agent_proposed"] = "agent_proposed"


def scope_id(spec: LabSpec) -> str:
    env = spec.program.queue.environment
    return digest(
        {
            "family": spec.family,
            "evaluator": env.evaluator_sha256,
            "template": env.template_sha256,
            "side": env.side,
            "result_origin": env.result_origin,
        }
    )


def make_experience(store: Store, owner: str, program: str, proposal: ExperienceProposal) -> str:
    spec = LabSpec.model_validate(store.manifest(owner)["spec"])
    report = program_report(store, program)
    results = {e["result_ref"]: e for e in report["experiments"]}
    if report["status"] != "CLOSED" or not set(
        (*proposal.evidence_refs, *proposal.counter_evidence_refs)
    ) <= set(results):
        raise IntegrityError("curator claim lacks actual completed experiment evidence")
    record = ResearchExperience(
        scope_id=scope_id(spec),
        producer=owner,
        program_id=program,
        proposal=proposal,
        observations=tuple(results.values()),
        exposure=spec.program.queue.environment.period.exposure,
    )
    return store.artifacts.put(record)


def verify_experience(store: Store, ref: str) -> ResearchExperience:
    value = ResearchExperience.model_validate(store.artifacts.get(ref))
    producer = store.manifest(value.producer)
    if producer["kind"] != "ResearchLabEpisode" or value.scope_id != scope_id(
        LabSpec.model_validate(producer["spec"])
    ):
        raise IntegrityError("experience scope changed")
    report = program_report(store, value.program_id)
    if (
        report["status"] != "CLOSED"
        or list(value.observations) != report["experiments"]
        or not any(
            e["event_type"] == "LAB_EXPERIENCE_CREATED" and e["payload"]["experience_ref"] == ref
            for e in store.events(value.producer)
        )
    ):
        raise IntegrityError("experience differs from actual producer evidence")
    return value


def retrieve(store: Store, spec: LabSpec, *, before: str) -> dict[str, Any]:
    accepted, rejected = [], []
    for ref in spec.experience_refs:
        value = verify_experience(store, ref)
        produced = next(
            e
            for e in store.events(value.producer)
            if e["event_type"] == "LAB_EXPERIENCE_CREATED" and e["payload"]["experience_ref"] == ref
        )
        if (
            value.scope_id == scope_id(spec)
            and value.exposure == "development_exposed"
            and produced["recorded_at_utc"] <= before
            and store.manifest(value.producer)["source_identity"] == source_identity()
        ):
            accepted.append({"experience_ref": ref, "experience": value.model_dump(mode="json")})
        else:
            rejected.append(
                {"experience_ref": ref, "reason": "INCOMPATIBLE_OR_RESTRICTED_OR_FUTURE"}
            )
    return {
        "available": list(spec.experience_refs),
        "accepted": accepted,
        "rejected": rejected,
        "claim_status": "provisional interpretations; actual observations retain source refs",
    }


def create_capability(store: Store, definition: CapabilityDefinition, owner: str) -> str:
    sources = [store.artifacts.get(r) for r in definition.source_refs]
    if any(s.get("kind") != "SourceDocument" for s in sources):
        raise IntegrityError("capability needs source provenance")
    fidelity = run_tool(
        store,
        owner,
        "capability-fidelity:" + digest(definition),
        {"definition": definition.model_dump(mode="json"), "fixed_cases": "group-mean-argmax:v1"},
        lambda: fidelity_check(definition),
    )
    return store.artifacts.put(
        {
            "kind": "CapabilityCandidate",
            "definition": definition.model_dump(mode="json"),
            "fidelity": fidelity,
            "producer": owner,
            "status": "EXPERIMENTAL",
            "utility": "UNVERIFIED",
        }
    )


def capability_definition(store: Store, ref: str) -> CapabilityDefinition:
    value = store.artifacts.get(ref)
    definition = CapabilityDefinition.model_validate(value["definition"])
    checks = [
        p["payload"]
        for p in store.events(value["producer"])
        if p["event_type"] == "LAB_TOOL_COMPLETED"
        and p["payload"]["key"] == "capability-fidelity:" + digest(definition)
    ]
    if (
        value["kind"] != "CapabilityCandidate"
        or len(checks) != 1
        or checks[0]["error"] is not None
        or checks[0]["value_ref"] != digest(value["fidelity"])
    ):
        raise IntegrityError("capability fidelity/version changed")
    return definition


class ResearchBoard:
    """User-owned durable issue board. New episodes pin a snapshot of its inputs."""

    def __init__(self, store: Store):
        self.store = store

    def create(self, goal: str) -> str:
        return self.store.create(
            {
                "kind": "ResearchBoard",
                "goal": goal,
                "world_id": digest({"goal": goal, "kind": "ResearchBoard"}),
                "task": {"budget": Budget().model_dump()},
            }
        )

    def add(self, board: str, artifact_ref: str) -> None:
        from jarvis.lab_contracts import HumanInput, SourceDocument

        if self.store.manifest(board)["kind"] != "ResearchBoard":
            raise IntegrityError("not a research board")
        value = self.store.artifacts.get(artifact_ref)
        kind = value.get("kind")
        if kind == "HumanResearchInput":
            HumanInput.model_validate(value)
        elif kind == "SourceDocument":
            SourceDocument.model_validate(value)
        elif kind == "ResearchExperience":
            verify_experience(self.store, artifact_ref)
        else:
            raise IntegrityError("unsupported board artifact")
        with self.store.campaign_lock(board):
            if not any(
                e["event_type"] == "BOARD_ITEM_ADDED"
                and e["payload"]["artifact_ref"] == artifact_ref
                for e in self.store.events(board)
            ):
                self.store.append(
                    board, "BOARD_ITEM_ADDED", {"artifact_ref": artifact_ref, "kind": kind}
                )

    def snapshot(self, board: str) -> dict[str, Any]:
        if self.store.manifest(board)["kind"] != "ResearchBoard":
            raise IntegrityError("not a research board")
        events = self.store.events(board)
        return {
            "board": board,
            "prefix_hash": events[-1]["event_hash"],
            "items": [e["payload"] for e in events if e["event_type"] == "BOARD_ITEM_ADDED"],
        }


def pin_board(store: Store, spec: LabSpec) -> LabSpec:
    if spec.board_id is None:
        if spec.board_snapshot_ref is not None:
            raise IntegrityError("board snapshot has no board identity")
        return spec
    ref = spec.board_snapshot_ref or store.artifacts.put(
        ResearchBoard(store).snapshot(spec.board_id)
    )
    snapshot = store.artifacts.get(ref)
    events = store.events(spec.board_id)
    prefix = next(
        (
            events[: i + 1]
            for i, e in enumerate(events)
            if e["event_hash"] == snapshot["prefix_hash"]
        ),
        None,
    )
    if (
        snapshot["board"] != spec.board_id
        or prefix is None
        or snapshot["items"]
        != [e["payload"] for e in prefix if e["event_type"] == "BOARD_ITEM_ADDED"]
    ):
        raise IntegrityError("board snapshot differs from its sealed event prefix")
    names = {
        "SourceDocument": "source_refs",
        "HumanResearchInput": "human_refs",
        "ResearchExperience": "experience_refs",
    }
    update: dict[str, Any] = {"board_snapshot_ref": ref}
    for kind, name in names.items():
        update[name] = tuple(
            dict.fromkeys(
                [
                    *getattr(spec, name),
                    *[p["artifact_ref"] for p in snapshot["items"] if p["kind"] == kind],
                ]
            )
        )
    return LabSpec.model_validate(spec.model_copy(update=update).model_dump())
