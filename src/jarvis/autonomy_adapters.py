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
from typing import Any, Literal

from pydantic import Field

from jarvis.autonomy_contracts import ImplementTarget
from jarvis.contracts import AdmissionError, IntegrityError, Record
from jarvis.identity import digest, file_digest
from jarvis.iris import IrisTransport, LocalIrisTransport, split_regions
from jarvis.iris_contracts import IrisEnvironment
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
    # "python": run through the workbench runner (no child processes);
    # "interpreter": exec the pinned interpreter directly so the script may spawn the
    # declared toolchain (compilers), still inside the same OS sandbox.
    mode: Literal["python", "interpreter"] = "python"


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
        if self.spec.mode == "interpreter":
            argv = [str(self.workbench.interpreter), "-I", "-B", *self.spec.argv]
            result = self.workbench.execute(
                "command", argv, workdir, readonly=readonly, log_name="train"
            )
        else:
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


def region_body(text: str, name: str) -> str:
    start, end = f"// @start:{name}", f"// @end:{name}"
    if text.count(start) != 1 or text.count(end) != 1:
        raise AdmissionError(f"region {name} must appear exactly once")
    begin = text.index("\n", text.index(start)) + 1
    return text[begin : text.index(end, begin)]


def candidate_regions(candidate: ResolvedCandidate) -> dict[str, str]:
    regions: dict[str, str] = {}
    folders = ([candidate.model_dir] if candidate.model_dir else []) + list(candidate.patch_dirs)
    for folder in folders:
        for path in sorted(folder.glob("*" + REGION_SUFFIX)):
            regions[path.name[: -len(REGION_SUFFIX)]] = path.read_text()
    return regions


def candidate_constants(candidate: ResolvedCandidate) -> dict[str, float]:
    """Numeric constants a trained model needs (e.g. its decision cutoff), from constants.json."""
    path = candidate.model_dir / "constants.json" if candidate.model_dir else None
    return dict(json.loads(path.read_text())) if path and path.is_file() else {}


class RegionSetting(Record):
    """Binds one research setting to one literal in one strategy region."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    region: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    symbol: str = Field(pattern=r"^[A-Za-z_][A-Za-z_0-9]*$")
    syntax: Literal["double", "int", "macro_bool"]
    scale: float = Field(default=1.0, gt=0)


def _literal_pattern(symbol: str, syntax: str) -> str:
    if syntax == "macro_bool":
        return rf"(#define\s+{re.escape(symbol)}\s+\()\s*(true|false)\s*(\))"
    kinds = "double" if syntax == "double" else "(?:int|uint32_t)"
    return rf"(\bconstexpr\s+{kinds}\s+{re.escape(symbol)}\s*=\s*)([^;]+?)(\s*;)"


def set_literal(text: str, region: str, symbol: str, syntax: str, value: Any) -> str:
    """Replace exactly one literal inside one region; protected text never changes."""
    body = region_body(text, region)
    if syntax == "macro_bool":
        if not isinstance(value, bool):
            raise AdmissionError(f"{symbol} needs a boolean")
        encoded = "true" if value else "false"
    elif syntax == "int":
        if isinstance(value, bool) or not float(value).is_integer():
            raise AdmissionError(f"{symbol} needs an integer")
        encoded = str(int(value))
    else:
        if isinstance(value, bool):
            raise AdmissionError(f"{symbol} needs a number")
        encoded = repr(float(value))  # shortest exact round-trip
    edited, count = re.subn(
        _literal_pattern(symbol, syntax), lambda m: str(m[1]) + encoded + str(m[3]), body
    )
    if count != 1:
        raise AdmissionError(f"{symbol} must match exactly one literal in region {region}")
    return apply_regions(text, {region: edited})


class SubmissionLedger:
    """Append-only local record of remote submissions; enforces an owner daily cap."""

    def __init__(self, path: Path, daily_limit: int):
        self.path, self.daily_limit = path, daily_limit

    def today(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        day = time.strftime("%Y-%m-%d")
        rows = [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]
        return [r for r in rows if r["day"] == day]

    def reserve(self, request: dict[str, Any]) -> None:
        if len(self.today()) >= self.daily_limit:
            raise AdmissionError(
                f"QUOTA_GUARD: owner daily cap of {self.daily_limit} submissions reached"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as stream:
            stream.write(
                json.dumps({"day": time.strftime("%Y-%m-%d"), "at": time.time(), **request}) + "\n"
            )


class IrisRegionBacktester:
    """Candidate = model region files (+ constants.json) + strategy region patches + settings.

    One owner-pinned IrisEnvironment per contract period (its calendar is fixed). Each
    submission is recorded before upload; a restarted run only collects, never re-uploads.
    """

    def __init__(
        self,
        environments: dict[str, IrisEnvironment],
        *,
        settings: tuple[RegionSetting, ...],
        setting_defaults: dict[str, Any],
        ledger: SubmissionLedger,
        transport_factory: Any = LocalIrisTransport,
        poll_seconds: float = 60,
        timeout_seconds: float = 6 * 3600,
        table_limit: int = 200 * 1024 * 1024,
    ):
        if not environments or len({e.template_sha256 for e in environments.values()}) != 1:
            raise AdmissionError("all periods must share one pinned Iris template")
        self.environments = environments
        self.settings = {s.name: s for s in settings}
        self.setting_defaults = setting_defaults
        self.ledger = ledger
        self.transports: dict[str, IrisTransport] = {
            k: transport_factory(v) for k, v in environments.items()
        }
        self.poll_seconds, self.timeout_seconds = poll_seconds, timeout_seconds
        self.table_limit = table_limit

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "iris-region-backtester-v2",
            "environments": {k: digest(v) for k, v in sorted(self.environments.items())},
            "settings": digest(
                [
                    s.model_dump(mode="json")
                    for s in sorted(self.settings.values(), key=lambda s: s.name)
                ]
            ),
            "transports": {k: t.identity() for k, t in sorted(self.transports.items())},
            "daily_limit": self.ledger.daily_limit,
        }

    def render_text(self, candidate: ResolvedCandidate, settings: dict[str, Any]) -> str:
        environment = next(iter(self.environments.values()))
        template = Path(environment.template)
        if file_digest(template) != environment.template_sha256:
            raise IntegrityError("Iris template changed")
        text = apply_regions(template.read_text(), candidate_regions(candidate))
        for symbol, value in candidate_constants(candidate).items():
            text = set_literal(text, "config", symbol, "double", value)
        for name, value in settings.items():
            if self.setting_defaults.get(name) == value:
                continue  # the pinned template already holds the baseline value
            if name not in self.settings:
                raise AdmissionError("REQUIRES_CAPABILITY: no region binding for " + name)
            b = self.settings[name]
            scaled = value if b.syntax == "macro_bool" else float(value) * b.scale
            text = set_literal(text, b.region, b.symbol, b.syntax, scaled)
        return text

    def render(
        self, candidate: ResolvedCandidate, settings: dict[str, Any], workdir: Path, period: str
    ) -> Path:
        workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        output = workdir / f"{self.environments[period].branch}.txt"
        output.write_text(self.render_text(candidate, settings))
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
            self.ledger.reserve({**submission, "period_name": period})
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
        raw = value.get("provenance", {}).get("raw_sha256")
        raw_path = next(
            (
                p
                for p in sorted((workdir / "transport").rglob("*.csv"))
                if raw and file_digest(p) == raw
            ),
            None,
        )
        tables = []
        if raw_path is not None:
            manifest = workdir / "raw_source.json"
            manifest.write_text(
                json.dumps(
                    {
                        "kind": "IrisRawSource",
                        "raw_csv": str(raw_path),
                        "raw_csv_sha256": raw,
                        "commit": value.get("commit"),
                        "period": submission["period"],
                        "trading_dates": [
                            str(d) for d in self.environments[period].period.trading_dates
                        ],
                        "settings": settings,
                        "candidate_sha256": submission["candidate_sha256"],
                        "request_id": submission["request_id"],
                    },
                    indent=1,
                )
            )
            tables.append(
                {
                    "name": "raw_source",
                    "path": str(manifest),
                    "description": "Pointer to the full raw Iris output (entries + pre-fill snapshots); use as training data",
                }
            )
            if raw_path.stat().st_size <= self.table_limit:
                tables.append(
                    {
                        "name": "trades",
                        "path": str(raw_path),
                        "description": "Raw Iris rows: dynamicexit=0 filled entries, dynamicexit<0 pre-fill snapshots; features column holds 91 values",
                    }
                )
        risk = value.get("risk", {})
        return {
            "status": "SUCCEEDED",
            "metrics": {
                "pl": value["entry_only_pl"],
                "mean_pr": float(risk["mean_pr"]) if risk.get("mean_pr") is not None else None,
                "count": value["count"],
                "mdd": abs(float(value["mdd"])),
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
        harness: Any = None,
        max_bytes: int = 1_000_000,
    ):
        """`harness(candidate_text) -> {filename: text}` (trusted owner code) prepares files,
        e.g. a C++ translation unit with engine stubs, that `check` then compiles."""
        self.template, self.template_sha256, self.check, self.max_bytes = (
            template,
            template_sha256,
            check,
            max_bytes,
        )
        self.harness = harness

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "region-patch-validator",
            "template": self.template_sha256,
            "check": digest(self.check) if self.check else None,
            "harness": getattr(self.harness, "identity", lambda: None)(),
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
            if self.harness is not None:
                for name, content in self.harness(text).items():
                    (stage / name).write_text(content)
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
                "check": {**run, "stderr": run["stderr"][-6000:]},
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
        IrisRegionBacktester(
            environments,
            settings=tuple(
                RegionSetting.model_validate(x) for x in args.get("region_settings", [])
            ),
            setting_defaults=defaults,
            ledger=SubmissionLedger(
                Path(args["submission_ledger"]), int(args.get("daily_submission_limit", 4))
            ),
        )
        if environments
        else None
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
        harness = None
        if value.get("harness"):
            import importlib

            module, _, function = str(value["harness"]).partition(":")
            harness = getattr(importlib.import_module(module), function)
        validators[name] = RegionPatchValidator(
            Path(value["template"]), value["template_sha256"], check=check, harness=harness
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
