"""Admitted native smoke tests, with real OS isolation and sealed observations.

No shell, agent-supplied flags, host builds or private strategy assumptions.
Receipts establish execution and source identity, not scientific correctness.
"""

import json
from pathlib import Path
from typing import Any

from jarvis.contracts import AdmissionError
from jarvis.identity import digest, file_digest
from jarvis.storage import Store
from jarvis.workbench import Workbench


def capability(bench: Workbench) -> dict[str, Any]:
    spec = bench.environment.native
    if spec is None:
        return {"available": False, "reason": "native capability not declared"}
    compiler = Path(spec.compiler)
    available = compiler.is_absolute() and compiler.is_file()
    if available:
        available = file_digest(compiler.resolve()) == spec.compiler_sha256
    return {
        "available": bool(available and bench.identity()["available"]),
        "reason": "owner-declared hash-pinned compiler and OS workbench required",
        "spec_ref": digest(spec),
        "tool": "run_native",
        "arguments": "none; owner flags only",
        "scope": "local native smoke; no engine/backtest/causal-validity claim",
    }


def source_manifest(workspace: Path) -> dict[str, str]:
    sources = {}
    for section in ("work", "sources", "base", "inputs"):
        for path in sorted((workspace / section).rglob("*")):
            if path.is_file() and not any(
                p.startswith(".native-") or p == ".tmp" for p in path.parts
            ):
                if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()):
                    raise AdmissionError("native input path escapes workspace")
                sources[str(path.relative_to(workspace))] = file_digest(path)
    if len(sources) > 256:
        raise AdmissionError("native source manifest exceeds 256 inputs")
    return sources


def execute_native(
    store: Store, bench: Workbench, workspace: Path, source: str, index: int
) -> dict[str, Any]:
    from jarvis.workspace_agent import safe_path

    spec = bench.environment.native
    if not capability(bench)["available"] or spec is None:
        raise AdmissionError("NATIVE_UNAVAILABLE: declare an unchanged compiler and OS sandbox")
    if not source.startswith("work/") or Path(source).suffix not in {".cpp", ".cc", ".cxx"}:
        raise AdmissionError("run_native requires a saved C++ source under work/")
    path = safe_path(workspace / "work", source.removeprefix("work/"))
    if not path.is_file():
        raise AdmissionError("native source is missing")
    manifest = source_manifest(workspace)
    if sum((workspace / p).stat().st_size for p in manifest) > 32 * 1024 * 1024:
        raise AdmissionError("native input envelope exceeds 32 MiB")
    source_blobs = {p: store.blobs.put_file(workspace / p) for p in manifest}
    work = workspace / "work"
    build = work / f".native-{index}"
    build.mkdir(exist_ok=True, mode=0o700)
    binary = build / "probe"
    compiler = Path(spec.compiler).resolve()
    # argv[0] selects clang++/g++ driver behavior. Resolving a symlink for the
    # invocation loses automatic C++ runtime linkage even when its hash matches.
    invocation = Path(spec.compiler).absolute()
    compile_argv = [str(invocation), *spec.flags, str(path), "-o", str(binary)]
    compilation = bench.execute(
        "command",
        compile_argv,
        work,
        readonly=(workspace,),
        executables=(compiler,),
        cwd=workspace,
        log_name=f"native-{index}-compile",
    )
    execution = None
    binary_hash = None
    if compilation["status"] == "PASS" and binary.is_file():
        binary_hash = file_digest(binary)
        # The compiled program gets no compiler capability, even when the owner's
        # compilation environment mounts a toolchain. RLIMIT_NPROC also forbids forks.
        restricted = Workbench(
            bench.environment.model_copy(
                update={
                    "toolchain_paths": (),
                    "allow_exec_in_workdir": False,
                    "native": None,
                }
            )
        )
        execution = restricted.execute(
            "native",
            [str(binary)],
            work,
            readonly=(workspace,),
            executables=(binary,),
            cwd=workspace,
            log_name=f"native-{index}-run",
        )
    unchanged = all(
        (workspace / p).is_file() and file_digest(workspace / p) == h for p, h in manifest.items()
    )
    compiler_unchanged = file_digest(compiler) == spec.compiler_sha256
    receipt = {
        "kind": "NativeExecutionReceipt",
        "origin": "trusted_harness",
        "status": "PASS"
        if unchanged and compiler_unchanged and execution and execution["status"] == "PASS"
        else "FAIL",
        "source": source,
        "source_manifest": manifest,
        "inputs_unchanged": unchanged,
        "source_blobs": source_blobs,
        "compiler_sha256": file_digest(compiler),
        "compiler_unchanged": compiler_unchanged,
        "compiler_invocation": str(invocation),
        "compiler_resolved": str(compiler),
        "compile_argv": compile_argv,
        "run_argv": [str(binary)] if execution else None,
        "capability_ref": digest(spec),
        "binary_sha256": binary_hash,
        "compilation": compilation,
        "execution": execution,
        "scope": "Observed local native test. Assertions are proposed code, not a fixed scientific evaluator. Python regeneration may consume this sealed receipt without repeating compilation.",
    }
    ref = store.artifacts.put(receipt)
    name = f"native-{ref[7:23]}.json"
    target = workspace / "inputs" / name
    target.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    target.chmod(0o400)
    return {**receipt, "receipt_ref": ref, "input_path": "inputs/" + name}
