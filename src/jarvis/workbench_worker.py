"""Trusted workbench child entry point. Run only behind Workbench, never directly."""

import json
import os
import resource
import runpy
import sys


def main() -> None:
    config = json.loads(sys.argv[1])
    limits = config["limits"]
    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits["file_bytes"], limits["file_bytes"]))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits["open_files"], limits["open_files"]))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if config["nproc"]:
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    target = config["target"]
    if config["mode"] == "command":
        os.execv(target[0], target)
    for path in reversed(config["site_paths"]):
        sys.path.insert(0, path)
    sys.path.insert(0, os.getcwd())
    sys.argv = list(target)
    runpy.run_path(target[0], run_name="__main__")


if __name__ == "__main__":
    main()
