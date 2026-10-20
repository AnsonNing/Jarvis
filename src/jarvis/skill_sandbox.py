"""OS-enforced execution of generated skills with minimal read-only mounts."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from jarvis.contracts import AdmissionError
from jarvis.identity import file_digest


class SandboxUnavailable(AdmissionError):
    pass


class SkillSandbox:
    def probe(self, directory: Path) -> dict[str, Any]:
        """Execute trusted sentinels before requesting any generated implementation."""
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        secret = directory / "host-sentinel"
        secret.write_text("synthetic isolation sentinel")
        source = directory / "source" / "implementation.py"
        source.parent.mkdir(mode=0o700)
        source.write_text(
            "import os, socket\n"
            "def run(payload):\n"
            "    flags = {}\n"
            "    for name, path, mode in [('host_read', payload['sentinel'], 'r'), ('source_write', __file__, 'w')]:\n"
            "        try:\n"
            "            with open(path, mode): pass\n"
            "            flags[name] = False\n"
            "        except (PermissionError, FileNotFoundError):\n"
            "            flags[name] = True\n"
            "    try:\n"
            "        sock = socket.socket(); sock.connect(('127.0.0.1', 9))\n"
            "        flags['network'] = False\n"
            "    except PermissionError:\n"
            "        flags['network'] = True\n"
            "    except OSError:\n"
            "        flags['network'] = bool(payload.get('host_netns')) and os.readlink('/proc/self/ns/net') != payload['host_netns']\n"
            "    return flags\n"
        )
        try:
            result = self.run(
                source,
                {
                    "sentinel": str(secret),
                    "host_netns": os.readlink("/proc/self/ns/net")
                    if sys.platform.startswith("linux")
                    else None,
                },
                directory / "execution",
            )
        except (SandboxUnavailable, OSError) as exc:
            return {"status": "UNAVAILABLE", "reason": str(exc), "sandbox": self.identity()}
        passed = result["status"] == "PASS" and result["value"] == {
            "host_read": True,
            "source_write": True,
            "network": True,
        }
        return {**result, "status": "PASS" if passed else "UNAVAILABLE"}

    def identity(self) -> dict[str, Any]:
        executable = shutil.which("sandbox-exec" if sys.platform == "darwin" else "bwrap")
        return {
            "backend": "macos-sbpl-v1" if sys.platform == "darwin" else "linux-bwrap-v1",
            "available": bool(executable),
            "executable_hash": file_digest(Path(executable)) if executable else None,
            "worker_hash": file_digest(Path(__file__).with_name("skill_worker.py")),
            "network": "denied",
            "credentials": "not mounted or inherited",
            "limits": {
                "wall_seconds": 8,
                "cpu_seconds": 3,
                "output_bytes": 1048576,
                "open_files": 32,
                "address_space_bytes": None if sys.platform == "darwin" else 536870912,
            },
        }

    def command(self, source: Path, inputs: Path, output: Path, mode: str) -> list[str]:
        python = Path(sys.executable).resolve()
        prefix = Path(sys.base_prefix).resolve()
        worker = Path(__file__).with_name("skill_worker.py").resolve()
        source, inputs, output = source.resolve(), inputs.resolve(), output.resolve()
        arguments = [str(python), "-I", "-S", "-B", str(worker), str(source), str(inputs), mode]
        if sys.platform == "darwin" and shutil.which("sandbox-exec"):

            def literal(p: Path | str) -> str:
                return "(literal " + json.dumps(str(p)) + ")"

            def subpath(p: Path | str) -> str:
                return "(subpath " + json.dumps(str(p)) + ")"

            reads = [
                subpath(prefix),
                subpath(source.parent),
                subpath(output),
                literal(inputs),
                literal(worker),
                subpath("/System/Library"),
                subpath("/usr/lib"),
                literal("/"),
                literal("/dev/null"),
                literal("/dev/urandom"),
                literal("/dev/random"),
            ]
            profile = "\n".join(
                [
                    "(version 1)",
                    "(deny default)",
                    "(allow process-exec " + literal(python) + ")",
                    "(allow sysctl-read)",
                    "(allow file-read-metadata)",
                    "(allow file-read* " + " ".join(reads) + ")",
                    "(allow file-write* " + subpath(output) + " " + literal("/dev/null") + ")",
                    "(deny network*)",
                ]
            )
            return ["/usr/bin/sandbox-exec", "-p", profile, *arguments]
        executable = shutil.which("bwrap") if sys.platform.startswith("linux") else None
        if not executable:
            raise SandboxUnavailable("No supported OS sandbox; generated code was not executed")
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
        for mount in [Path("/usr"), Path("/lib"), Path("/lib64"), prefix, source.parent]:
            if mount.exists():
                cmd += ["--ro-bind", str(mount), str(mount)]
        for mount in [worker, inputs, Path("/etc/ld.so.cache")]:
            if mount.exists():
                cmd += ["--ro-bind", str(mount), str(mount)]
        return cmd + ["--bind", str(output), str(output), "--chdir", str(output), *arguments]

    def run(
        self, source: Path, payload: dict[str, Any], directory: Path, *, mode: str = "run"
    ) -> dict[str, Any]:
        if mode not in {"run", "test"}:
            raise AdmissionError("unexpected sandbox operation")
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        inputs, output = directory / "input.json", directory / "output"
        output.mkdir(mode=0o700)
        inputs.write_text(json.dumps(payload, allow_nan=False))
        command = self.command(source, inputs, output, mode)
        start = time.monotonic()
        with (
            (directory / "stdout").open("wb") as stdout,
            (directory / "stderr").open("wb") as stderr,
        ):
            process = subprocess.Popen(
                command,
                cwd=output,
                env={"PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            timed_out = False
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        raw = (directory / "stdout").read_bytes()
        error = (directory / "stderr").read_bytes()[:10000].decode(errors="replace")
        try:
            value = json.loads(raw) if len(raw) <= 1048576 else None
        except ValueError:
            value = None
        return {
            "status": "PASS"
            if process.returncode == 0 and not timed_out and value is not None
            else "FAIL",
            "value": value,
            "exit_code": process.returncode,
            "timeout": timed_out,
            "stderr": error,
            "wall_seconds": time.monotonic() - start,
            "sandbox": self.identity(),
        }
