"""Explicit foreground mechanism research; preflight never creates a store or starts work."""

import argparse
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

import yaml
from pydantic import Field

from jarvis.contracts import AdmissionError, Budget, Record
from jarvis.identity import digest, raw_digest
from jarvis.mechanism_research import (
    MechanismResearch,
    MechanismResearchSpec,
    replay_workflow,
    validate_experiment_contract,
    workflow_report,
)
from jarvis.research_contract import ResearchContract
from jarvis.research_notebook import export_notebook
from jarvis.research_program import ResearchProgramSpec
from jarvis.research_skills import SkillRegistry
from jarvis.researcher import AgentConfig
from jarvis.skill_sandbox import SkillSandbox
from jarvis.storage import Store
from jarvis.trading_diagnostics import (
    DiagnosticInput,
    DiagnosticSnapshot,
    import_snapshot_csv,
    inventory,
    snapshot_bytes,
)


class ContractDeclaration(ResearchContract):
    baseline_refs: tuple[str, ...] = ()


class SnapshotFile(Record):
    path: str
    sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class ResearchDeclaration(Record):
    kind: Literal["MechanismResearchDeclaration"] = "MechanismResearchDeclaration"
    contract: ContractDeclaration
    baseline_evidence: tuple[dict[str, Any], ...] = Field(min_length=1)
    inputs: tuple[SnapshotFile, ...] = Field(min_length=2, max_length=16)
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


class CSVDeclaration(Record):
    path: str
    metadata: dict[str, Any]
    columns: dict[str, str]


def preflight(path: Path) -> dict[str, Any]:
    declaration = ResearchDeclaration.model_validate(yaml.safe_load(path.read_text()))
    refs = tuple(digest(b) for b in declaration.baseline_evidence)
    if declaration.contract.baseline_refs and declaration.contract.baseline_refs != refs:
        raise AdmissionError("declared baseline identities do not match supplied baseline evidence")
    contract = ResearchContract.model_validate(
        {**declaration.contract.model_dump(mode="json"), "baseline_refs": refs}
    )
    validate_experiment_contract(contract, declaration.program)
    # Reuse the exact execution identity/model checks without creating a store/provider.
    MechanismResearchSpec(
        contract_ref=digest(contract),
        dataset_refs=tuple(asset.sha256 for asset in declaration.inputs),
        registry="declaration-only",
        config=declaration.config,
        budget=declaration.budget,
        program=declaration.program,
    )
    snapshots = []
    for asset in declaration.inputs:
        source = Path(asset.path).expanduser()
        if not source.is_absolute():
            source = path.parent / source
        raw = snapshot_bytes(source)
        if raw_digest(raw) != asset.sha256:
            raise AdmissionError("snapshot size/checksum mismatch")
        snapshot = DiagnosticSnapshot.model_validate_json(raw)
        if (snapshot.dataset_id, snapshot.evaluator_id, snapshot.exposure) != (
            contract.dataset_id,
            contract.evaluator_id,
            contract.exposure,
        ):
            raise AdmissionError("input lineage/exposure does not match owner intent")
        snapshots.append(snapshot)
    return {
        "status": "DECLARATION_VALID",
        "contract": contract.model_dump(mode="json"),
        "snapshots": [s.model_dump(mode="json") for s in snapshots],
        "inventory": [
            inventory(DiagnosticInput(baseline=s, candidate=s)).model_dump(mode="json")
            for s in snapshots
        ],
        "baseline_evidence": list(declaration.baseline_evidence),
        "program": declaration.program.model_dump(mode="json") if declaration.program else None,
        "builder_repository": str((path.parent / declaration.builder_repository).resolve())
        if declaration.builder_repository
        else None,
        "config": declaration.config.model_dump(mode="json"),
        "budget": declaration.budget.model_dump(mode="json"),
        "execution_started": False,
        "provider_calls": 0,
        "backend_submissions": 0,
        "limitations": [
            "Declaration/available-row checks only; does not establish complete opportunity coverage, remote availability or independent validation."
        ],
    }


def add_commands(commands: Any) -> None:
    workflow = commands.add_parser("workflow").add_subparsers(dest="operation", required=True)
    workflow.add_parser("fixture")
    workflow.add_parser("sandbox-check")
    workflow.add_parser("preflight").add_argument("declaration", type=Path)
    workflow.add_parser("start").add_argument("declaration", type=Path)
    for name in ("advance", "report", "replay", "successor"):
        workflow.add_parser(name).add_argument("owner")
    run = workflow.add_parser("run")
    run.add_argument("owner")
    run.add_argument("--max-steps", type=int, default=200)
    run.add_argument("--wall-seconds", type=float, default=3600)
    run.add_argument("--poll-seconds", type=float, default=2)
    notebook = workflow.add_parser("notebook")
    notebook.add_argument("owner")
    notebook.add_argument("directory", type=Path)
    workflow.add_parser("skill-list").add_argument("registry")
    install = workflow.add_parser("skill-install")
    install.add_argument("registry")
    install.add_argument("directory", type=Path)
    workflow.add_parser("import-csv").add_argument("declaration", type=Path)


def sandbox_check() -> dict[str, Any]:
    with TemporaryDirectory(prefix="jarvis-sandbox-check-") as tmp:
        result = SkillSandbox().probe(Path(tmp) / "probe")
        return {
            "status": result["status"],
            "sandbox": result["sandbox"],
            "stderr": result.get("stderr", result.get("reason")),
            "external_calls": 0,
        }


def notebook(store: Store, owner: str, directory: Path) -> dict[str, Any]:
    report = workflow_report(store, owner)
    export_notebook(store, owner, directory)
    trace = json.loads((directory / "audit.json").read_text())
    registry = store.manifest(owner)["spec"]["registry"]
    audit = {
        **trace,
        **report,
        "manifest": store.manifest(owner),
        "owner_events": store.events(owner),
        "skill_registry": {
            "manifest": store.manifest(registry),
            "events": store.events(registry),
        },
        "jobs": [j for m in store.resource_members(owner) for j in store.jobs(m)],
    }
    (directory / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# Mechanism research notebook",
        "",
        "Status: " + report["status"],
        "",
        "Complete owner intent, decision-time evidence, questions, skills, role receipts and actual/pending resources are in audit.json.",
        "Actual role contexts and structured answers include child experiments; raw provider files and generated source remain in the private store. No hidden reasoning is inferred.",
        "",
    ]
    for p in report["decisions"]:
        decision = store.artifacts.get(p["decision_ref"])
        lines += [
            f"## Decision {p['index']}: {decision['action']['kind']}",
            "",
            decision["action"]["rationale"],
            "",
            "Expected observation: " + decision["action"]["expected_observation"],
            "",
            "Evidence: " + ", ".join(decision["action"]["evidence_refs"]),
            "",
        ]
    lines += [
        "## Resources",
        "",
        json.dumps(report["usage"], ensure_ascii=False),
        "",
        report["boundary"],
    ]
    (directory / "NOTEBOOK.md").write_text("\n".join(lines) + "\n")
    return {"status": "EXPORTED", "directory": str(directory), "external_calls": 0}


def run(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    engine = MechanismResearch(store)
    if args.operation == "fixture":
        from jarvis.mechanism_fixture import run_mechanism_fixture

        return run_mechanism_fixture(store)
    if args.operation == "start":
        prepared = preflight(args.declaration)
        registry = SkillRegistry.create(store)
        domain = prepared["snapshots"][0]["domain"]
        builtins: tuple[
            tuple[str, Literal["inventory-v1", "paired-attribution-v1", "score-distribution-v1"]],
            ...,
        ] = (
            ("inventory", "inventory-v1"),
            ("trade_attribution", "paired-attribution-v1"),
            ("score_distribution", "score-distribution-v1"),
        )
        for name, protocol in builtins:
            ref = registry.define_builtin(name, protocol, domain)
            registry.register(ref, registry.validate(registry.registry, ref))
        for value in prepared["baseline_evidence"]:
            store.artifacts.put(value)
        spec = MechanismResearchSpec(
            contract_ref=store.artifacts.put(prepared["contract"]),
            dataset_refs=tuple(store.artifacts.put(s) for s in prepared["snapshots"]),
            registry=registry.registry,
            config=AgentConfig.model_validate(prepared["config"]),
            budget=Budget.model_validate(prepared["budget"]),
            builder_repository=prepared["builder_repository"],
            program=ResearchProgramSpec.model_validate(prepared["program"])
            if prepared["program"]
            else None,
        )
        return {"status": "DECLARED", "owner": engine.create(spec), "execution_started": False}
    if args.operation == "import-csv":
        data = CSVDeclaration.model_validate(yaml.safe_load(args.declaration.read_text()))
        path = Path(data.path).expanduser()
        if not path.is_absolute():
            path = args.declaration.parent / path
        value = import_snapshot_csv(path, data.metadata, data.columns)
        return {
            "snapshot_ref": store.artifacts.put(value),
            "snapshot": value.model_dump(mode="json"),
            "external_calls": 0,
        }
    if args.operation == "advance":
        return engine.advance(args.owner)
    if args.operation == "report":
        return workflow_report(store, args.owner)
    if args.operation == "replay":
        return replay_workflow(store, args.owner)
    if args.operation == "successor":
        return {"successor": engine.successor(args.owner)}
    if args.operation == "skill-list":
        return SkillRegistry(store, args.registry).snapshot()
    if args.operation == "skill-install":
        return {
            "skill_ref": SkillRegistry(store, args.registry).install_package(args.directory),
            "external_calls": 0,
        }
    if args.operation == "notebook":
        return notebook(store, args.owner, args.directory)
    if args.operation == "run":
        if args.max_steps < 1 or args.wall_seconds <= 0 or args.poll_seconds < 0:
            raise AdmissionError("invalid runner bounds")
        with store.campaign_lock(args.owner, scope="workflow-runner"):
            deadline = time.monotonic() + args.wall_seconds
            for _ in range(args.max_steps):
                result = engine.advance(args.owner)
                if result["status"] in {
                    "CLOSED",
                    "BUDGET_EXHAUSTED",
                    "NEEDS_RECONCILIATION",
                    "PAUSED",
                    "CANCELLED",
                }:
                    return result
                if time.monotonic() >= deadline:
                    break
                time.sleep(args.poll_seconds)
            return {
                "status": "RUNNER_LIMIT",
                "owner": args.owner,
                "resumption": "same owner/store; no background process created",
            }
    raise AdmissionError("unknown workflow operation")
