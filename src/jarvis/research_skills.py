"""Content-pinned skill registry and receipt-backed deterministic dispatch."""

import uuid
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from jarvis.contracts import AdmissionError, Budget, Cost, IntegrityError, Record
from jarvis.identity import digest, file_digest
from jarvis.lab_tools import run_tool
from jarvis.research_contract import SkillManifest
from jarvis.skill_sandbox import SkillSandbox
from jarvis.storage import Store
from jarvis.trading_diagnostics import (
    DiagnosticInput,
    DiagnosticResult,
    attribution,
    inventory,
    score_distribution,
)

BUILTINS = {
    "inventory-v1": inventory,
    "paired-attribution-v1": attribution,
    "score-distribution-v1": score_distribution,
}


class SkillPackage(Record):
    kind: Literal["BuiltinSkillPackage"] = "BuiltinSkillPackage"
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,79}$")
    version: Literal["1.0.0"] = "1.0.0"
    protocol: Literal["inventory-v1", "paired-attribution-v1", "score-distribution-v1"]
    domain: str = Field(min_length=1)
    applicability: str = Field(min_length=1)
    files: dict[Literal["CARD.md", "implementation.py", "test_skill.py"], str]
    dependencies: tuple[Literal["jarvis-research>=0.14,<0.15"], ...] = (
        "jarvis-research>=0.14,<0.15",
    )
    permissions: tuple[Literal["read_input_snapshot", "write_private_output"], ...] = (
        "read_input_snapshot",
        "write_private_output",
    )
    origin: Literal["human_provided"] = "human_provided"

    @model_validator(mode="after")
    def complete_files(self) -> "SkillPackage":
        if set(self.files) != {"CARD.md", "implementation.py", "test_skill.py"}:
            raise ValueError("skill package requires card, implementation and test")
        return self


def diagnostic_case(index: int = 0) -> DiagnosticInput:
    from jarvis.trading_diagnostics import DiagnosticRow, DiagnosticSnapshot

    common: dict[str, Any] = {
        "domain": "artificial",
        "model_id": "model-fixed",
        "evaluator_id": "pl-v1",
        "dataset_id": "fixture-development",
        "period_id": "period-a",
        "entity_kind": "fill",
        "stable_keyspace": "declared-entity-v1",
        "id_provenance": "Declared synthetic stable keys",
        "pl_units": "artificial units",
        "exposure": "synthetic",
        "origin": "declared_fixture",
        "source_checksum": digest({"artificial_case": index}),
    }
    a: list[dict[str, Any]] = [
        {"entity_id": "shared", "pl": "10", "score": 0.2},
        {"entity_id": "removed", "pl": "2"},
        {"entity_id": None, "pl": "3"},
    ]
    b: list[dict[str, Any]] = [
        {"entity_id": "shared", "pl": "8", "score": 0.4},
        {"entity_id": "added", "pl": "7"},
        {"entity_id": None, "pl": "5"},
    ]
    if index == 1:
        b.append({"entity_id": "shared", "pl": "1"})
    elif index == 2:
        a, b = [], []
    elif index == 3:
        a, b = b, a
    return DiagnosticInput(
        baseline=DiagnosticSnapshot(
            **common, policy_id="baseline", rows=tuple(DiagnosticRow(**r) for r in a)
        ),
        candidate=DiagnosticSnapshot(
            **common, policy_id="candidate", rows=tuple(DiagnosticRow(**r) for r in b)
        ),
    )


class SkillRegistry:
    def __init__(self, store: Store, registry: str, sandbox: SkillSandbox | None = None):
        self.store, self.registry, self.sandbox = store, registry, sandbox or SkillSandbox()
        if store.manifest(registry)["kind"] != "ResearchSkillRegistry":
            raise IntegrityError("not a skill registry")

    @classmethod
    def create(cls, store: Store) -> "SkillRegistry":
        owner = store.create(
            {
                "kind": "ResearchSkillRegistry",
                "world_id": digest({"registry": uuid.uuid4().hex}),
                "task": {
                    "budget": Budget(
                        max_actions=1000, max_tool_calls=1000, max_wall_seconds=10000
                    ).model_dump()
                },
            }
        )
        return cls(store, owner)

    def define_builtin(
        self,
        name: str,
        protocol: Literal["inventory-v1", "paired-attribution-v1", "score-distribution-v1"],
        domain: str = "artificial",
    ) -> str:
        if protocol not in BUILTINS:
            raise AdmissionError("unregistered builtin")
        source = self.store.artifacts.put(
            {
                "builtin": protocol,
                "implementation_sha256": file_digest(
                    Path(__file__).with_name("trading_diagnostics.py")
                ),
            }
        )
        provenance = self.store.artifacts.put(
            {
                "kind": "SkillSource",
                "origin": "human_provided",
                "description": "Trusted versioned diagnostic implementation; no measured utility claim",
            }
        )
        return self.store.artifacts.put(
            SkillManifest(
                name=name,
                version="1.0.0",
                protocol=protocol,
                implementation="trusted_builtin",
                source_ref=source,
                input_schema_ref=self.store.artifacts.put(DiagnosticInput.model_json_schema()),
                output_schema_ref=self.store.artifacts.put(DiagnosticResult.model_json_schema()),
                applicability="Explicit compatible snapshots; no inferred opportunity coverage or causality",
                domain=domain,
                source_refs=(provenance,),
                origin="human_provided",
                dependencies=("python-stdlib", "jarvis-research"),
            )
        )

    def install_package(self, directory: Path) -> str:
        declaration = SkillPackage.model_validate(
            yaml.safe_load((directory / "manifest.yaml").read_text())
        )
        contents = {}
        for name, checksum in declaration.files.items():
            path = directory / name
            if path.is_symlink() or path.stat().st_size > 50000 or file_digest(path) != checksum:
                raise AdmissionError("skill package source checksum/size mismatch")
            contents[name] = path.read_text()
        package_ref = self.store.artifacts.put(
            {
                "kind": "BuiltinSkillPackageSource",
                "declaration": declaration.model_dump(mode="json"),
                "files": contents,
                "execution": "Only the declared closed builtin executes; package Python is never dynamically imported",
            }
        )
        base_ref = self.define_builtin(declaration.name, declaration.protocol, declaration.domain)
        base = self.manifest(base_ref)
        ref = self.store.artifacts.put(
            base.model_copy(
                update={
                    "source_refs": (*base.source_refs, package_ref),
                    "applicability": declaration.applicability,
                }
            )
        )
        self.register(ref, self.validate(self.registry, ref))
        return ref

    def manifest(self, ref: str) -> SkillManifest:
        m = SkillManifest.model_validate(self.store.artifacts.get(ref))
        if (
            self.store.artifacts.get(m.input_schema_ref) != DiagnosticInput.model_json_schema()
            or self.store.artifacts.get(m.output_schema_ref) != DiagnosticResult.model_json_schema()
        ):
            raise IntegrityError("skill IO protocol changed")
        return m

    def evaluate(self, ref: str, data: DiagnosticInput, directory: Path) -> DiagnosticResult:
        m = self.manifest(ref)
        if m.domain != data.baseline.domain:
            raise AdmissionError("skill domain mismatch")
        source = self.store.artifacts.get(m.source_ref)
        if m.implementation == "trusted_builtin":
            if source != {
                "builtin": m.protocol,
                "implementation_sha256": file_digest(
                    Path(__file__).with_name("trading_diagnostics.py")
                ),
            }:
                raise IntegrityError("builtin source identity changed")
            return BUILTINS[m.protocol](data)
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        source_path = directory / "implementation.py"
        source_path.write_text(source["implementation.py"])
        result = self.sandbox.run(
            source_path, data.model_dump(mode="json"), directory / "execution"
        )
        if result["status"] != "PASS":
            raise AdmissionError("generated skill execution failed: " + result["stderr"][:500])
        return DiagnosticResult.model_validate(result["value"])

    def validate(self, owner: str, ref: str) -> str:
        m = self.manifest(ref)

        def check() -> dict[str, Any]:
            cases = []
            errors: list[str | None] = []
            for i in range(4):
                if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
                    raise AdmissionError("owner stopped validation")
                data = diagnostic_case(i)
                if m.domain != "artificial":
                    data = data.model_copy(
                        update={
                            "baseline": data.baseline.model_copy(update={"domain": m.domain}),
                            "candidate": data.candidate.model_copy(update={"domain": m.domain}),
                        }
                    )
                expected = BUILTINS[m.protocol](data)
                if m.protocol == "paired-attribution-v1" and i in {0, 3}:
                    wanted = ("-2", "5", "2") if i == 0 else ("2", "-5", "-2")
                    if (
                        tuple(
                            expected.facts[k]
                            for k in ("shared_pl_delta", "total_pl_delta", "unmatched_residual")
                        )
                        != wanted
                    ):
                        raise IntegrityError("trusted oracle failed fixed independent answers")
                try:
                    actual = self.evaluate(
                        ref, data, self.store.root / "skill-validation" / uuid.uuid4().hex
                    )
                    cases.append(
                        (actual.status, actual.evidence_level, actual.facts)
                        == (expected.status, expected.evidence_level, expected.facts)
                    )
                    errors.append(None)
                except (AdmissionError, ValueError) as exc:
                    cases.append(False)
                    errors.append(str(exc)[:1000])
            return {
                "kind": "SkillFidelity",
                "skill_ref": ref,
                "cases": cases,
                "errors": errors,
                "passed": all(cases),
                "utility": "UNVERIFIED",
                "validator": "independent-fixed-diagnostic-cases-v1",
            }

        value = run_tool(
            self.store,
            owner,
            "skill-validation:" + ref,
            {"skill_ref": ref, "protocol": m.protocol},
            check,
            reserved_cost=Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=40),
        )
        return self.store.artifacts.put({**value, "producer": owner})

    def register(self, ref: str, validation_ref: str) -> None:
        m = self.manifest(ref)
        proof = self.store.artifacts.get(validation_ref)
        receipts = [
            e["payload"]
            for e in self.store.events(proof["producer"])
            if e["event_type"] == "LAB_TOOL_COMPLETED"
            and e["payload"]["key"] == "skill-validation:" + ref
        ]
        expected = {k: v for k, v in proof.items() if k != "producer"}
        if (
            proof["skill_ref"] != ref
            or not proof["passed"]
            or len(receipts) != 1
            or receipts[0]["error"] is not None
            or receipts[0]["value_ref"] != digest(expected)
        ):
            raise IntegrityError("skill requires receipted independent fidelity")
        with self.store.campaign_lock(self.registry):
            historical = [
                e["payload"]["skill_ref"]
                for e in self.store.events(self.registry)
                if e["event_type"] == "SKILL_REGISTERED"
            ]
            for old in historical:
                prior = self.manifest(old)
                if prior.name == m.name and prior.version == m.version:
                    if old != ref:
                        raise IntegrityError("skill name/version is immutable")
                    return
            self.store.append(
                self.registry,
                "SKILL_REGISTERED",
                {
                    "skill_ref": ref,
                    "validation_ref": validation_ref,
                    "status": "APPROVED_FOR_DIAGNOSTIC_USE",
                    "utility": "UNVERIFIED",
                },
            )

    def snapshot(self) -> dict[str, Any]:
        events = self.store.events(self.registry)
        return self._snapshot(events)

    def _snapshot(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        disabled = {
            e["payload"]["skill_ref"] for e in events if e["event_type"] == "SKILL_DISABLED"
        }
        skills = [
            e["payload"]["skill_ref"]
            for e in events
            if e["event_type"] == "SKILL_REGISTERED" and e["payload"]["skill_ref"] not in disabled
        ]
        for ref in skills:
            self.manifest(ref)
        return {
            "registry": self.registry,
            "prefix_hash": events[-1]["event_hash"],
            "skills": skills,
        }

    def verify_snapshot(self, snapshot: dict[str, Any]) -> None:
        events = self.store.events(self.registry)
        prefix = next(
            (i for i, e in enumerate(events) if e["event_hash"] == snapshot.get("prefix_hash")),
            None,
        )
        if prefix is None or self._snapshot(events[: prefix + 1]) != snapshot:
            raise IntegrityError("skill registry snapshot is not an admitted historical prefix")

    def dispatch(
        self, owner: str, ref: str, data: DiagnosticInput, *, role: str = "director"
    ) -> dict[str, Any]:
        m = self.manifest(ref)
        if ref not in self.snapshot()["skills"] or role not in m.allowed_roles:
            raise AdmissionError("skill is unavailable, disabled or not allowed for role")
        request = {"skill_ref": ref, "input_ref": self.store.artifacts.put(data), "role": role}
        result = run_tool(
            self.store,
            owner,
            "skill-dispatch:" + digest(request),
            request,
            lambda: self.evaluate(
                ref, data, self.store.root / "skill-execution" / uuid.uuid4().hex
            ).model_dump(mode="json"),
        )
        return {"skill_ref": ref, "result": result, "utility": "UNVERIFIED"}
