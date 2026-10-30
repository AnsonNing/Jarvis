"""OS-isolated research workbench: agent-written analysis/code runs with declared mounts only.

Generalizes the skill sandbox: a declared analysis interpreter with pinned site paths,
read-only input mounts, one writable work directory, no network and resource limits.
Compiler commands may run only from declared toolchain directories.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from jarvis.contracts import Record
from jarvis.identity import digest, file_digest
from jarvis.skill_sandbox import SandboxUnavailable, SkillSandbox


class WorkbenchLimits(Record):
    wall_seconds: Annotated[int, Field(ge=1, le=7200, strict=True)] = 120
    cpu_seconds: Annotated[int, Field(ge=1, le=7200, strict=True)] = 60
    output_bytes: Annotated[int, Field(ge=1024, le=16777216, strict=True)] = 4194304
    file_bytes: Annotated[int, Field(ge=1024, le=2147483648, strict=True)] = 268435456
    open_files: Annotated[int, Field(ge=16, le=4096, strict=True)] = 256


class NativeExecution(Record):
    """Owner supplied capability; generated code never chooses compiler/flags."""

    compiler: str
    compiler_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    flags: tuple[str, ...] = Field(default=("-std=c++17", "-O0"), max_length=16)


class AnalysisEnvironment(Record):
    """Owner-declared interpreter packages; never Jarvis's own trusted runtime state."""

    interpreter: str | None = None
    interpreter_sha256: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    site_paths: tuple[str, ...] = Field(default=(), max_length=8)
    toolchain_paths: tuple[str, ...] = Field(default=(), max_length=8)
    packages: dict[str, str] = Field(default_factory=dict)
    limits: WorkbenchLimits = WorkbenchLimits()
    # Lets owner pipelines run binaries they compiled inside their own work directory
    # (e.g. an HPP parity probe). Still no network and no other writable location.
    allow_exec_in_workdir: bool = False
    extra_env: dict[str, str] = Field(default_factory=dict)
    native: NativeExecution | None = Field(default=None, exclude_if=lambda v: v is None)


def tail(data: bytes, limit: int = 12000) -> str:
    return data[-limit:].decode(errors="replace")


class Workbench:
    def __init__(self, environment: AnalysisEnvironment | None = None):
        self.environment = environment or AnalysisEnvironment()
        if self.environment.interpreter:
            self.interpreter = Path(self.environment.interpreter).resolve()
            if file_digest(self.interpreter) != self.environment.interpreter_sha256:
                raise SandboxUnavailable("declared analysis interpreter changed")
        else:
            self.interpreter = SkillSandbox().interpreter()
        self.worker = Path(__file__).with_name("workbench_worker.py").resolve()

    def identity(self) -> dict[str, Any]:
        executable = shutil.which("sandbox-exec" if sys.platform == "darwin" else "bwrap")
        return {
            "backend": "macos-sbpl-workbench-v1"
            if sys.platform == "darwin"
            else "linux-bwrap-workbench-v1",
            "available": bool(executable),
            "executable_hash": file_digest(Path(executable)) if executable else None,
            "worker_hash": file_digest(self.worker),
            "interpreter_hash": file_digest(self.interpreter),
            "environment": digest(self.environment),
            "network": "denied",
            "credentials": "not mounted or inherited",
        }

    def _profile(self, workdir: Path, readonly: list[Path], executables: list[Path]) -> str:
        def literal(p: Path | str) -> str:
            return "(literal " + json.dumps(str(p)) + ")"

        def subpath(p: Path | str) -> str:
            return "(subpath " + json.dumps(str(p)) + ")"

        prefix = Path(sys.base_prefix).resolve()
        if self.environment.interpreter:
            prefix = self.interpreter.parent.parent
        reads = [
            subpath(prefix),
            subpath(workdir),
            literal(self.worker),
            subpath("/System/Library"),
            subpath("/usr/lib"),
            subpath("/usr/share/zoneinfo"),
            subpath("/private/var/db/timezone"),
            literal("/"),
            literal("/dev/null"),
            literal("/dev/urandom"),
            literal("/dev/random"),
            *[subpath(Path(p).resolve()) for p in self.environment.site_paths],
            *[subpath(p.resolve()) for p in readonly],
        ]
        execs = [literal(self.interpreter)]
        if self.environment.allow_exec_in_workdir:
            execs.append(subpath(workdir))
        for tool in [*self.environment.toolchain_paths, *[str(e) for e in executables]]:
            path = Path(tool).resolve()
            reads.append(subpath(path))
            execs.append(subpath(path))
        return "\n".join(
            [
                "(version 1)",
                "(deny default)",
                "(allow process-exec " + " ".join(execs) + ")",
                "(allow process-fork)",
                "(allow sysctl-read)",
                "(allow file-read-metadata)",
                '(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo"))',
                "(allow file-read* " + " ".join(reads) + ")",
                "(allow file-write* " + subpath(workdir) + " " + literal("/dev/null") + ")",
                "(deny network*)",
            ]
        )

    def _command(
        self,
        arguments: list[str],
        workdir: Path,
        readonly: list[Path],
        executables: list[Path],
        cwd: Path | None = None,
    ) -> list[str]:
        if sys.platform == "darwin" and shutil.which("sandbox-exec"):
            return [
                "/usr/bin/sandbox-exec",
                "-p",
                self._profile(workdir, readonly, executables),
                *arguments,
            ]
        executable = shutil.which("bwrap") if sys.platform.startswith("linux") else None
        if not executable:
            raise SandboxUnavailable("No supported OS sandbox; agent code was not executed")
        prefix = Path(sys.base_prefix).resolve()
        cmd = [
            executable,
            "--unshare-all",
            "--new-session",
            "--die-with-parent",
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        mounts = [
            Path("/usr"),
            Path("/lib"),
            Path("/lib64"),
            Path("/etc/ld.so.cache"),
            prefix,
            self.worker,
            *[Path(p).resolve() for p in self.environment.site_paths],
            *[Path(p).resolve() for p in self.environment.toolchain_paths],
            *[p.resolve() for p in readonly],
        ]
        for mount in mounts:
            if mount.exists():
                cmd += ["--ro-bind", str(mount), str(mount)]
        return cmd + [
            "--bind",
            str(workdir),
            str(workdir),
            "--chdir",
            str(cwd or workdir),
            *arguments,
        ]

    def execute(
        self,
        mode: str,
        target: list[str],
        workdir: Path,
        *,
        readonly: tuple[Path, ...] = (),
        executables: tuple[Path, ...] = (),
        log_name: str = "run",
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        """Run `python script` or an allowed toolchain command; only `workdir` is writable.

        `cwd` (default `workdir`) must be `workdir` or one of the read-only mounts.
        """
        if mode not in {"python", "command", "native"}:
            raise SandboxUnavailable("unexpected workbench operation")
        workdir = workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (workdir / ".tmp").mkdir(exist_ok=True, mode=0o700)
        logs = workdir.parent / (workdir.name + ".logs")
        logs.mkdir(exist_ok=True, mode=0o700)
        limits = self.environment.limits
        config = {
            "mode": mode,
            "target": target,
            "site_paths": [str(Path(p).resolve()) for p in self.environment.site_paths],
            "limits": limits.model_dump(mode="json"),
            "nproc": mode != "command",
        }
        arguments = [str(self.interpreter), "-I", "-B", str(self.worker), json.dumps(config)]
        cwd = (cwd or workdir).resolve()
        if cwd != workdir and not any(cwd == r.resolve() for r in readonly):
            raise SandboxUnavailable(
                "working directory must be writable or a declared read-only mount"
            )
        command = self._command(arguments, workdir, list(readonly), list(executables), cwd)
        started = time.monotonic()
        stdout_path, stderr_path = logs / f"{log_name}.stdout", logs / f"{log_name}.stderr"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env={
                    "PATH": "/usr/bin:/bin",
                    "TMPDIR": str(workdir / ".tmp"),
                    "HOME": str(workdir),
                    "MPLBACKEND": "Agg",
                    "OMP_NUM_THREADS": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    **self.environment.extra_env,
                },
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            timed_out = False
            try:
                process.wait(timeout=limits.wall_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        out = stdout_path.read_bytes()
        return {
            "status": "PASS" if process.returncode == 0 and not timed_out else "FAIL",
            "exit_code": process.returncode,
            "timeout": timed_out,
            "stdout": tail(out[: limits.output_bytes]),
            "stdout_truncated": len(out) > 12000,
            "stderr": tail(stderr_path.read_bytes()),
            "wall_seconds": time.monotonic() - started,
        }

    def probe(self, directory: Path) -> dict[str, Any]:
        """Trusted sentinels: host read, outside write and network must all be denied."""
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        secret = directory / "host-sentinel"
        secret.write_text("synthetic isolation sentinel")
        outside = directory / "outside-write"
        workdir = directory / "work"
        workdir.mkdir(mode=0o700)
        (workdir / "probe.py").write_text(
            "import json, socket, sys\n"
            "flags = {}\n"
            "for name, path, mode in [('host_read', sys.argv[1], 'r'), ('outside_write', sys.argv[2], 'w')]:\n"
            "    try:\n"
            "        with open(path, mode): pass\n"
            "        flags[name] = False\n"
            "    except (PermissionError, FileNotFoundError, OSError):\n"
            "        flags[name] = True\n"
            "try:\n"
            "    s = socket.socket(); s.settimeout(1); s.connect(('1.1.1.1', 53))\n"
            "    flags['network'] = False\n"
            "except OSError:\n"
            "    flags['network'] = True\n"
            "with open('probe.json', 'w') as f: json.dump(flags, f)\n"
        )
        try:
            result = self.execute(
                "python", ["probe.py", str(secret), str(outside)], workdir, log_name="probe"
            )
        except (SandboxUnavailable, OSError) as exc:
            return {"status": "UNAVAILABLE", "reason": str(exc), "workbench": self.identity()}
        try:
            flags = json.loads((workdir / "probe.json").read_text())
        except (OSError, ValueError):
            flags = None
        passed = (
            result["status"] == "PASS"
            and flags == {"host_read": True, "outside_write": True, "network": True}
            and not outside.exists()
        )
        return {
            "status": "PASS" if passed else "UNAVAILABLE",
            "flags": flags,
            "run": result,
            "workbench": self.identity(),
        }
