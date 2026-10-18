"""Allowlisted region-only candidate construction and private Iris transport."""

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Protocol

from jarvis.contracts import AdmissionError, IntegrityError
from jarvis.identity import digest, file_digest
from jarvis.iris_contracts import IrisBinding, IrisEnvironment, IrisSettings
from jarvis.iris_design import read_profile, render_rules


def clock_to_ms(value: int) -> int:
    hours, minutes, seconds, millis = (
        value // 10000000,
        value // 100000 % 100,
        value // 1000 % 100,
        value % 1000,
    )
    if hours > 23 or minutes > 59 or seconds > 59:
        raise AdmissionError("invalid source HHMMSSmmm clock")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def ms_to_clock(value: int) -> int:
    return (
        value // 3600000 * 10000000
        + value // 60000 % 60 * 100000
        + value // 1000 % 60 * 1000
        + value % 1000
    )


def scalar_pattern(binding: IrisBinding) -> str:
    symbol = re.escape(binding.symbol)
    if binding.syntax == "macro_bool":
        return rf"(#define\s+{symbol}\s+\()\s*(true|false)\s*(\))"
    kind = "uint32_t" if binding.syntax == "clock" else binding.syntax
    number = r"[-+]?(?:[0-9][0-9']*(?:\.[0-9']*)?|\.[0-9']+)(?:[eE][-+]?[0-9]+)?"
    # Closed literal or two-literal product, never a general C++ expression.
    value = rf"{number}(?:[ \t]*\*[ \t]*{number})?"
    return rf"(\bconstexpr\s+{kind}\s+{symbol}\s*=\s*)({value})(\s*;)"


def scalar_number(value: str) -> float:
    parts = value.split("*")
    if len(parts) > 2:
        raise AdmissionError("unsupported scalar source expression")
    number = math.prod(float(part.replace("'", "")) for part in parts)
    if not math.isfinite(number):
        raise AdmissionError("nonfinite source constant")
    return number


def split_regions(text: str) -> list[tuple[bool, str]]:
    lines = text.splitlines(keepends=True)
    output: list[tuple[bool, str]] = []
    opened: str | None = None
    names: set[str] = set()
    for line in lines:
        marker = re.fullmatch(r"// @(start|end):([A-Za-z0-9_]+)\s*", line.strip())
        if marker:
            edge, name = marker.groups()
            if edge == "start":
                if opened or name in names:
                    raise AdmissionError("nested or duplicate Iris region")
                names.add(name)
                opened = name
            else:
                if opened != name:
                    raise AdmissionError("unmatched Iris region")
                opened = None
            output.append((False, line))
        else:
            output.append((opened is not None, line))
    if opened or not names:
        raise AdmissionError("missing or unclosed Iris region")
    return output


def render_candidate(env: IrisEnvironment, settings: IrisSettings) -> str:
    path = Path(env.template)
    if file_digest(path) != env.template_sha256:
        raise IntegrityError("Iris template changed")
    original = path.read_text()
    parts = split_regions(original)
    for name, value in settings.changes().items():
        if name in {"entry_rule", "cancel_rule"}:
            continue
        if type(value) not in {int, float, bool}:
            raise AdmissionError("scalar setting requires a number or boolean")
        binding = next((b for b in env.bindings if b.setting == name), None)
        if binding is None:
            raise AdmissionError("REQUIRES_CAPABILITY: unbound Iris setting " + name)
        pattern = scalar_pattern(binding)
        count = 0
        edited = []
        current_region = None
        for writable, line in parts:
            start = re.fullmatch(r"// @start:([A-Za-z0-9_]+)\s*", line.strip())
            if start:
                current_region = start[1]
            if line.strip().startswith("// @end:"):
                current_region = None

            def replace(
                match: re.Match[str],
                binding: IrisBinding | None = binding,
                value: object = value,
            ) -> str:
                nonlocal count
                assert binding is not None
                count += 1
                if binding.syntax == "macro_bool":
                    if type(value) is not bool:
                        raise AdmissionError("boolean binding requires a boolean")
                    encoded = str(value).lower()
                else:
                    if type(value) not in {int, float} or not isinstance(value, (int, float)):
                        raise AdmissionError("numeric binding does not accept bool")
                    number = float(value) * binding.scale
                    if binding.mode == "delta":
                        number += scalar_number(match[2])
                    if (binding.minimum is not None and number < binding.minimum) or (
                        binding.maximum is not None and number > binding.maximum
                    ):
                        raise AdmissionError("setting violates declared source units/bounds")
                    if binding.syntax == "int" and not number.is_integer():
                        raise AdmissionError("integer binding requires an integer")
                    if binding.syntax == "clock":
                        encoded = str(ms_to_clock(int(number)))
                    else:
                        encoded = (
                            str(int(number)) if binding.syntax == "int" else format(number, ".17g")
                        )
                return match[1] + encoded + match[3]

            edited.append(
                (
                    writable,
                    re.sub(pattern, replace, line)
                    if writable and current_region == binding.region
                    else line,
                )
            )
        if count != 1:
            raise AdmissionError("setting must match exactly one writable constant")
        parts = edited
    result = render_rules(env, "".join(line for _, line in parts), settings)
    if any(k in settings.changes() for k in ["session_start_ms", "session_end_ms", "flat_time_ms"]):
        clocks = {c["setting"]: c["baseline_value"] for c in setting_capabilities(env, text=result)}
        values = [clocks.get(k) for k in ["session_start_ms", "session_end_ms", "flat_time_ms"]]
        if any(v is None for v in values):
            raise AdmissionError("session clock bindings are incomplete")
        session_begin, session_end, flat = (float(v) for v in values if v is not None)
        if not session_begin < session_end <= flat:
            raise AdmissionError("session start < session end <= flat time must hold")
    if [line for writable, line in split_regions(original) if not writable] != [
        line for writable, line in split_regions(result) if not writable
    ]:
        raise IntegrityError("candidate changed protected region boundaries")
    return result


def verify_environment(env: IrisEnvironment) -> None:
    root = Path(env.root)
    expected = {
        str(root / "iris_agent.py"): env.agent_script_sha256,
        env.evaluator: env.evaluator_sha256,
        env.template: env.template_sha256,
        **env.accepted_inputs,
    }
    for name, expected_hash in expected.items():
        if file_digest(Path(name)) != expected_hash:
            raise IntegrityError("Iris protected input changed")
        if Path(name).suffix == ".json":
            try:
                record = json.loads(Path(name).read_bytes())
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("kind") == "IrisDevelopmentUse":
                if env.period.exposure != "development_exposed":
                    raise IntegrityError(
                        "owner development use cannot supply independent test evidence"
                    )
    read_profile(env)


def setting_capabilities(env: IrisEnvironment, *, text: str | None = None) -> list[dict[str, Any]]:
    """Only allowlisted constants and units are visible to the direction role."""
    text = text if text is not None else Path(env.template).read_text()
    if file_digest(Path(env.template)) != env.template_sha256:
        raise IntegrityError("setting source changed")
    output = []
    for binding in env.bindings:
        region = text.split("// @start:" + binding.region, 1)[1].split(
            "// @end:" + binding.region, 1
        )[0]
        if binding.syntax == "macro_bool":
            matches = re.findall(
                rf"#define\s+{re.escape(binding.symbol)}\s+\(\s*(true|false)\s*\)", region
            )
            value: Any = matches[0] == "true" if len(matches) == 1 else None
        else:
            matches = list(re.finditer(scalar_pattern(binding), region))
            value = scalar_number(matches[0][2]) / binding.scale if len(matches) == 1 else None
            if binding.syntax == "clock" and value is not None:
                value = clock_to_ms(int(value))
        output.append(
            {
                "setting": binding.setting,
                "mode": binding.mode,
                "baseline_value": value,
                "source_minimum": binding.minimum,
                "source_maximum": binding.maximum,
                "source_units_per_setting_unit": binding.scale,
                "units": "ms_since_midnight" if binding.syntax == "clock" else binding.units,
            }
        )
    return output


class IrisTransport(Protocol):
    def identity(self) -> dict[str, Any]: ...
    def submit(self, request: dict[str, Any], directory: Path) -> dict[str, Any]: ...
    def collect(self, request: dict[str, Any], directory: Path) -> dict[str, Any]: ...
    def import_baseline(self, request: dict[str, Any], directory: Path) -> dict[str, Any]: ...
    def quota(self, request_ids: list[str], directory: Path) -> dict[str, Any]: ...


class LocalIrisTransport:
    """Worker imports the supplied API; credentials never enter the parent/prompt."""

    def __init__(self, environment: IrisEnvironment):
        self.environment = environment

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "private-iris-worker",
            "worker": file_digest(Path(__file__).with_name("iris_worker.py")),
            "environment": digest(self.environment),
        }

    def invoke(self, operation: str, request: dict[str, Any], directory: Path) -> dict[str, Any]:
        verify_environment(self.environment)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        inp = directory / "request.json"
        data = {"environment": self.environment.model_dump(mode="json"), "request": request}
        if inp.exists() and json.loads(inp.read_text()) != data:
            raise IntegrityError("remote worker request changed")
        inp.write_text(json.dumps(data))
        out = directory / (operation + ".json")
        # Submit ack is immutable; collection snapshots may advance for this same ID.
        if operation == "submit" and out.exists():
            return dict(json.loads(out.read_text()))
        command = [
            self.environment.python,
            "-B",
            str(Path(__file__).with_name("iris_worker.py")),
            operation,
            str(inp),
            str(out),
        ]
        clean = {
            k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}
        }
        clean.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
        try:
            result = subprocess.run(
                command,
                cwd=self.environment.root,
                env=clean,
                capture_output=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "UNKNOWN",
                "request_id": request["request_id"],
                "error": "transport timeout; collect only",
            }
        if result.returncode or not out.exists():
            return {
                "status": "UNKNOWN",
                "request_id": request["request_id"],
                "error": "transport incomplete; collect only",
            }
        value = dict(json.loads(out.read_text()))
        verify_environment(self.environment)
        return value

    def submit(self, request: dict[str, Any], directory: Path) -> dict[str, Any]:
        return self.invoke("submit", request, directory)

    def collect(self, request: dict[str, Any], directory: Path) -> dict[str, Any]:
        return self.invoke("collect", request, directory)

    def import_baseline(self, request: dict[str, Any], directory: Path) -> dict[str, Any]:
        return self.invoke("import", request, directory)

    def quota(self, request_ids: list[str], directory: Path) -> dict[str, Any]:
        return self.invoke(
            "quota", {"request_id": "00000000", "known_request_ids": request_ids}, directory
        )
