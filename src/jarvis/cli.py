"""Small CLI over real kernel operations. Unsupported capabilities fail explicitly."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jarvis.contracts import JarvisError, ResearchTask
from jarvis.identity import canonical


def load_task(path: Path) -> ResearchTask:
    return ResearchTask.model_validate(yaml.safe_load(path.read_text()))


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis", description="Durable local research campaigns")
    p.add_argument(
        "--store", type=Path, default=Path(".jarvis"), help="Private local evidence directory"
    )
    commands = p.add_subparsers(dest="command", required=True)
    task = commands.add_parser("task").add_subparsers(dest="operation", required=True)
    task.add_parser("validate").add_argument("path", type=Path)
    schema = commands.add_parser("schema")
    schema.add_argument("output", type=Path)
    campaign = commands.add_parser("campaign").add_subparsers(dest="operation", required=True)
    start = campaign.add_parser("start")
    start.add_argument("path", type=Path)
    start.add_argument("--policy", choices=["Q0"], default="Q0")
    start.add_argument("--max-steps", type=int)
    for op in ["status", "resume", "pause", "stop", "report"]:
        sub = campaign.add_parser(op)
        sub.add_argument("campaign_id")
        if op == "resume":
            sub.add_argument("--max-steps", type=int)
        if op == "report":
            sub.add_argument("--format", choices=["json", "markdown"], default="markdown")
    history = commands.add_parser("history").add_subparsers(dest="operation", required=True)
    history.add_parser("verify").add_argument("campaign_id")
    replay = (
        commands.add_parser("rsi")
        .add_subparsers(dest="operation", required=True)
        .add_parser("replay")
    )
    replay.add_argument("campaign_id")
    replay.add_argument("--policy", type=Path)
    hermes = commands.add_parser("hermes").add_subparsers(dest="operation", required=True)
    for op in ["inspect", "start"]:
        sub = hermes.add_parser(op)
        sub.add_argument("--root", type=Path, required=True)
        sub.add_argument("--side", choices=["long", "short"], required=True)
        sub.add_argument("--ledger-root", type=Path)
        if op == "start":
            sub.add_argument("--max-steps", type=int)
    materialize = hermes.add_parser("materialize")
    materialize.add_argument("--root", type=Path, required=True)
    materialize.add_argument("--side", choices=["long", "short"], required=True)
    materialize.add_argument(
        "--python", type=Path, required=True, help="Trusted upstream Python environment"
    )
    materialize.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New private output folder outside the source repository",
    )
    return p


def run(args: argparse.Namespace) -> None:
    if args.command == "task":
        task = load_task(args.path)
        emit({"status": "VALID", "task_identity": task.identity, "domain": task.domain})
        return
    if args.command == "schema":
        from jarvis import agents, contracts, policy

        args.output.mkdir(parents=True, exist_ok=True)
        for module in [contracts, agents, policy]:
            for name in dir(module):
                model = getattr(module, name)
                if isinstance(model, type) and issubclass(model, contracts.Record):
                    (args.output / f"{name}.v1.json").write_bytes(
                        canonical(model.model_json_schema()) + b"\n"
                    )
        emit({"status": "EXPORTED", "output": str(args.output)})
        return

    from jarvis.campaign import CampaignEngine
    from jarvis.storage import Store

    store = Store(args.store)
    engine = CampaignEngine(store)
    if args.command == "hermes":
        if args.operation == "materialize":
            from jarvis.materialize import materialize

            emit(materialize(args.root, args.side, args.python, args.output))
            return
        from jarvis.hermes import HermesAdapter

        adapter = HermesAdapter(args.root, args.side, args.ledger_root)
        if args.operation == "inspect":
            emit(adapter.inspect())
        else:
            campaign_id = engine.create(adapter.task(), adapter)
            emit(
                {
                    "campaign_id": campaign_id,
                    "status": engine.run(campaign_id, max_steps=args.max_steps),
                }
            )
        return
    if args.command == "history":
        from jarvis.history import verify

        emit(verify(store, args.campaign_id))
        return
    if args.command == "rsi":
        from jarvis.policy import FixedPolicy, PolicySpec
        from jarvis.replay import replay

        spec = (
            PolicySpec.model_validate(yaml.safe_load(args.policy.read_text()))
            if args.policy
            else PolicySpec()
        )
        emit(replay(store, args.campaign_id, FixedPolicy(spec)))
        return
    if args.operation == "start":
        task = load_task(args.path)
        if task.domain != "synthetic":
            raise JarvisError(
                "Hermes requires explicit local configuration: use hermes start --root --side"
            )
        campaign_id = engine.create(task)
        emit(
            {
                "campaign_id": campaign_id,
                "status": engine.run(campaign_id, max_steps=args.max_steps),
            }
        )
    elif args.operation == "status":
        emit(
            {
                "campaign_id": args.campaign_id,
                "status": store.status(args.campaign_id),
                "usage": store.usage(args.campaign_id),
            }
        )
    elif args.operation in {"pause", "stop"}:
        command = "PAUSED" if args.operation == "pause" else "CANCELLED"
        store.control(args.campaign_id, command)
        emit({"campaign_id": args.campaign_id, "control": command, "applies": "between actions"})
    elif args.operation == "resume":
        emit(
            {
                "campaign_id": args.campaign_id,
                "status": engine.run(args.campaign_id, max_steps=args.max_steps),
            }
        )
    elif args.operation == "report":
        from jarvis.reporting import render_markdown, report

        value = report(store, args.campaign_id)
        if args.format == "json":
            emit(value)
        else:
            print(render_markdown(value))


def main() -> None:
    args = parser().parse_args()
    try:
        run(args)
    except (JarvisError, ValidationError, OSError, ValueError) as exc:
        print(f"jarvis: {exc}", file=sys.stderr)
        sys.exit(2)
