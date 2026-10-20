"""Trusted minimal child entry point. Run only behind SkillSandbox, never directly."""

import json
import resource
import runpy
import sys
from pathlib import Path


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (536870912, 536870912))
    source, input_path, mode = sys.argv[1:]
    sys.path.insert(0, str(Path(source).parent))
    module = runpy.run_path(source)
    if mode == "test":
        module["self_test"]()
        print(json.dumps({"builder_self_test": "PASS"}))
    else:
        payload = json.loads(Path(input_path).read_text())
        print(json.dumps(module["run"](payload), allow_nan=False))


if __name__ == "__main__":
    main()
