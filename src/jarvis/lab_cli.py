"""Explicit local research-lab declarations; fixtures never start Hermes."""

import argparse
import time
from pathlib import Path
from typing import Any

import yaml

from jarvis.contracts import AdmissionError
from jarvis.lab_contracts import HumanInput, LabPolicy, LabSpec, SourceDocument
from jarvis.lab_evolution import (
    LabEvolution,
    LabEvolutionSpec,
    adopt_profile,
    evolution_report,
    replay_lab,
)
from jarvis.lab_memory import ResearchBoard
from jarvis.lab_tools import CrossrefSearch, GitHubImplementationSearch, SourceSearch, run_tool
from jarvis.research_lab import ResearchLab, lab_report
from jarvis.storage import Store


def add_commands(commands: Any) -> None:
    lab = commands.add_parser("lab").add_subparsers(dest="operation", required=True)
    lab.add_parser("fixture")
    lab.add_parser("team-fixture")
    lab.add_parser("session-fixture")
    start = lab.add_parser("start")
    start.add_argument("spec", type=Path)
    start.add_argument("--profile", action="append", default=[])
    start.add_argument("--board")
    for name in ["advance", "report", "resume-receipts", "capability-propose"]:
        lab.add_parser(name).add_argument("episode")
    run = lab.add_parser("run")
    run.add_argument("episode")
    run.add_argument("--max-steps", type=int, default=80)
    run.add_argument("--wall-seconds", type=int, default=3600)
    run.add_argument("--poll-seconds", type=int, default=30)
    replay = lab.add_parser("replay")
    replay.add_argument("episode")
    replay.add_argument("policy", type=Path)
    lab.add_parser("evolve-start").add_argument("spec", type=Path)
    for name in ["evolve-advance", "evolve-report"]:
        lab.add_parser(name).add_argument("study")
    lab.add_parser("board-create").add_argument("goal")
    add = lab.add_parser("board-add")
    add.add_argument("board")
    add.add_argument("artifact", type=Path)
    lab.add_parser("board-report").add_argument("board")
    query = lab.add_parser("source-query")
    query.add_argument(
        "public_question", help="Explicit owner-approved PUBLIC query; no private strategy/data"
    )
    query.add_argument("--provider", choices=["crossref", "github"], default="crossref")
    lab.add_parser("session-start").add_argument("spec", type=Path)
    for name in ["session-advance", "session-report", "session-quota"]:
        lab.add_parser(name).add_argument("session")
    notebook = lab.add_parser("notebook")
    notebook.add_argument("owner")
    notebook.add_argument("--output", type=Path, required=True)


def run(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    op = args.operation
    if op == "session-fixture":
        from jarvis.iris_lab_session import run_session_fixture

        return run_session_fixture(store)
    if op == "notebook":
        from jarvis.research_notebook import export_notebook

        return export_notebook(store, args.owner, args.output)
    if op.startswith("session-"):
        from jarvis.iris_lab_session import IrisLabSession, IrisLabSessionSpec

        session = IrisLabSession(store)
        if op == "session-start":
            session_spec = IrisLabSessionSpec.model_validate(yaml.safe_load(args.spec.read_text()))
            return {"session": session.create(session_spec), "status": "READY"}
        if op == "session-report":
            return session.report(args.session)
        if op == "session-quota":
            return session.refresh_quota(args.session, session.known_requests(args.session))
        return session.advance(args.session)
    if op in {"fixture", "team-fixture"}:
        from jarvis.lab_fixture import run_improvement_fixture, run_lab_fixture

        return run_improvement_fixture(store) if op == "fixture" else run_lab_fixture(store)
    board = ResearchBoard(store)
    if op == "board-create":
        return {"board": board.create(args.goal), "status": "READY"}
    if op == "board-add":
        raw = yaml.safe_load(args.artifact.read_text())
        record = (
            HumanInput.model_validate(raw)
            if raw.get("kind") == "HumanResearchInput"
            else SourceDocument.model_validate(raw)
        )
        board.add(args.board, store.artifacts.put(record))
        return board.snapshot(args.board)
    if op == "board-report":
        return board.snapshot(args.board)
    if op == "source-query":
        owner = board.create("Public source discovery: " + args.public_question)
        source: SourceSearch = (
            GitHubImplementationSearch() if args.provider == "github" else CrossrefSearch()
        )
        docs = run_tool(
            store,
            owner,
            "source-query",
            {"public_question": args.public_question, "adapter": source.identity()},
            lambda: [d.model_dump(mode="json") for d in source.search(args.public_question)],
        )
        for document in docs:
            board.add(owner, store.artifacts.put(SourceDocument.model_validate(document)))
        return {
            "board": owner,
            "documents": docs,
            "usage": store.resource_totals(owner),
            "read_scope": "metadata/abstract; no relevance or effectiveness verdict",
        }
    if op == "report":
        return lab_report(store, args.episode)
    if op == "evolve-report":
        return evolution_report(store, args.study)
    if op == "replay":
        return replay_lab(
            store, args.episode, LabPolicy.model_validate(yaml.safe_load(args.policy.read_text()))
        )
    engine = ResearchLab(store)
    if op == "start":
        spec = LabSpec.model_validate(yaml.safe_load(args.spec.read_text()))
        if args.board:
            spec = spec.model_copy(update={"board_id": args.board, "board_snapshot_ref": None})
        for profile in args.profile:
            spec = adopt_profile(store, spec, profile)
        return {"episode": engine.create(spec), "status": "READY"}
    if op == "evolve-start":
        evolution_spec = LabEvolutionSpec.model_validate(yaml.safe_load(args.spec.read_text()))
        return {"study": LabEvolution(store, engine).create(evolution_spec), "status": "READY"}
    if op == "evolve-advance":
        return LabEvolution(store, engine).advance(args.study)
    if op == "advance":
        return engine.advance(args.episode)
    if op == "resume-receipts":
        return engine.resume_receipts(args.episode)
    if op == "capability-propose":
        return {"capability_ref": engine.propose_capability(args.episode), "status": "EXPERIMENTAL"}
    if (
        op != "run"
        or not 1 <= args.max_steps <= 2000
        or (not 1 <= args.wall_seconds <= 14400)
        or (not 1 <= args.poll_seconds <= 60)
    ):
        raise AdmissionError("finite research runner bounds required")
    started = time.monotonic()
    result: dict[str, Any] = {"status": "RUNNER_LIMIT"}
    with store.campaign_lock(args.episode, scope="lab-run"):
        for _ in range(args.max_steps):
            if time.monotonic() - started >= args.wall_seconds:
                break
            result = engine.advance(args.episode)
            if result["status"] in {
                "CLOSED",
                "PAUSED",
                "CANCELLED",
                "BUDGET_EXHAUSTED",
                "LAB_NEEDS_ATTENTION",
                "NEEDS_RECONCILIATION",
            }:
                return result
            if any(
                not j["finalized"]
                for member in store.resource_members(args.episode)
                for j in store.jobs(member)
            ):
                time.sleep(
                    min(args.poll_seconds, max(0, args.wall_seconds - (time.monotonic() - started)))
                )
    return {
        "status": "RUNNER_LIMIT",
        "episode": args.episode,
        "last_progress": result,
        "resumable": True,
    }
