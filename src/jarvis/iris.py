"""Allowlisted region-only candidate construction and private Iris transport."""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Protocol

from jarvis.contracts import AdmissionError, IntegrityError
from jarvis.identity import digest, file_digest
from jarvis.iris_contracts import IrisBinding, IrisEnvironment, IrisSettings


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
        binding = next((b for b in env.bindings if b.setting == name), None)
        if binding is None:
            raise AdmissionError("REQUIRES_CAPABILITY: unbound Iris setting " + name)
        symbol = re.escape(binding.symbol)
        if binding.syntax == "macro_bool":
            pattern = rf"(#define\s+{symbol}\s+\()\s*(true|false)\s*(\))"
        else:
            pattern = rf"(\bconstexpr\s+{binding.syntax}\s+{symbol}\s*=\s*)([-+0-9.eE]+)(\s*;)"
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
                value: float | int | bool = value,
            ) -> str:
                nonlocal count
                assert binding is not None
                count += 1
                if binding.syntax == "macro_bool":
                    if type(value) is not bool:
                        raise AdmissionError("boolean binding requires a boolean")
                    encoded = str(value).lower()
                else:
                    if type(value) is bool:
                        raise AdmissionError("numeric binding does not accept bool")
                    number = float(value) * binding.scale
                    if binding.mode == "delta":
                        number += float(match[2])
                    if (binding.minimum is not None and number < binding.minimum) or (
                        binding.maximum is not None and number > binding.maximum
                    ):
                        raise AdmissionError("setting violates declared source units/bounds")
                    if binding.syntax == "int" and not number.is_integer():
                        raise AdmissionError("integer binding requires an integer")
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
    result = "".join(line for _, line in parts)
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


def setting_capabilities(env: IrisEnvironment) -> list[dict[str, Any]]:
    """Only allowlisted constants and units are visible to the direction role."""
    text = Path(env.template).read_text()
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
            matches = re.findall(
                rf"\bconstexpr\s+{binding.syntax}\s+{re.escape(binding.symbol)}\s*=\s*([-+0-9.eE]+)\s*;",
                region,
            )
            value = float(matches[0]) / binding.scale if len(matches) == 1 else None
        output.append(
            {
                "setting": binding.setting,
                "mode": binding.mode,
                "baseline_value": value,
                "source_minimum": binding.minimum,
                "source_maximum": binding.maximum,
                "source_units_per_setting_unit": binding.scale,
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
