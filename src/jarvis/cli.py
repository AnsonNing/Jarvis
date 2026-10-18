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


def agent_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent", choices=["q0", "codex"], default="q0")
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument(
        "--model", help="Optional explicit provider model; default uses installed CLI default"
    )
    parser.add_argument("--llm-token-budget", type=int, default=200000)
    parser.add_argument("--llm-timeout", type=int, default=120)
    parser.add_argument("--max-llm-calls", type=int, default=8)
    parser.add_argument("--goal", help="Research objective frozen at campaign creation")


def agent_configuration(task: ResearchTask, args: argparse.Namespace) -> tuple[ResearchTask, Any]:
    from jarvis.researcher import AgentConfig

    if args.goal:
        task = task.model_copy(update={"goal": args.goal})
    if args.agent == "q0":
        return ResearchTask.model_validate(task.model_dump()), None
    config = AgentConfig(
        executable=args.codex_executable,
        model=args.model,
        timeout_seconds=args.llm_timeout,
        max_calls=args.max_llm_calls,
    )
    budget = task.budget.model_copy(
        update={
            "max_llm_tokens": args.llm_token_budget,
            "max_wall_seconds": max(1800.0, task.budget.max_wall_seconds),
        }
    )
    # Validate command-line budget changes, including negative/boolean inputs.
    budget = type(task.budget).model_validate(budget.model_dump())
    hypotheses = tuple(dict.fromkeys((*task.hypotheses_allowed, "parameter_change")))
    return ResearchTask.model_validate(
        task.model_copy(update={"budget": budget, "hypotheses_allowed": hypotheses}).model_dump()
    ), config


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis", description="Durable local research campaigns")
    p.add_argument(
        "--store", type=Path, default=Path(".jarvis"), help="Private local evidence directory"
    )
    commands = p.add_subparsers(dest="command", required=True)
    from jarvis.iris_cli import add_commands as add_iris_commands

    add_iris_commands(commands)
    task = commands.add_parser("task").add_subparsers(dest="operation", required=True)
    task.add_parser("validate").add_argument("path", type=Path)
    schema = commands.add_parser("schema")
    schema.add_argument("output", type=Path)
    campaign = commands.add_parser("campaign").add_subparsers(dest="operation", required=True)
    start = campaign.add_parser("start")
    start.add_argument("path", type=Path)
    start.add_argument("--policy", choices=["Q0"], default="Q0")
    start.add_argument("--max-steps", type=int)
    agent_options(start)
    for op in ["status", "resume", "pause", "stop", "report"]:
        sub = campaign.add_parser(op)
        sub.add_argument("campaign_id")
        if op == "resume":
            sub.add_argument("--max-steps", type=int)
        if op == "report":
            sub.add_argument("--format", choices=["json", "markdown"], default="markdown")
    history = commands.add_parser("history").add_subparsers(dest="operation", required=True)
    history.add_parser("verify").add_argument("campaign_id")
    rsi = commands.add_parser("rsi").add_subparsers(dest="operation", required=True)
    replay = rsi.add_parser("replay")
    from jarvis.dream_cli import add_commands

    add_commands(rsi)
    replay.add_argument("campaign_id")
    replay.add_argument("--policy", type=Path)
    hermes = commands.add_parser("hermes").add_subparsers(dest="operation", required=True)
    for op in ["inspect", "start"]:
        sub = hermes.add_parser(op)
        sub.add_argument("--root", type=Path, required=True)
        sub.add_argument("--side", choices=["long", "short"], required=True)
        sub.add_argument("--ledger-root", type=Path)
        sub.add_argument("--format", choices=["legacy", "new"], default="legacy")
        sub.add_argument(
            "--artifacts", type=Path, help="Explicit per-side new-baseline artifact directory"
        )
        if op == "start":
            sub.add_argument("--max-steps", type=int)
            agent_options(sub)
    materialize = hermes.add_parser("materialize")
    materialize.add_argument("--root", type=Path, required=True)
    materialize.add_argument("--side", choices=["long", "short"], required=True)
    materialize.add_argument("--format", choices=["legacy", "new"], default="legacy")
    materialize.add_argument("--artifacts", type=Path)
    materialize.add_argument(
        "--python", type=Path, required=True, help="Trusted upstream Python environment"
    )
    materialize.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New private output folder outside the source repository",
    )
    study = hermes.add_parser("study")
    for name in ("root", "artifacts", "ledger-root"):
        study.add_argument("--" + name, type=Path, required=True)
    study.add_argument("--side", choices=["long", "short"], required=True)
    study.add_argument("--rounds", type=int, default=2)
    agent_options(study)
    for op in ("study-resume", "study-verify", "study-report", "study-upgrade"):
        sub = hermes.add_parser(op)
        sub.add_argument("study_id")
    exit_parser = hermes.add_parser("exit-replay")
    exit_parser.add_argument("--input", type=Path, required=True)
    exit_parser.add_argument("--settings", type=Path, required=True)
    for op in ("exit-resume", "exit-verify"):
        hermes.add_parser(op).add_argument("job_id")
    return p


def run(args: argparse.Namespace) -> None:
    if args.command == "task":
        task = load_task(args.path)
        emit({"status": "VALID", "task_identity": task.identity, "domain": task.domain})
        return
    if args.command == "schema":
        from jarvis import (
            agents,
            contracts,
            discovery,
            dream,
            exit_replay,
            exploration,
            hermes_dream,
            hermes_policy,
            hermes_study,
            iris_continuous,
            iris_contracts,
            iris_design,
            iris_quota,
            new_research,
            policy,
            pool,
            research_program,
            research_roles,
            researcher,
            search_space,
            suite,
        )

        args.output.mkdir(parents=True, exist_ok=True)
        for module in [
            contracts,
            agents,
            policy,
            researcher,
            new_research,
            discovery,
            dream,
            exploration,
            exit_replay,
            hermes_study,
            hermes_dream,
            hermes_policy,
            iris_contracts,
            iris_design,
            iris_continuous,
            iris_quota,
            research_program,
            research_roles,
            pool,
            search_space,
            suite,
        ]:
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
    if args.command in {"iris", "research", "hermes-dream"}:
        from jarvis.iris_cli import run as iris_run

        emit(iris_run(store, args))
        return
    if args.command == "hermes":
        if args.operation.startswith("study"):
            from jarvis.hermes_study import HermesStudy, HermesStudySpec, verify_hermes_study
            from jarvis.researcher import AgentConfig

            study = HermesStudy(store)
            if args.operation == "study":
                if args.agent != "codex":
                    raise JarvisError("real Hermes study requires explicit --agent codex")
                sid = study.create(
                    HermesStudySpec(
                        root=str(args.root.resolve()),
                        side=args.side,
                        artifacts=str(args.artifacts.resolve()),
                        ledger_root=str(args.ledger_root.resolve()),
                        rounds=args.rounds,
                        objective=args.goal,
                        tokens_per_round=args.llm_token_budget,
                        agent=AgentConfig(
                            executable=args.codex_executable,
                            model=args.model,
                            timeout_seconds=args.llm_timeout,
                            max_calls=args.max_llm_calls,
                        ),
                    )
                )
                print(f"Hermes study created: {sid}", file=sys.stderr, flush=True)
                ref = study.run(sid)
                emit({**store.artifacts.get(ref), "report_ref": ref})
            elif args.operation == "study-resume":
                ref = study.run(args.study_id)
                emit({**store.artifacts.get(ref), "report_ref": ref})
            elif args.operation == "study-verify":
                emit(verify_hermes_study(store, args.study_id))
            elif args.operation == "study-upgrade":
                emit(study.admit_runtime_upgrade(args.study_id))
            else:
                checked = verify_hermes_study(store, args.study_id)
                emit(
                    {
                        **store.artifacts.get(checked["report_ref"]),
                        "report_ref": checked["report_ref"],
                    }
                )
            return
        if args.operation.startswith("exit-"):
            from jarvis.exit_replay import create_exit_job, run_exit_job, verify_exit_job

            jid = (
                create_exit_job(store, args.input, args.settings)
                if args.operation == "exit-replay"
                else args.job_id
            )
            if args.operation == "exit-verify":
                emit(verify_exit_job(store, jid))
            else:
                print(f"Exit replay created/resumed: {jid}", file=sys.stderr, flush=True)
                ref = run_exit_job(store, jid)
                emit(
                    {
                        "job_id": jid,
                        "result": store.artifacts.get(ref),
                        "verification": verify_exit_job(store, jid),
                    }
                )
            return
        if args.operation == "materialize":
            if args.format == "new":
                from jarvis.hermes_new import materialize_new

                if args.artifacts is None:
                    raise JarvisError("new-baseline materialization requires --artifacts")
                emit(
                    materialize_new(args.root, args.side, args.artifacts, args.python, args.output)
                )
                return
            from jarvis.materialize import materialize

            emit(materialize(args.root, args.side, args.python, args.output))
            return
        from jarvis.hermes import HermesAdapter

        if args.format == "new":
            from jarvis.hermes_new import NewBaselineAdapter

            if args.artifacts is None:
                raise JarvisError("new-baseline admission requires --artifacts")
            adapter: Any = NewBaselineAdapter(
                args.root, args.side, args.artifacts, args.ledger_root
            )
        else:
            adapter = HermesAdapter(args.root, args.side, args.ledger_root)
        if args.operation == "inspect":
            emit(adapter.inspect())
        else:
            task, config = agent_configuration(adapter.task(), args)
            campaign_id = engine.create(task, adapter, researcher=config)
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
        if args.operation != "replay":
            from jarvis.dream_cli import run as dream_run

            result = dream_run(store, args)
            if isinstance(result, str):
                print(result)
            else:
                emit(result)
            return
        from jarvis.policy import FixedPolicy, PolicySpec
        from jarvis.replay import replay

        spec = (
            PolicySpec.model_validate(yaml.safe_load(args.policy.read_text()))
            if args.policy
            else None
        )
        emit(replay(store, args.campaign_id, FixedPolicy(spec) if spec else None))
        return
    if args.operation == "start":
        task = load_task(args.path)
        if task.domain != "synthetic":
            raise JarvisError(
                "Hermes requires explicit local configuration: use hermes start --root --side"
            )
        task, config = agent_configuration(task, args)
        campaign_id = engine.create(task, researcher=config)
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
