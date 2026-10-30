"""Agentic research engineer: one receipted tool call per turn inside the OS workbench.

The provider itself has no tools. The harness executes each requested step (write a
file, run Python, read, list, validate) behind `Workbench`, records the observation and
returns it in the next context. Workspace layout:

    inputs/   read-only observed tables      sources/  read-only declared sources
    base/     read-only implementation base  work/     the only writable directory
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from jarvis.autonomy_jobs import (
    ProviderUnavailable,
    is_quota_refusal,
    local_job,
    settle_ended_provider_calls,
)
from jarvis.bounded_roles import evidence_role
from jarvis.contracts import AdmissionError, Cost, JarvisError, Record
from jarvis.native_execution import capability
from jarvis.providers import ResearchProvider
from jarvis.research_contract import Ref
from jarvis.researcher import AgentConfig
from jarvis.storage import Store
from jarvis.workbench import Workbench

READ_LIMIT = 40000
# Each retry after an output-limit ending asks for a smaller step; give it room to converge.
WORKER_PROVIDER_RETRIES = 6
SECTIONS = ("inputs", "sources", "base", "skills", "work")
READONLY = ("inputs", "sources", "base", "skills")


class WorkerFinding(Record):
    statement: str = Field(min_length=1, max_length=1200)
    value: str = Field(min_length=1, max_length=1500)


class WorkerStep(Record):
    context_id: Ref
    note: str = Field(min_length=1, max_length=1200)
    tool: Literal[
        "write_file", "run_python", "run_native", "read_file", "list_files", "validate", "finish"
    ]
    path: str | None
    content: str | None = Field(max_length=200000)
    arguments: tuple[str, ...] = Field(max_length=16)
    summary: str | None = Field(max_length=8000)
    findings: tuple[WorkerFinding, ...] = Field(max_length=40)
    output_files: tuple[str, ...] = Field(max_length=24)
    final_script: str | None


def prepare_workspace(
    root: Path,
    *,
    inputs: dict[str, bytes],
    sources: dict[str, bytes],
    base: dict[str, bytes],
    skills: dict[str, bytes] | None = None,
) -> Path:
    if root.exists():
        return root
    staging = root.with_name(root.name + ".staging")
    shutil.rmtree(staging, ignore_errors=True)
    for section, files in (
        ("inputs", inputs),
        ("sources", sources),
        ("base", base),
        ("skills", skills or {}),
    ):
        (staging / section).mkdir(parents=True, mode=0o700)
        for name, data in files.items():
            target = safe_path(staging / section, name)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(data)
            target.chmod(0o400)
    (staging / "work").mkdir(mode=0o700)
    for name, data in base.items():
        target = safe_path(staging / "work", name)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(data)
    staging.rename(root)
    return root


def safe_path(base: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not relative or relative.startswith("~"):
        raise AdmissionError("path must stay inside the workspace section")
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise AdmissionError("path escapes the workspace section")
    return resolved


def listing(root: Path, limit: int = 400) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in SECTIONS:
        for path in sorted((root / section).rglob("*")):
            if path.is_file() and len(rows) < limit:
                rows.append(
                    {
                        "path": str(path.relative_to(root)),
                        "bytes": path.stat().st_size,
                        "writable": section == "work",
                    }
                )
    return rows


def run_workspace_task(
    store: Store,
    owner: str,
    *,
    task: dict[str, Any],
    workspace: Path,
    provider: ResearchProvider,
    config: AgentConfig,
    workbench: Workbench,
    max_steps: int,
    validator: Callable[[Path], dict[str, Any]] | None = None,
    guidance: str = "",
    finish_validator: Callable[[WorkerStep, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    transcript: list[dict[str, Any]] = []
    for prior in [e["payload"] for e in store.events(owner) if e["event_type"] == "WORKER_STEP"]:
        transcript.append(
            {
                "step": prior["index"],
                "request": store.artifacts.get(prior["step_ref"]),
                "observation": store.artifacts.get(prior["observation_ref"]),
            }
        )
    for index in range(len(transcript), max_steps):
        if (
            transcript
            and transcript[-1]["request"]["tool"] == "finish"
            and transcript[-1]["observation"].get("status") == "OK"
        ):
            break  # an invalid finish is returned to the worker to correct
        context = {
            "task": task,
            "workspace": listing(workspace),
            "steps_remaining": max_steps - index,
            "validator_available": validator is not None,
            "native_execution": capability(workbench),
            "transcript": [
                t
                if t["step"] >= len(transcript) - 8
                else {"step": t["step"], "note": t["request"]["note"], "tool": t["request"]["tool"]}
                for t in transcript
            ],
            "visible_evidence_ids": [],
        }

        def check(answer: Record, frozen: dict[str, Any]) -> None:
            assert isinstance(answer, WorkerStep)
            if answer.tool in {"write_file", "read_file"} and not answer.path:
                raise ValueError("file tools require a path")
            if answer.tool == "write_file" and (
                answer.content is None or not (answer.path or "").startswith("work/")
            ):
                raise ValueError("write_file needs content and a path under work/")
            if answer.tool == "run_python" and not (
                answer.arguments or answer.path or answer.content
            ):
                raise ValueError(
                    "run_python needs inline code in content, or a script path under work/ in path or arguments[0]"
                )
            if answer.tool == "validate" and not frozen["validator_available"]:
                raise ValueError("no validator for this task")
            if answer.tool == "run_native" and (
                not frozen["native_execution"]["available"]
                or not answer.path
                or answer.arguments
                or answer.content is not None
            ):
                raise ValueError(
                    "run_native requires the declared capability, a saved path and no arguments/content"
                )
            if answer.tool == "finish" and not answer.summary:
                raise ValueError("finish requires a summary")

        step = None
        while step is None:
            failures = [
                e["payload"]
                for e in store.events(owner)
                if e["event_type"] == "WORKER_PROVIDER_FAILURE" and e["payload"]["index"] == index
            ]
            if len(failures) >= WORKER_PROVIDER_RETRIES:
                raise AdmissionError(
                    f"worker provider ended without usage {WORKER_PROVIDER_RETRIES} times"
                )
            paused = [
                e
                for e in store.events(owner)
                if e["event_type"] == "WORKER_PROVIDER_PAUSED" and e["payload"]["index"] == index
            ]
            try:
                step = evidence_role(
                    store,
                    owner,
                    f"worker:{index}"
                    + (f":p{len(failures)}" if failures else "")
                    + (f":q{len(paused)}" if paused else ""),
                    "autonomy_worker",
                    {
                        **context,
                        **({"retry_attempt": len(failures)} if failures else {}),
                        **(
                            {
                                "output_limit_recovery": {
                                    "reason": "The previous provider process ended at max_output_tokens; its usage remains unknown and its full reservation was charged.",
                                    "instruction": "Use one small step now. Split long scripts across short write_file calls, reuse saved files, and keep finish concise. Never regenerate raw data in the answer or repeat completed experiments. Preserve the declared reasoning effort.",
                                }
                            }
                            if failures and "OUTPUT_LIMIT" in failures[-1]["reason"]
                            else {}
                        ),
                        **({"provider_retry": len(paused)} if paused else {}),
                    },
                    WorkerStep,
                    provider,
                    config,
                    guidance,
                    validate=check,
                )
            except JarvisError as exc:
                if is_quota_refusal(exc):
                    store.append(
                        owner, "WORKER_PROVIDER_PAUSED", {"index": index, "reason": str(exc)[:500]}
                    )
                    raise ProviderUnavailable(str(exc)) from exc
                if isinstance(exc, AdmissionError) or not settle_ended_provider_calls(store, owner):
                    raise
                store.append(
                    owner, "WORKER_PROVIDER_FAILURE", {"index": index, "reason": str(exc)[:500]}
                )
        assert isinstance(step, WorkerStep)
        observation = execute_step(
            store,
            owner,
            index,
            step,
            workspace,
            workbench,
            validator,
            finish_validator=finish_validator,
        )
        store.append(
            owner,
            "WORKER_STEP",
            {
                "index": index,
                "step_ref": store.artifacts.put(step),
                "observation_ref": store.artifacts.put(observation),
            },
        )
        transcript.append(
            {"step": index, "request": step.model_dump(mode="json"), "observation": observation}
        )
    last = transcript[-1]["request"] if transcript else None
    if not last or last["tool"] != "finish" or transcript[-1]["observation"].get("status") != "OK":
        if last and last["tool"] == "finish":
            return {
                "status": "INVALID_FINISH",
                "finish_check": transcript[-1]["observation"],
                "summary": last["summary"],
                "findings": last["findings"],
                "steps": len(transcript),
                "transcript_owner": owner,
            }
        return {"status": "STEP_LIMIT", "steps": len(transcript), "transcript_owner": owner}
    finish_check = transcript[-1]["observation"]
    return {
        "status": "FINISHED" if finish_check.get("status") == "OK" else "INVALID_FINISH",
        "summary": last["summary"],
        "findings": last["findings"],
        "output_files": last["output_files"],
        "final_script": last["final_script"],
        "finish_check": finish_check,
        "steps": len(transcript),
        "transcript_owner": owner,
    }


def execute_step(
    store: Store,
    owner: str,
    index: int,
    step: WorkerStep,
    workspace: Path,
    workbench: Workbench,
    validator: Callable[[Path], dict[str, Any]] | None,
    *,
    finish_validator: Callable[[WorkerStep, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    work = workspace / "work"

    def write() -> dict[str, Any]:
        target = safe_path(work, (step.path or "")[len("work/") :])
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_text(step.content or "")
        return {"status": "OK", "written": step.path, "bytes": target.stat().st_size}

    def read() -> dict[str, Any]:
        section, _, rest = (step.path or "").partition("/")
        if section not in SECTIONS:
            raise AdmissionError("read path must start with inputs/, sources/, base/ or work/")
        target = safe_path(workspace / section, rest)
        data = target.read_bytes()
        return {
            "status": "OK",
            "path": step.path,
            "bytes": len(data),
            "content": data[:READ_LIMIT].decode(errors="replace"),
            "truncated": len(data) > READ_LIMIT,
        }

    def run() -> dict[str, Any]:
        named = step.path or (step.arguments[0] if step.arguments else None)
        script = (named or f"work/step_{index}.py").removeprefix("./")
        script = script if script.startswith("work/") else "work/" + script
        target = safe_path(work, script[len("work/") :])
        if step.content is not None:
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_text(step.content)
        elif not target.is_file():
            raise AdmissionError(
                f"{script} does not exist; write it first or pass inline code in content"
            )
        extra = list(
            step.arguments[1:] if step.arguments and step.arguments[0] == named else step.arguments
        )
        result = workbench.execute(
            "python",
            [script, *extra],
            work,
            readonly=(workspace, *(workspace / s for s in READONLY)),
            cwd=workspace,
            log_name=f"step-{index}",
        )
        return {**result, "script": script}

    def validate() -> dict[str, Any]:
        assert validator is not None
        return validator(work)

    def native() -> dict[str, Any]:
        from jarvis.native_execution import execute_native

        if step.arguments or step.content is not None:
            raise AdmissionError("native flags/arguments/content cannot be supplied by the worker")
        return execute_native(store, workbench, workspace, step.path or "", index)

    def finish() -> dict[str, Any]:
        missing = [
            f
            for f in [*step.output_files, *([step.final_script] if step.final_script else [])]
            if not safe_path(work, f.removeprefix("work/")).is_file()
        ]
        if missing:
            return {"status": "MISSING_OUTPUTS", "missing": missing}
        if finish_validator:
            return finish_validator(step, index)
        return {"status": "OK", "missing": []}

    if step.tool == "list_files":
        return {"status": "OK", "files": listing(workspace)}
    function = {
        "write_file": write,
        "read_file": read,
        "run_python": run,
        "run_native": native,
        "validate": validate,
        "finish": finish,
    }[step.tool]
    reserved = Cost(
        actions=1,
        tool_calls=1,
        compute_units=max(1.0, workbench.environment.limits.wall_seconds / 60)
        if step.tool in {"run_python", "run_native", "validate"}
        else 1,
        wall_seconds=workbench.environment.limits.wall_seconds
        if step.tool in {"run_python", "run_native", "validate"}
        else 10,
    )
    if step.tool == "run_native":
        reserved = reserved.model_copy(
            update={
                "compute_units": reserved.compute_units * 2,
                "wall_seconds": reserved.wall_seconds * 2,
            }
        )
    outcome = local_job(
        store,
        owner,
        f"tool:{index}",
        {"tool": step.tool, "path": step.path, "arguments": list(step.arguments)},
        function,
        reserved=reserved,
    )
    if outcome["error"]:
        return {"status": "ERROR", "error": outcome["error"]}
    return dict(outcome["value"])
