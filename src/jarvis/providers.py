"""Real Codex CLI structured completion with captured usage and no domain tools."""

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol

from jarvis.contracts import AdmissionError, Cost, JarvisError
from jarvis.identity import file_digest
from jarvis.researcher import AgentConfig

QUOTA_PATTERN = re.compile(
    r"usage limit|rate limit|quota|insufficient credits|purchase more credits", re.I
)


def quota_refusal(events: list[dict[str, Any]]) -> str | None:
    """Message of a service-side usage/rate-limit refusal, if the stream reports one."""
    for event in events:
        if event.get("type") in {"turn.failed", "error"}:
            message = str((event.get("error") or {}).get("message") or event.get("message") or "")
            if QUOTA_PATTERN.search(message):
                return message
    return None


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
            **(
                {"reasoning_effort": self.config.reasoning_effort}
                if self.config.reasoning_effort
                else {}
            ),
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
        if self.config.reasoning_effort:
            cmd += ["-c", 'model_reasoning_effort="' + self.config.reasoning_effort + '"']
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
        refusal = quota_refusal(events)
        if refusal and not terminal and not forbidden:
            # The service refused the turn before any model work (usage/rate limit):
            # nothing was generated, so the call is settled with zero tokens.
            error = "PROVIDER_QUOTA_EXHAUSTED: " + refusal[:300]
            usage = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        answer: Any = None
        try:
            answer = json.loads((directory / "answer.json").read_text())
        except (OSError, ValueError):
            error = error or "INVALID_STRUCTURED_RESPONSE"
        if usage is None:
            error = error or "USAGE_UNAVAILABLE"
        if error and error.startswith("PROVIDER_QUOTA_EXHAUSTED"):
            answer = None
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


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.encode()) // 4)


class RelayProvider:
    """A human or assistant answers each role request through files (tools-disabled).

    `complete` writes prompt.txt and schema.json, announces the request under
    `<relay>/pending/`, and waits for `answer.json` in the request directory. Token
    units are estimated from bytes because no metered API is involved.
    """

    def __init__(self, config: AgentConfig, relay: Path, *, timeout_seconds: float = 6 * 3600):
        self.config, self.relay, self.timeout_seconds = config, relay.resolve(), timeout_seconds

    def identity(self) -> dict[str, Any]:
        return {
            "provider": "file-relay",
            "model": self.config.model or "relay-answerer",
            "tools": "disabled",
            "token_units": "estimated from bytes",
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        directory = directory.resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        (directory / "prompt.txt").write_text(prompt)
        (directory / "schema.json").write_text(json.dumps(schema))
        pending = self.relay / "pending"
        pending.mkdir(parents=True, exist_ok=True, mode=0o700)
        ticket = pending / (directory.name + ".json")
        ticket.write_text(
            json.dumps(
                {
                    "directory": str(directory),
                    "schema_title": schema.get("title"),
                    "prompt_bytes": len(prompt.encode()),
                    "created": time.time(),
                }
            )
        )
        started = time.monotonic()
        answer_path = directory / "answer.json"
        while not answer_path.exists():
            if time.monotonic() - started > self.timeout_seconds:
                ticket.unlink(missing_ok=True)
                return {
                    "answer": None,
                    "usage": None,
                    "cost": None,
                    "error": "PROVIDER_TIMEOUT",
                    "events": [],
                    "stderr": "relay answer not received",
                }
            time.sleep(1)
        time.sleep(0.2)  # allow the writer to finish
        raw = answer_path.read_text()
        ticket.unlink(missing_ok=True)
        try:
            answer: Any = json.loads(raw)
            error = None
        except ValueError:
            answer, error = None, "INVALID_STRUCTURED_RESPONSE"
        usage = {
            "input_tokens": _estimate_tokens(prompt),
            "output_tokens": _estimate_tokens(raw),
            "cached_input_tokens": 0,
        }
        return {
            "provider": "file-relay",
            "answer": answer,
            "usage": usage,
            "cost": Cost(
                llm_tokens=usage["input_tokens"] + usage["output_tokens"],
                wall_seconds=time.monotonic() - started,
            ).model_dump(mode="json"),
            "error": error,
            "events": [],
            "stderr": "",
            "note": "estimated token units",
        }


class ClaudeCodeProvider:
    """Headless Claude Code (`claude -p --json-schema`) with every tool disabled.

    The executable comes from JARVIS_CLAUDE_EXECUTABLE or PATH and must already be
    logged in. Usage and quota refusals are read from its JSON result.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        located = os.environ.get("JARVIS_CLAUDE_EXECUTABLE") or shutil.which("claude")
        if not located or not Path(located).exists():
            raise JarvisError("Claude Code executable unavailable; set JARVIS_CLAUDE_EXECUTABLE")
        self.executable = Path(located).absolute()

    def identity(self) -> dict[str, Any]:
        version = subprocess.run(
            [str(self.executable), "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout.strip()
        return {
            "provider": "claude-code",
            "version": version,
            "executable_sha256": file_digest(self.executable),
            "model": self.config.model,
            **({"effort": self.config.reasoning_effort} if self.config.reasoning_effort else {}),
            "tools": "disabled",
            "session_persistence": "disabled",
        }

    def complete(self, prompt: str, schema: dict[str, Any], directory: Path) -> dict[str, Any]:
        directory = directory.resolve()
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        (directory / "prompt.txt").write_text(prompt)
        cmd = [
            str(self.executable),
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
            "--tools",
            "",
            "--no-session-persistence",
            "--permission-mode",
            "default",
        ]
        if self.config.model:
            cmd += ["--model", self.config.model]
        if self.config.reasoning_effort in {"low", "medium", "high", "xhigh", "max"}:
            cmd += ["--effort", self.config.reasoning_effort]
        env = {
            k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
        }
        started = time.monotonic()
        try:
            process = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                cwd=directory,
                env=env,
                timeout=self.config.timeout_seconds,
            )
            out, timed_out = process.stdout, False
        except subprocess.TimeoutExpired as exc:
            out, timed_out = (
                (exc.stdout or b"").decode()
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or ""),
                True,
            )
        (directory / "result.json").write_text(out)
        try:
            data = json.loads(out)
        except ValueError:
            data = {}
        text = str(data.get("result") or "")
        if timed_out:
            error, usage = "PROVIDER_TIMEOUT", None
        elif data.get("is_error") and QUOTA_PATTERN.search(text):
            error = "PROVIDER_QUOTA_EXHAUSTED: " + text[:300]
            usage = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        elif data.get("is_error") or not data:
            error, usage = "PROVIDER_INCOMPLETE: " + text[:300], None
        else:
            raw = data.get("usage") or {}
            cached = int(raw.get("cache_read_input_tokens", 0)) + int(
                raw.get("cache_creation_input_tokens", 0)
            )
            usage = {
                "input_tokens": int(raw.get("input_tokens", 0)) + cached,
                "output_tokens": int(raw.get("output_tokens", 0)),
                "cached_input_tokens": cached,
            }
            error = None
        answer: Any = data.get("structured_output")
        if answer is None and error is None:
            try:
                answer = json.loads(text)
            except ValueError:
                error = "INVALID_STRUCTURED_RESPONSE"
        cost = (
            None
            if usage is None
            else Cost(
                llm_tokens=usage["input_tokens"] + usage["output_tokens"],
                wall_seconds=time.monotonic() - started,
            ).model_dump(mode="json")
        )
        return {
            "provider": "claude-code",
            "answer": answer,
            "usage": usage,
            "cost": cost,
            "error": error,
            "events": [],
            "stderr": "",
            "result_subtype": data.get("subtype"),
        }
