"""Separate structured coding role, disposable Git worktree and trusted validation."""

import ast
import subprocess
from pathlib import Path
from typing import Any

from pydantic import Field

from jarvis.bounded_roles import evidence_role
from jarvis.contracts import AdmissionError, Record
from jarvis.identity import digest
from jarvis.lab_tools import run_tool
from jarvis.research_contract import BuildSkillAction, SkillManifest
from jarvis.research_skills import SkillRegistry
from jarvis.researcher import AgentConfig
from jarvis.skill_sandbox import SandboxUnavailable
from jarvis.storage import Store
from jarvis.trading_diagnostics import DiagnosticInput, DiagnosticResult


class BuilderSource(Record):
    context_id: str
    implementation: str = Field(min_length=1, max_length=30000)
    self_test: str = Field(min_length=1, max_length=12000)
    card: str = Field(min_length=1, max_length=5000)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Jarvis Skill Builder",
            "-c",
            "user.email=builder@invalid.example",
            "-C",
            str(repository),
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise AdmissionError("builder Git operation failed: " + result.stderr[:500])
    return result.stdout.strip()


class SkillBuilder:
    def __init__(self, store: Store, registry: SkillRegistry, provider_factory: Any):
        self.store, self.registry, self.provider_factory = store, registry, provider_factory

    def build(
        self,
        owner: str,
        action: BuildSkillAction,
        context: dict[str, Any],
        config: AgentConfig,
        repository: Path | None,
        repository_commit: str | None,
    ) -> dict[str, Any]:
        if repository is None or repository_commit is None:
            return {"status": "UNAVAILABLE", "reason": "No declared builder repository"}
        if not self.registry.sandbox.identity()["available"]:
            return {
                "status": "UNAVAILABLE",
                "reason": "No OS sandbox; no builder/provider call made",
            }
        key = "skill-build:" + digest(action)
        done = [
            e["payload"]
            for e in self.store.events(owner)
            if e["event_type"] == "SKILL_BUILD_COMPLETED" and e["payload"]["key"] == key
        ]
        if done:
            return done[0]
        if self.store.get_control(owner) in {"PAUSED", "CANCELLED"}:
            raise AdmissionError("owner stopped skill building")
        probe = run_tool(
            self.store,
            owner,
            key + ":sandbox-probe",
            {"sandbox": self.registry.sandbox.identity()},
            lambda: self.registry.sandbox.probe(
                self.store.root / "builder-probes" / digest(action)[7:]
            ),
        )
        if probe["status"] != "PASS":
            return {
                "status": "UNAVAILABLE",
                "reason": "OS isolation probe failed; no builder/provider call made",
                "probe_ref": self.store.artifacts.put(probe),
            }

        def validate(value: Record, frozen: dict[str, Any]) -> None:
            assert isinstance(value, BuilderSource)
            if not set(value.evidence_refs) <= set(frozen["visible_evidence_ids"]):
                raise ValueError("builder source provenance escaped input")
            for code, required in ((value.implementation, "run"), (value.self_test, "self_test")):
                try:
                    tree = ast.parse(code)
                except SyntaxError as exc:
                    raise ValueError("invalid Python source: " + str(exc)) from exc
                if not any(
                    isinstance(n, ast.FunctionDef) and n.name == required for n in tree.body
                ):
                    raise ValueError("builder must define " + required)

        builder_context = {
            "research_contract": context["research_contract"],
            "baseline_evidence": context["baseline_evidence"],
            "input_manifests": context["input_manifests"],
            "experiment_envelope": context["experiment_envelope"],
            "request": action.model_dump(mode="json"),
            "visible_evidence_ids": context["visible_evidence_ids"],
            "input_schema": DiagnosticInput.model_json_schema(),
            "output_schema": DiagnosticResult.model_json_schema(),
            "protocol": "Strict cross-run unique ID matching. Decompose PL into shared delta, candidate-only, baseline-only and unmatched residual. Duplicate or absent keyspace must be INSUFFICIENT_DATA. Never infer counterfactual fills or opportunity coverage.",
            "protocol_output": {
                "inventory_per_side": [
                    "rows",
                    "missing_ids",
                    "duplicate_ids",
                    "scores",
                    "entity_kind",
                    "stable_keyspace",
                    "total_pl",
                ],
                "observed_facts": [
                    "entity_kind",
                    "common",
                    "candidate_only_identified",
                    "baseline_only_identified",
                    "unmatched_baseline",
                    "unmatched_candidate",
                    "changed_paths",
                    "shared_pl_delta",
                    "added_pl",
                    "removed_pl",
                    "unmatched_residual",
                    "total_pl_delta",
                    "reconciled",
                ],
                "rules": "PL outputs are Decimal sums rendered as strings. Counts are integers, reconciled is true. evidence_level=descriptive_decomposition. Insufficient facts={inventory:{baseline:...,candidate:...}}. Do not call unpaired rows new/lost opportunities.",
            },
            "output_contract": "implementation defines run(payload) returning DiagnosticResult JSON. test_skill defines self_test(); it may import implementation. Standard library only. No host files/network/credentials. Independent validator remains outside the worktree.",
        }
        provider = self.provider_factory(config)
        source = evidence_role(
            self.store,
            owner,
            key,
            "skill_builder",
            builder_context,
            BuilderSource,
            provider,
            config,
            "Propose a deterministic Python diagnostic implementation, a self_test and a provenance card. No command execution. Original evaluator and validation inputs are unavailable.",
            validate=validate,
        )
        assert isinstance(source, BuilderSource)
        source_ref = self.store.artifacts.put(
            {
                "implementation.py": source.implementation,
                "test_skill.py": source.self_test,
                "CARD.md": source.card,
            }
        )
        ref = self.store.artifacts.put(
            SkillManifest(
                name=action.skill_name,
                version=action.version,
                protocol=action.protocol,
                implementation="sandboxed_python",
                source_ref=source_ref,
                input_schema_ref=self.store.artifacts.put(DiagnosticInput.model_json_schema()),
                output_schema_ref=self.store.artifacts.put(DiagnosticResult.model_json_schema()),
                applicability=action.requirement,
                domain=context["domain"],
                source_refs=source.evidence_refs,
                origin="declared_fixture"
                if provider.identity().get("provider", "").endswith("fixture")
                else "agent_proposed",
            )
        )
        worktree = self.store.root / "builder-worktrees" / ref[7:27]
        skill_dir = worktree / "skills" / action.skill_name

        def materialize() -> dict[str, str]:
            if worktree.exists():
                raise AdmissionError("unknown existing builder worktree; reconcile before reuse")
            worktree.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            git(
                repository,
                "worktree",
                "add",
                "--detach",
                "--no-checkout",
                str(worktree),
                repository_commit,
            )
            skill_dir.mkdir(parents=True, mode=0o700)
            for name, content in self.store.artifacts.get(source_ref).items():
                (skill_dir / name).write_text(content)
            (skill_dir / "manifest.yaml").write_text(
                self.registry.manifest(ref).model_dump_json(indent=2)
            )
            return {"worktree": str(worktree), "skill_ref": ref, "base_commit": repository_commit}

        run_tool(
            self.store,
            owner,
            key + ":materialize",
            {"skill_ref": ref, "commit": repository_commit},
            materialize,
        )
        result = run_tool(
            self.store,
            owner,
            key + ":self-test",
            {"skill_ref": ref},
            lambda: self.registry.sandbox.run(
                skill_dir / "test_skill.py",
                {},
                self.store.root / "builder-tests" / ref[7:27],
                mode="test",
            ),
        )
        if result["status"] != "PASS":
            outcome = {
                "key": key,
                "status": "BUILD_FAILED",
                "skill_ref": ref,
                "reason": "builder self-test failed",
                "test_result_ref": self.store.artifacts.put(result),
            }
        else:
            try:
                validation_ref = self.registry.validate(owner, ref)
            except SandboxUnavailable:
                return {
                    "status": "UNAVAILABLE",
                    "skill_ref": ref,
                    "reason": "OS isolation could not execute",
                }
            proof = self.store.artifacts.get(validation_ref)
            if not proof["passed"]:
                outcome = {
                    "key": key,
                    "status": "BUILD_FAILED",
                    "skill_ref": ref,
                    "reason": "independent fidelity failed",
                    "validation_ref": validation_ref,
                }
            else:

                def commit() -> dict[str, str]:
                    git(worktree, "add", "--", "skills/" + action.skill_name)
                    git(worktree, "commit", "-m", "Candidate diagnostic skill " + ref)
                    value = git(worktree, "rev-parse", "HEAD")
                    git(repository, "update-ref", "refs/jarvis/skills/" + ref[7:], value)
                    return {"commit": value, "tree": git(worktree, "rev-parse", "HEAD^{tree}")}

                committed = run_tool(self.store, owner, key + ":commit", {"skill_ref": ref}, commit)
                self.registry.register(ref, validation_ref)
                outcome = {
                    "key": key,
                    "status": "SKILL_READY_FOR_SUCCESSOR",
                    "skill_ref": ref,
                    "validation_ref": validation_ref,
                    "git": committed,
                    "utility": "UNVERIFIED",
                }
        self.store.append(owner, "SKILL_BUILD_COMPLETED", outcome)
        # Only this owned disposable checkout is removed; source, diff and commit remain durable.
        run_tool(
            self.store,
            owner,
            key + ":cleanup",
            {"worktree": str(worktree)},
            lambda: {
                "removed": git(repository, "worktree", "remove", "--force", str(worktree)) == ""
            },
        )
        return outcome
