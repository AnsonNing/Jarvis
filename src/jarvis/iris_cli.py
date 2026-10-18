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
    use = iris.add_parser("development-use")
    use.add_argument("--upstream", type=Path, required=True)
    use.add_argument("--owner-instruction", required=True)
    use.add_argument("--output", type=Path, required=True)
    ready = iris.add_parser("readiness")
    ready.add_argument("--upstream", type=Path, required=True)
    ready.add_argument("--python", type=Path, required=True)
    ready.add_argument("--development-use", type=Path)
    ready.add_argument("--design-root", type=Path)
    ready.add_argument("--state", type=Path, action="append", default=[])
    ready.add_argument("--output", type=Path)
    continuous = iris.add_parser("continuous-prepare")
    continuous.add_argument("--upstream", type=Path, required=True)
    continuous.add_argument("--python", type=Path, required=True)
    continuous.add_argument("--owner-instruction", required=True)
    continuous.add_argument("--output", type=Path, required=True)
    continuous.add_argument("--design-root", type=Path)
    continuous.add_argument("--development-use", type=Path)
    iris.add_parser("continuous-start").add_argument("spec", type=Path)
    for operation in ["continuous-advance", "continuous-report", "continuous-quota"]:
        iris.add_parser(operation).add_argument("study_id")
    continuous = iris.add_parser("continuous-run")
    continuous.add_argument("study_id")
    continuous.add_argument("--wall-seconds", type=int, default=1800)
    continuous.add_argument("--poll-seconds", type=int, default=45)
    continuous.add_argument("--max-steps", type=int, default=200)
    watcher = iris.add_parser("continuous-watch")
    watcher.add_argument("--upstream", type=Path, required=True)
    watcher.add_argument("--python", type=Path, required=True)
    watcher.add_argument("--owner-instruction", required=True)
    watcher.add_argument("--state", type=Path, required=True)
    watcher.add_argument("--wall-seconds", type=int, default=7200)
    watcher.add_argument("--poll-seconds", type=int, default=45)
    watcher.add_argument("--max-steps", type=int, default=2000)
    watcher.add_argument("--design-root", type=Path)
    watcher.add_argument("--predecessor-state", type=Path)
    iris.add_parser("model-status").add_argument("--upstream", type=Path, required=True)
    prepare = iris.add_parser("prepare")
    prepare.add_argument("--upstream", type=Path, required=True)
    prepare.add_argument("--side", choices=["long", "short"], required=True)
    prepare.add_argument("--month", choices=["08", "09"], default="08")
    prepare.add_argument("--window", choices=["full", "smoke"], default="full")
    prepare.add_argument("--python", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--development-use", type=Path)
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
    design_fixture = research.add_parser("design-fixture")
    design_fixture.add_argument("--agent", choices=["fixture", "codex"], default="fixture")
    design_fixture.add_argument("--executable", default="codex")
    critic_fixture = research.add_parser("critic-fixture")
    critic_fixture.add_argument("--agent", choices=["fixture", "codex"], default="fixture")
    critic_fixture.add_argument("--executable", default="codex")
    research.add_parser("dream-loop-fixture")
    research.add_parser("dream-loop-start").add_argument("spec", type=Path)
    for operation in ["dream-loop-advance", "dream-loop-report"]:
        research.add_parser(operation).add_argument("study_id")
    loop_run = research.add_parser("dream-loop-run")
    loop_run.add_argument("study_id")
    loop_run.add_argument("--max-steps", type=int, default=2000)
    loop_run.add_argument("--wall-seconds", type=int, default=14400)
    loop_run.add_argument("--poll-seconds", type=int, default=45)
    research.add_parser("start").add_argument("spec", type=Path)
    prepare = research.add_parser("prepare")
    prepare.add_argument("--environment", type=Path, required=True)
    prepare.add_argument("--baseline", type=Path, required=True)
    prepare.add_argument("--direction", action="append", required=True)
    prepare.add_argument("--goal", default="最大化 entry-only PL，完整保留風險、負面結果與資料限制")
    prepare.add_argument("--batches", type=int, default=3)
    prepare.add_argument("--experiments-per-batch", type=int, default=3)
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


def preparation(args: argparse.Namespace) -> dict[str, Any]:
    """Runs before Store/provider/queue construction; no job or remote operation."""
    from jarvis.iris_admission import development_use, model_status
    from jarvis.iris_readiness import research_readiness

    if args.operation == "model-status":
        return model_status(args.upstream)
    if args.operation == "development-use":
        value = development_use(args.upstream, args.owner_instruction).model_dump(mode="json")
    else:
        value = research_readiness(
            args.upstream,
            args.python,
            design_root=args.design_root,
            development_use_path=args.development_use,
            states=tuple(args.state),
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with args.output.open("xb") as stream:
            stream.write(canonical(value) + b"\n")
    return value


def run(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "research" and args.operation == "dream-loop-fixture":
        from jarvis.reviewed_dream_fixture import run_reviewed_dream_fixture

        return run_reviewed_dream_fixture(store)
    if args.command == "research" and args.operation.startswith("dream-loop-"):
        from jarvis.reviewed_dream import (
            ReviewedDreamLoop,
            ReviewedDreamSpec,
            reviewed_dream_report,
        )

        loop_engine = ReviewedDreamLoop(store)
        if args.operation == "dream-loop-start":
            return {
                "study_id": loop_engine.create(ReviewedDreamSpec.model_validate(read(args.spec))),
                "status": "READY",
            }
        if args.operation == "dream-loop-advance":
            return loop_engine.advance(args.study_id)
        if args.operation == "dream-loop-run":
            return loop_engine.run(
                args.study_id,
                max_steps=args.max_steps,
                wall_seconds=args.wall_seconds,
                poll_seconds=args.poll_seconds,
            )
        return reviewed_dream_report(store, args.study_id)
    if args.command == "research" and args.operation == "critic-fixture":
        from jarvis.critic_fixture import run_critic_fixture

        return run_critic_fixture(store, agent=args.agent, executable=args.executable)
    if args.command == "research" and args.operation == "design-fixture":
        from jarvis.iris_design_fixture import run_design_fixture

        return run_design_fixture(store, agent=args.agent, executable=args.executable)
    if args.command in {"research", "hermes-dream"} and args.operation == "fixture":
        from jarvis.research_fixture import run_fixture

        return run_fixture(
            store,
            "research" if args.command == "research" else "dream",
            agent=args.agent,
            executable=args.executable,
        )
    if args.command == "iris":
        if args.operation.startswith("continuous-"):
            from jarvis.iris_continuous import (
                IrisContinuousSpec,
                IrisContinuousStudy,
                continuous_report,
                prepare_continuous,
            )

            continuous_engine = IrisContinuousStudy(store)
            if args.operation == "continuous-watch":
                from jarvis.iris_watch import watch_research

                return watch_research(
                    store,
                    args.upstream,
                    args.python,
                    args.owner_instruction,
                    args.state,
                    wall_seconds=args.wall_seconds,
                    poll_seconds=args.poll_seconds,
                    max_steps=args.max_steps,
                    design_root=args.design_root,
                    predecessor_state=args.predecessor_state,
                )
            if args.operation == "continuous-prepare":
                if args.output.exists():
                    raise JarvisError("choose a new private continuous study specification")
                continuous_spec = prepare_continuous(
                    args.upstream,
                    args.python,
                    args.owner_instruction,
                    design_root=args.design_root,
                    development_use_path=args.development_use,
                )
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_bytes(canonical(continuous_spec) + b"\n")
                return {"status": "PREPARED_NO_SUBMISSION", "spec": str(args.output)}
            if args.operation == "continuous-start":
                return {
                    "study_id": continuous_engine.create(
                        IrisContinuousSpec.model_validate(read(args.spec))
                    ),
                    "status": "READY",
                }
            if args.operation == "continuous-quota":
                return continuous_engine.refresh_quota(args.study_id)
            if args.operation == "continuous-advance":
                return continuous_engine.advance(args.study_id)
            if args.operation == "continuous-run":
                return continuous_engine.run(
                    args.study_id,
                    wall_seconds=args.wall_seconds,
                    poll_seconds=args.poll_seconds,
                    max_steps=args.max_steps,
                )
            return continuous_report(store, args.study_id)
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
                args.upstream,
                args.side,
                args.month,
                args.python,
                window=args.window,
                development_use_path=args.development_use,
            )
            baseline = prepare_baseline(
                args.upstream,
                args.side,
                args.month,
                window=args.window,
                development_use_path=args.development_use,
            )
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
                experiments_per_batch=args.experiments_per_batch,
                agent=AgentConfig(
                    executable=args.executable,
                    max_calls=args.batches,
                    reserve_tokens=65536,
                    timeout_seconds=180,
                ),
                critic=AgentConfig(
                    executable=args.executable,
                    max_calls=2 * args.batches,
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
    from jarvis.hermes_dream import (
        HermesDream,
        HermesDreamSpec,
        hermes_dream_report,
        verify_hermes_dream,
    )

    engine = HermesDream(store)
    if args.operation == "start":
        return {
            "study_id": engine.create(HermesDreamSpec.model_validate(read(args.spec))),
            "status": "READY",
        }
    if args.operation == "advance":
        return engine.advance(args.study_id)
    return (
        verify_hermes_dream(store, args.study_id)
        if args.operation == "verify"
        else hermes_dream_report(store, args.study_id)
    )
