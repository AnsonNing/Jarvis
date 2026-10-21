"""Owner-declared executors for real domains (Hermes training, Iris backtests).

No private path, credential or strategy logic lives here: the owner's private
declaration supplies commands, roots and hashes. Agent-influenced code (pipeline
patches) and owner pipelines run behind the Workbench; remote submissions record a
durable request before upload and are only collected, never resubmitted, after a crash.
"""

import json
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from jarvis.autonomy_contracts import ImplementTarget
from jarvis.contracts import AdmissionError, IntegrityError, Record
from jarvis.identity import digest, file_digest
from jarvis.iris import IrisTransport, LocalIrisTransport, render_candidate, split_regions
from jarvis.iris_contracts import IrisEnvironment, IrisSettings
from jarvis.research_executors import ResolvedCandidate
from jarvis.workbench import Workbench

REGION_SUFFIX = ".region"


class CommandSpec(Record):
    """A trusted owner command run inside the Workbench with a JSON request file."""

    argv: tuple[str, ...] = Field(min_length=1, max_length=32)
    readonly: tuple[str, ...] = Field(default=(), max_length=16)
    source_hashes: dict[str, str] = Field(default_factory=dict)
    space: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(min_length=1, max_length=1000)


def _check_sources(spec: CommandSpec) -> None:
    for path, expected in spec.source_hashes.items():
        if file_digest(Path(path)) != expected:
            raise IntegrityError("declared executor source changed: " + Path(path).name)


class CommandTrainer:
    """Runs the owner's training entry point; it must write outputs/ and result.json.

    request.json: {config, base_model_dir, training_data, pipeline_patch_dir}
    result.json:  {status, model_id, files: [relative paths under outputs/], validation, data_policy}
    """

    def __init__(self, spec: CommandSpec, workbench: Workbench):
        self.spec, self.workbench = spec, workbench

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "owner-command-trainer",
            "spec": digest(self.spec),
            "workbench": self.workbench.identity(),
        }

    def validate_config(self, space: dict[str, Any] | None, config: dict[str, Any]) -> None:
        space = space or self.spec.space
        for name, value in config.items():
            if name not in space:
                raise ValueError(f"undeclared training option {name}")
            rule = space[name]
            if rule.get("kind") == "choice":
                if value not in rule["choices"]:
                    raise ValueError(f"training option {name} outside choices")
            elif rule.get("kind") == "bool":
                if not isinstance(value, bool):
                    raise ValueError(f"training option {name} must be boolean")
            elif (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not (rule["minimum"] <= value <= rule["maximum"])
            ):
                raise ValueError(f"training option {name} outside declared range")

    def run(
        self,
        base: ResolvedCandidate,
        config: dict[str, Any],
        training_data: Path | None,
        pipeline_patch: Path | None,
        workdir: Path,
    ) -> dict[str, Any]:
        _check_sources(self.spec)
        workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (workdir / "outputs").mkdir(exist_ok=True, mode=0o700)
        (workdir / "request.json").write_text(
            json.dumps(
                {
                    "config": config,
                    "base_model_dir": str(base.model_dir) if base.model_dir else None,
                    "training_data": str(training_data) if training_data else None,
                    "pipeline_patch_dir": str(pipeline_patch) if pipeline_patch else None,
                }
            )
        )
        readonly = tuple(Path(p) for p in self.spec.readonly) + tuple(
            p for p in (base.root, training_data, pipeline_patch) if p is not None
        )
        result = self.workbench.execute(
            "python", list(self.spec.argv), workdir, readonly=readonly, log_name="train"
        )
        if result["status"] != "PASS" or not (workdir / "result.json").is_file():
            return {"status": "FAILED", "error": (result["stderr"] or result["stdout"])[-3000:]}
        value = json.loads((workdir / "result.json").read_text())
        files = []
        for name in value.get("files", []):
            path = (workdir / "outputs" / name).resolve()
            if not path.is_relative_to((workdir / "outputs").resolve()) or not path.is_file():
                raise IntegrityError("trainer output escaped outputs/ or is missing")
            files.append({"name": name, "path": str(path)})
        return {**value, "files": files}


def apply_regions(text: str, replacements: dict[str, str]) -> str:
    """Replace whole region bodies; markers and protected text must stay identical."""
    parts = split_regions(text)
    out, current, seen = [], None, set()
    for writable, line in parts:
        start = re.fullmatch(r"// @start:([A-Za-z0-9_]+)\s*", line.strip())
        if start:
            current = start[1]
            out.append(line)
            if current in replacements:
                body = replacements[current]
                out.append(body if body.endswith("\n") else body + "\n")
                seen.add(current)
            continue
        if line.strip().startswith("// @end:"):
            current = None
            out.append(line)
            continue
        if writable and current in replacements:
            continue
        out.append(line)
    if seen != set(replacements):
        raise AdmissionError(
            "unknown region in replacement: " + ", ".join(sorted(set(replacements) - seen))
        )
    result = "".join(out)
    if [x for w, x in split_regions(text) if not w] != [
        x for w, x in split_regions(result) if not w
    ]:
        raise IntegrityError("region replacement changed protected text")
    return result


def candidate_regions(candidate: ResolvedCandidate) -> dict[str, str]:
    regions: dict[str, str] = {}
    folders = ([candidate.model_dir] if candidate.model_dir else []) + list(candidate.patch_dirs)
    for folder in folders:
        for path in sorted(folder.glob("*" + REGION_SUFFIX)):
            regions[path.name[: -len(REGION_SUFFIX)]] = path.read_text()
    return regions


class IrisRegionBacktester:
    """Candidate = model region files + strategy region patches + bound settings.

    One owner-pinned IrisEnvironment per contract period name (its calendar is fixed).
    """

    def __init__(
        self,
        environments: dict[str, IrisEnvironment],
        *,
        setting_defaults: dict[str, Any],
        transport_factory: Any = LocalIrisTransport,
        poll_seconds: float = 60,
        timeout_seconds: float = 6 * 3600,
    ):
        if not environments or len({e.template_sha256 for e in environments.values()}) != 1:
            raise AdmissionError("all periods must share one pinned Iris template")
        self.environments = environments
        self.setting_defaults = setting_defaults
        self.transports: dict[str, IrisTransport] = {
            k: transport_factory(v) for k, v in environments.items()
        }
        self.poll_seconds, self.timeout_seconds = poll_seconds, timeout_seconds

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "iris-region-backtester",
            "environments": {k: digest(v) for k, v in sorted(self.environments.items())},
            "transports": {k: t.identity() for k, t in sorted(self.transports.items())},
        }

    def render(
        self, candidate: ResolvedCandidate, settings: dict[str, Any], workdir: Path, period: str
    ) -> Path:
        environment = self.environments[period]
        template = Path(environment.template)
        if file_digest(template) != environment.template_sha256:
            raise IntegrityError("Iris template changed")
        workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        staged = workdir / "template.txt"
        staged.write_text(apply_regions(template.read_text(), candidate_regions(candidate)))
        env = environment.model_copy(
            update={"template": str(staged), "template_sha256": file_digest(staged)}
        )
        changes = {k: v for k, v in settings.items() if self.setting_defaults.get(k) != v}
        unknown = set(changes) - set(IrisSettings.model_fields)
        if unknown:
            raise AdmissionError(
                "REQUIRES_CAPABILITY: no Iris binding for " + ", ".join(sorted(unknown))
            )
        output = workdir / f"{environment.branch}.txt"
        output.write_text(render_candidate(env, IrisSettings(**changes)))
        return output

    def run(
        self, candidate: ResolvedCandidate, settings: dict[str, Any], period: str, workdir: Path
    ) -> dict[str, Any]:
        if period not in self.environments:
            raise AdmissionError("no pinned Iris environment for period " + period)
        transport = self.transports[period]
        workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = workdir / "submission.json"
        if record.exists():
            submission = json.loads(record.read_text())  # collect only; never upload twice
        else:
            path = self.render(candidate, settings, workdir, period)
            submission = {
                "request_id": secrets.token_hex(4),
                "candidate_path": str(path),
                "candidate_sha256": file_digest(path),
                "period": self.environments[period].period.request_period,
            }
            temp = record.with_suffix(".tmp")
            temp.write_text(json.dumps(submission))
            temp.replace(record)
            ack = transport.submit(submission, workdir / "transport")
            if ack.get("status") not in {"SUBMITTED", "UNKNOWN"}:
                return {"status": "FAILED", "error": "submission refused: " + json.dumps(ack)[:500]}
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            value = transport.collect(submission, workdir / "transport")
            if value.get("status") not in {"PENDING", "UNKNOWN"}:
                break
            if time.monotonic() > deadline:
                return {
                    "status": "FAILED",
                    "error": "PENDING_TIMEOUT: collect again by request id",
                    "request_id": submission["request_id"],
                }
            time.sleep(self.poll_seconds)
        if value.get("status") != "SUCCEEDED":
            return {
                "status": "FAILED",
                "error": json.dumps(value)[:2000],
                "request_id": submission["request_id"],
            }
        tables = []
        raw = value.get("provenance", {}).get("raw_sha256")
        for csv_path in sorted((workdir / "transport").rglob("*.csv")):
            if raw and file_digest(csv_path) == raw:
                tables.append(
                    {
                        "name": "trades",
                        "path": str(csv_path),
                        "description": "Iris raw trades for this candidate",
                    }
                )
        risk = value.get("risk", {})
        return {
            "status": "SUCCEEDED",
            "metrics": {
                "pl": value["entry_only_pl"],
                "mean_pr": risk.get("mean_pr"),
                "count": value["count"],
                "mdd": value["mdd"],
                "worst_day": value["worst_day"],
                "win_rate": risk.get("entry_win_rate"),
            },
            "daily": value.get("daily_pl", {}),
            "tables": tables,
            "result_origin": value.get("result_origin", "OBSERVED_IRIS_BACKTEST"),
            "provenance": {
                **value.get("provenance", {}),
                "request_id": submission["request_id"],
                "commit": value.get("commit"),
            },
        }


class RegionPatchValidator:
    """Strategy-region edits: markers intact, only declared regions change, bounded size,
    and optional compile/smoke command (owner-declared) run inside the Workbench."""

    def __init__(
        self,
        template: Path,
        template_sha256: str,
        *,
        check: CommandSpec | None = None,
        max_bytes: int = 1_000_000,
    ):
        self.template, self.template_sha256, self.check, self.max_bytes = (
            template,
            template_sha256,
            check,
            max_bytes,
        )

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "region-patch-validator",
            "template": self.template_sha256,
            "check": digest(self.check) if self.check else None,
        }

    def base_files(self, target: ImplementTarget, base: ResolvedCandidate) -> dict[str, bytes]:
        if file_digest(self.template) != self.template_sha256:
            raise IntegrityError("template changed")
        regions = candidate_regions(base)
        text = (
            apply_regions(self.template.read_text(), regions)
            if regions
            else self.template.read_text()
        )
        files = {}
        for name in target.files:
            region = name.removesuffix(REGION_SUFFIX)
            body = text.split("// @start:" + region, 1)[1].split("// @end:" + region, 1)[0]
            files[name] = body.split("\n", 1)[1].encode() if "\n" in body else b""
        return files

    def validate(
        self, target: ImplementTarget, base: ResolvedCandidate, work: Path, workbench: Workbench
    ) -> dict[str, Any]:
        replacements = {}
        for name in target.files:
            data = (work / name).read_text()
            if "// @start:" in data or "// @end:" in data:
                return {
                    "status": "FAIL",
                    "error": "region markers must not appear inside a region body",
                }
            replacements[name.removesuffix(REGION_SUFFIX)] = data
        try:
            text = apply_regions(
                self.template.read_text(), {**candidate_regions(base), **replacements}
            )
        except (AdmissionError, IntegrityError) as exc:
            return {"status": "FAIL", "error": str(exc)}
        if len(text.encode()) > self.max_bytes:
            return {"status": "FAIL", "error": "candidate exceeds the submission size limit"}
        result: dict[str, Any] = {"status": "PASS", "checks": ["markers", "protected text", "size"]}
        if self.check:
            _check_sources(self.check)
            stage = work.parent / "region-check"
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(mode=0o700)
            (stage / "candidate.txt").write_text(text)
            run = workbench.execute(
                "command" if not self.check.argv[0].endswith(".py") else "python",
                list(self.check.argv),
                stage,
                readonly=tuple(Path(p) for p in self.check.readonly),
                log_name="region-check",
            )
            result = {
                **result,
                "status": run["status"],
                "check": run,
                "checks": [*result["checks"], self.check.description],
            }
        return result


class CommandPatchValidator:
    """Pipeline/source edits checked by an owner command (tests, leakage checks) in the Workbench."""

    def __init__(self, base_paths: dict[str, str], check: CommandSpec):
        self.base_paths, self.check = base_paths, check

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "command-patch-validator",
            "base": {k: file_digest(Path(v)) for k, v in sorted(self.base_paths.items())},
            "check": digest(self.check),
        }

    def base_files(self, target: ImplementTarget, base: ResolvedCandidate) -> dict[str, bytes]:
        for patch, folder in zip(base.patches, base.patch_dirs, strict=True):
            if patch["target"] == target.name:
                return {name: (folder / name).read_bytes() for name in target.files}
        return {name: Path(self.base_paths[name]).read_bytes() for name in target.files}

    def validate(
        self, target: ImplementTarget, base: ResolvedCandidate, work: Path, workbench: Workbench
    ) -> dict[str, Any]:
        _check_sources(self.check)
        stage = work.parent / "patch-check"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(mode=0o700)
        for name in target.files:
            target_path = stage / name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / name, target_path)
        run = workbench.execute(
            "python" if self.check.argv[0].endswith(".py") else "command",
            list(self.check.argv),
            stage,
            readonly=tuple(Path(p) for p in self.check.readonly),
            log_name="patch-check",
        )
        return {"status": run["status"], "check": run, "checks": [self.check.description]}


def declared_domain(store: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Declaration adapter for real domains. All paths come from the owner's private file.

    args: contract (JSON path), baseline {label, settings, model_id, model_files, data_policy},
    iris_environments {period: IrisEnvironment JSON path}, trainer (CommandSpec JSON path),
    region_validators {target: {template, template_sha256, check?}},
    command_validators {target: {base_paths, check}}.
    """
    from jarvis.autonomy_contracts import AutonomyContract, Candidate, ModelArtifact, SourceScope
    from jarvis.research_executors import Executors

    raw = json.loads(Path(args["contract"]).read_text())
    raw["source_scopes"] = [
        {**s, "sha256": s.get("sha256") or file_digest(Path(s["path"]))}
        for s in raw.get("source_scopes", [])
    ]
    for scope in raw["source_scopes"]:
        SourceScope.model_validate(scope)
    baseline = args["baseline"]
    model = ModelArtifact(
        model_id=baseline["model_id"],
        files={
            name: store.blobs.put_file(Path(path)) for name, path in baseline["model_files"].items()
        },
        training_config={},
        training_data_ref=None,
        data_policy=baseline["data_policy"],
        validation=baseline.get("validation", {}),
        origin="owner_provided",
        trainer={"kind": "owner baseline"},
    )
    candidate = Candidate(
        label=baseline.get("label", "baseline"),
        parent_ref=None,
        model_ref=store.artifacts.put(model),
        settings=baseline.get("settings", {}),
        origin="owner_baseline",
        data_policy=baseline["data_policy"],
    )
    contract = AutonomyContract.model_validate(
        {**raw, "baseline_candidate_ref": store.artifacts.put(candidate)}
    )
    workbench = Workbench(contract.analysis_environment)
    environments = {
        period: IrisEnvironment.model_validate_json(Path(path).read_text())
        for period, path in args.get("iris_environments", {}).items()
    }
    defaults = {s.name: s.baseline for s in contract.settings}
    backtester = (
        IrisRegionBacktester(environments, setting_defaults=defaults) if environments else None
    )
    trainer = (
        CommandTrainer(
            CommandSpec.model_validate_json(Path(args["trainer"]).read_text()), workbench
        )
        if args.get("trainer")
        else None
    )
    validators: dict[str, Any] = {}
    for name, value in args.get("region_validators", {}).items():
        check = CommandSpec.model_validate(value["check"]) if value.get("check") else None
        validators[name] = RegionPatchValidator(
            Path(value["template"]), value["template_sha256"], check=check
        )
    for name, value in args.get("command_validators", {}).items():
        validators[name] = CommandPatchValidator(
            value["base_paths"], CommandSpec.model_validate(value["check"])
        )
    from jarvis.autonomous_research import table_record

    inputs = tuple(
        table_record(
            store,
            Path(item["path"]),
            name=item["name"],
            description=item["description"],
            lineage={
                "origin": "owner_provided",
                "candidate_ref": contract.baseline_candidate_ref,
                **item.get("lineage", {}),
            },
            exposure=contract.exposure,
        )
        for item in args.get("inputs", [])
    )
    return {
        "contract_ref": store.artifacts.put(contract),
        "executors": Executors(
            workbench=workbench, backtester=backtester, trainer=trainer, validators=validators
        ),
        "input_refs": inputs,
    }
