"""Real Codex CLI structured completion with captured usage and no domain tools."""

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol

from jarvis.contracts import AdmissionError, Cost, JarvisError
from jarvis.identity import file_digest
from jarvis.researcher import AgentConfig


class ResearchProvider(Protocol):
    def identity(self) -> dict[str, Any]: ...
    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]: ...


class CodexProvider:
    def __init__(self, config: AgentConfig):
        self.config = config
        located = shutil.which(config.executable)
        if not located:
            raise JarvisError("Codex executable unavailable; configure its local path")
        self.executable = Path(located).absolute()

    def identity(self) -> dict[str, Any]:
        result = subprocess.run(
            [str(self.executable), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return {
            "provider": "codex-cli",
            "version": result.stdout.strip(),
            "executable_sha256": file_digest(self.executable),
            "model": self.config.model,
            "model_resolution": "explicit"
            if self.config.model
            else "CLI default; resolved model not reported by exec JSON",
            "tools": "disabled",
            "config": "ignore-user-config",
            "sandbox": "read-only",
        }

    def command(self, directory: Path) -> list[str]:
        cmd = [
            str(self.executable),
            "--no-daemon",
            "-a",
            "never",
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            "--color",
            "never",
        ]
        for option in [
            "features.shell_tool=false",
            "features.plugins=false",
            "features.apps=false",
            "features.memories=false",
            "features.multi_agent=false",
            "features.hooks=false",
            "features.image_generation=false",
            "features.remote_plugin=false",
            'web_search="disabled"',
            "features.unbounded_connection_retries=false",
        ]:
            cmd += ["-c", option]
        if self.config.model:
            cmd += ["--model", self.config.model]
        return cmd + [
            "--output-schema",
            str(directory / "schema.json"),
            "-o",
            str(directory / "answer.json"),
            "-",
        ]

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        directory = directory.resolve()
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        (directory / "prompt.txt").write_text(prompt)
        (directory / "schema.json").write_text(json.dumps(schema))
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "CODEX_HOME"}
        }
        started = time.monotonic()
        timed_out = False
        with (
            (directory / "events.jsonl").open("w") as stdout,
            (directory / "stderr.txt").open("w") as stderr,
        ):
            process = subprocess.Popen(
                self.command(directory),
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                text=True,
                cwd=directory,
                env=clean_env,
                start_new_session=True,
            )
            try:
                process.communicate(prompt, timeout=self.config.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        events = []
        parse_error = False
        for line in (directory / "events.jsonl").read_text().splitlines():
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    parse_error = True
                    continue
                events.append(event)
            except ValueError:
                parse_error = True
        terminal = [e for e in events if e.get("type") == "turn.completed"]
        forbidden = [
            e
            for e in events
            if e.get("type", "").startswith("item.")
            and e.get("item", {}).get("type") not in {"agent_message", "reasoning"}
        ]
        error = None
        usage: dict[str, int] | None = None
        if terminal and all(isinstance(e.get("usage"), dict) for e in terminal):
            totals = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
            for event in terminal:
                raw = event["usage"]
                if not {"input_tokens", "output_tokens"} <= set(raw):
                    totals = {}
                    break
                for key in totals:
                    count = raw.get(key, 0)
                    if type(count) is not int or count < 0:
                        raise AdmissionError("invalid provider token usage")
                    totals[key] += count
            usage = totals or None
        if timed_out or process.returncode or not terminal or parse_error:
            error = "PROVIDER_TIMEOUT" if timed_out else "PROVIDER_INCOMPLETE"
            # A partial stream may omit a later paid turn. Retain the reservation
            # rather than treating a known lower bound as the complete charge.
            usage = None
        if forbidden:
            error = "FORBIDDEN_PROVIDER_TOOL"
        answer: Any = None
        try:
            answer = json.loads((directory / "answer.json").read_text())
        except (OSError, ValueError):
            error = error or "INVALID_STRUCTURED_RESPONSE"
        if usage is None:
            error = error or "USAGE_UNAVAILABLE"
        cost = (
            None
            if usage is None
            else Cost(
                llm_tokens=usage["input_tokens"] + usage["output_tokens"],
                wall_seconds=time.monotonic() - started,
            ).model_dump(mode="json")
        )
        return {
            "provider": "codex-cli",
            "answer": answer,
            "usage": usage,
            "cost": cost,
            "error": error,
            "exit_code": process.returncode,
            "events": events,
            "stderr": (directory / "stderr.txt").read_text(),
            "note": "Cached input is a subset of input_tokens; no separate monetary charge is inferred",
        }
