"""Dependency-injected executors for autonomous research actions.

Domain adapters implement these protocols. Every executor receives materialized local
files, never Store handles, and returns plain JSON-compatible dictionaries; the trusted
session records hashes, lineage and cost.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from jarvis.autonomy_contracts import Candidate, ImplementTarget, ModelArtifact
from jarvis.contracts import IntegrityError
from jarvis.storage import Store
from jarvis.workbench import Workbench


@dataclass(frozen=True)
class ResolvedCandidate:
    ref: str
    candidate: Candidate
    root: Path
    model_dir: Path | None
    model: ModelArtifact | None
    patch_dirs: tuple[Path, ...]
    patches: tuple[dict[str, Any], ...]
    settings: dict[str, Any]


class Backtester(Protocol):
    def identity(self) -> dict[str, Any]: ...

    def run(
        self, candidate: ResolvedCandidate, settings: dict[str, Any], period: str, workdir: Path
    ) -> dict[str, Any]:
        """Return status, metrics, daily, tables=[{name, path, description}], provenance."""
        ...


class Trainer(Protocol):
    def identity(self) -> dict[str, Any]: ...

    def validate_config(self, space: dict[str, Any] | None, config: dict[str, Any]) -> None: ...

    def run(
        self,
        base: ResolvedCandidate,
        config: dict[str, Any],
        training_data: Path | None,
        pipeline_patch: Path | None,
        workdir: Path,
    ) -> dict[str, Any]:
        """Return status, model_id, files=[{name, path}], validation, data_policy."""
        ...


class PatchValidator(Protocol):
    def identity(self) -> dict[str, Any]: ...

    def base_files(self, target: ImplementTarget, base: ResolvedCandidate) -> dict[str, bytes]: ...

    def validate(
        self, target: ImplementTarget, base: ResolvedCandidate, work: Path, workbench: Workbench
    ) -> dict[str, Any]:
        """Run trusted checks on the edited files in `work`; status PASS or FAIL."""
        ...


@dataclass
class Executors:
    workbench: Workbench
    backtester: Backtester | None = None
    trainer: Trainer | None = None
    validators: dict[str, PatchValidator] = field(default_factory=dict)

    def identity(self) -> dict[str, Any]:
        return {
            "workbench": self.workbench.identity(),
            "backtester": self.backtester.identity() if self.backtester else None,
            "trainer": self.trainer.identity() if self.trainer else None,
            "validators": {k: v.identity() for k, v in sorted(self.validators.items())},
        }


def materialize_files(store: Store, files: dict[str, str], target: Path) -> Path:
    for name, ref in files.items():
        path = (target / name).resolve()
        if not path.is_relative_to(target.resolve()):
            raise IntegrityError("artifact file escapes its directory")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            path.write_bytes(store.blobs.get(ref))
    return target


def resolve_candidate(store: Store, ref: str, root: Path) -> ResolvedCandidate:
    candidate = Candidate.model_validate(store.artifacts.get(ref))
    base = root / ref[7:23]
    model = (
        ModelArtifact.model_validate(store.artifacts.get(candidate.model_ref))
        if candidate.model_ref
        else None
    )
    model_dir = materialize_files(store, model.files, base / "model") if model else None
    patches, dirs = [], []
    for index, patch_ref in enumerate(candidate.patch_refs):
        patch = store.artifacts.get(patch_ref)
        patches.append(patch)
        dirs.append(materialize_files(store, patch["files"], base / f"patch-{index}"))
    return ResolvedCandidate(
        ref=ref,
        candidate=candidate,
        root=base,
        model_dir=model_dir,
        model=model,
        patch_dirs=tuple(dirs),
        patches=tuple(patches),
        settings=dict(candidate.settings),
    )
