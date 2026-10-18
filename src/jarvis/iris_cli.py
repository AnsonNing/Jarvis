"""Explicit CLI for strategy research, local preparation and remote queues."""

import argparse
from pathlib import Path
from typing import Any

import yaml

from jarvis.contracts import JarvisError
from jarvis.identity import canonical
from jarvis.storage import Store


def add_commands(commands: Any) -> None:
    iris = commands.add_parser("iris").add_subparsers(dest="operation", required=True)
    iris.add_parser("model-status").add_argument("--upstream", type=Path, required=True)
    prepare = iris.add_parser("prepare")
    prepare.add_argument("--upstream", type=Path, required=True)
    prepare.add_argument("--side", choices=["long", "short"], required=True)
    prepare.add_argument("--month", choices=["08", "09"], default="08")
    prepare.add_argument("--window", choices=["full", "smoke"], default="full")
    prepare.add_argument("--python", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    iris.add_parser("queue-start").add_argument("spec", type=Path)
    enqueue = iris.add_parser("enqueue")
    enqueue.add_argument("queue_id")
    enqueue.add_argument("experiment", type=Path)
    authorize = iris.add_parser("authorize")
    authorize.add_argument("queue_id")
    authorize.add_argument("authorization", type=Path)
    for name in ["tick", "queue-report", "queue-verify", "pause", "unpause", "world-seal"]:
        sub = iris.add_parser(name)
        sub.add_argument("queue_id")
        if name == "world-seal":
            sub.add_argument("--family", required=True)
    research = commands.add_parser("research").add_subparsers(dest="operation", required=True)
    fixture = research.add_parser("fixture")
    fixture.add_argument("--agent", choices=["fixture", "codex"], default="fixture")
    fixture.add_argument("--executable", default="codex")
    research.add_parser("start").add_argument("spec", type=Path)
    prepare = research.add_parser("prepare")
    prepare.add_argument("--environment", type=Path, required=True)
    prepare.add_argument("--baseline", type=Path, required=True)
    prepare.add_argument("--direction", action="append", required=True)
    prepare.add_argument("--goal", default="最大化 entry-only PL，完整保留風險、負面結果與資料限制")
    prepare.add_argument("--batches", type=int, default=3)
    prepare.add_argument("--executable", default="codex")
    prepare.add_argument("--output", type=Path, required=True)
    for name in ["advance", "report"]:
        research.add_parser(name).add_argument("program_id")
    runner = research.add_parser("run")
    runner.add_argument("program_id")
    runner.add_argument("--max-steps", type=int, default=100)
    runner.add_argument("--wall-seconds", type=int, default=1800)
    runner.add_argument("--poll-seconds", type=int, default=45)
    hd = commands.add_parser("hermes-dream").add_subparsers(dest="operation", required=True)
    fixture = hd.add_parser("fixture")
    fixture.add_argument("--agent", choices=["fixture", "codex"], default="fixture")
    fixture.add_argument("--executable", default="codex")
    hd.add_parser("start").add_argument("spec", type=Path)
    for name in ["advance", "verify", "report"]:
        hd.add_parser(name).add_argument("study_id")


def read(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def run(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    if args.command in {"research", "hermes-dream"} and args.operation == "fixture":
        from jarvis.research_fixture import run_fixture

        return run_fixture(
            store,
            "research" if args.command == "research" else "dream",
            agent=args.agent,
            executable=args.executable,
        )
    if args.command == "iris":
        from jarvis.iris_admission import model_status, prepare_baseline, prepare_environment
        from jarvis.iris_contracts import IrisAuthorization, IrisExperiment, IrisQueueSpec
        from jarvis.research_queue import IrisResearchQueue, queue_tasks, verify_queue

        queue = IrisResearchQueue(store)
        if args.operation == "model-status":
            return model_status(args.upstream)
        if args.operation == "prepare":
            if args.output.exists():
                raise JarvisError("choose a new private environment output")
            env = prepare_environment(
                args.upstream, args.side, args.month, args.python, window=args.window
            )
            baseline = prepare_baseline(args.upstream, args.side, args.month, window=args.window)
            baseline_file = args.output.with_name(args.output.stem + "-baseline.json")
            if baseline_file.exists():
                raise JarvisError("choose a new baseline proof output")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(canonical(env) + b"\n")
            baseline_file.write_bytes(canonical(baseline) + b"\n")
            return {
                "status": "PREPARED_NO_SUBMISSION",
                "environment": str(args.output),
                "baseline_proof": str(baseline_file),
                "available_settings": [b.setting for b in env.bindings],
            }
        if args.operation == "queue-start":
            return {
                "queue_id": queue.create(IrisQueueSpec.model_validate(read(args.spec))),
                "status": "READY",
            }
        if args.operation == "enqueue":
            return {
                "task_id": queue.enqueue(
                    args.queue_id, IrisExperiment.model_validate(read(args.experiment))
                )
            }
        if args.operation == "authorize":
            queue.authorize(
                args.queue_id, IrisAuthorization.model_validate(read(args.authorization))
            )
            return {"status": "AUTHORIZED_FINITE_ENVELOPE", "queue_id": args.queue_id}
        if args.operation == "tick":
            return queue.tick(args.queue_id)
        if args.operation == "pause":
            store.control(args.queue_id, "PAUSED")
            return {
                "status": "PAUSED",
                "note": "Existing remote work is collected; no new dispatch",
            }
        if args.operation == "unpause":
            store.control(args.queue_id, "RUN")
            store.set_status(args.queue_id, "READY")
            return {"status": "READY"}
        if args.operation == "world-seal":
            from jarvis.hermes_policy import seal_world

            return {"world_ref": seal_world(store, args.queue_id, args.family)}
        verification = verify_queue(store, args.queue_id)
        return (
            verification
            if args.operation == "queue-verify"
            else {
                "verification": verification,
                "tasks": queue_tasks(store, args.queue_id),
                "manifest": store.manifest(args.queue_id),
            }
        )
    if args.command == "research":
        from jarvis.research_program import ResearchProgram, ResearchProgramSpec, program_report

        program = ResearchProgram(store)
        if args.operation == "prepare":
            from jarvis.iris_contracts import IrisBaselineProof, IrisEnvironment, IrisQueueSpec
            from jarvis.researcher import AgentConfig

            if args.output.exists():
                raise JarvisError("choose a new private program specification")
            spec = ResearchProgramSpec(
                queue=IrisQueueSpec(
                    goal=args.goal,
                    environment=IrisEnvironment.model_validate(read(args.environment)),
                    baseline=IrisBaselineProof.model_validate(read(args.baseline)),
                ),
                directions=tuple(args.direction),
                max_direction_batches=args.batches,
                agent=AgentConfig(
                    executable=args.executable,
                    max_calls=args.batches,
                    reserve_tokens=65536,
                    timeout_seconds=180,
                ),
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(canonical(spec) + b"\n")
            return {
                "status": "PREPARED_NO_SUBMISSION",
                "spec": str(args.output),
                "primary_metric": spec.queue.primary_metric,
            }
        if args.operation == "start":
            return {
                "program_id": program.create(ResearchProgramSpec.model_validate(read(args.spec))),
                "status": "READY",
            }
        if args.operation == "advance":
            return program.advance(args.program_id)
        if args.operation == "run":
            from jarvis.research_runner import run_program

            return run_program(
                store,
                program,
                args.program_id,
                max_steps=args.max_steps,
                wall_seconds=args.wall_seconds,
                poll_seconds=args.poll_seconds,
            )
        return program_report(store, args.program_id)
    from jarvis.hermes_dream import HermesDream, HermesDreamSpec, verify_hermes_dream

    engine = HermesDream(store)
    if args.operation == "start":
        return {
            "study_id": engine.create(HermesDreamSpec.model_validate(read(args.spec))),
            "status": "READY",
        }
    if args.operation == "advance":
        return engine.advance(args.study_id)
    result = verify_hermes_dream(store, args.study_id)
    return result if args.operation == "verify" else dict(store.artifacts.get(result["report_ref"]))
