"""Task validation and schema export for the initial trusted kernel."""

import argparse
import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from jarvis.contracts import JarvisError, ResearchTask
from jarvis.identity import canonical


def load_task(path: Path) -> ResearchTask:
    return ResearchTask.model_validate(yaml.safe_load(path.read_text()))


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("task").add_subparsers(dest="operation", required=True)
    validate.add_parser("validate").add_argument("path", type=Path)
    commands.add_parser("schema").add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "task":
            task = load_task(args.path)
            print(
                json.dumps(
                    {"status": "VALID", "task_identity": task.identity, "domain": task.domain},
                    indent=2,
                )
            )
        else:
            from jarvis import contracts

            args.output.mkdir(parents=True, exist_ok=True)
            for name in dir(contracts):
                model = getattr(contracts, name)
                if isinstance(model, type) and issubclass(model, contracts.Record):
                    (args.output / f"{name}.v1.json").write_bytes(
                        canonical(model.model_json_schema()) + b"\n"
                    )
            print("Schemas exported")
    except (JarvisError, ValidationError, OSError, ValueError) as exc:
        print(f"jarvis: {exc}", file=sys.stderr)
        sys.exit(2)
