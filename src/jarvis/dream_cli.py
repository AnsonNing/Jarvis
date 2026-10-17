"""CLI and evidence-derived reports for the synthetic Dream-RSI workflow."""

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from jarvis.contracts import JarvisError
from jarvis.dream import DreamEngine, verify_round
from jarvis.exploration import ExplorationPolicy
from jarvis.pool import PoolBuilder, verify_pool
from jarvis.researcher import AgentConfig
from jarvis.storage import Store
from jarvis.study import PolicyRegistry, Study, verify_canary


def add_commands(subcommands: Any) -> None:
    collect = subcommands.add_parser("collect")
    collect.add_argument(
        "--omit-diagnose", action="store_true", help="Explicit coverage-gap control"
    )
    for name in ("dream", "study"):
        command = subcommands.add_parser(name)
        if name == "dream":
            command.add_argument("--pool", required=True)
        command.add_argument("--developer", choices=["fixture", "codex"], default="fixture")
        command.add_argument("--codex-executable", default="codex")
        command.add_argument("--model")
    for name in ("resume", "report", "verify", "status"):
        command = subcommands.add_parser(name)
        command.add_argument("run_id")
        if name == "report":
            command.add_argument("--format", choices=["json", "markdown"], default="markdown")
    coverage = subcommands.add_parser("coverage")
    coverage.add_argument("--pool", required=True)
    replay = subcommands.add_parser("replay-tree")
    replay.add_argument("--tree", required=True)
    replay.add_argument("--policy", type=Path, required=True)
    space = subcommands.add_parser("space")
    space.add_argument("--spec", type=Path)


def completed_report(store: Store, run_id: str) -> dict[str, Any]:
    event = next(
        (
            e
            for e in reversed(store.events(run_id))
            if e["event_type"] in {"DREAM_COMPLETED", "STUDY_COMPLETED"}
        ),
        None,
    )
    if event is None:
        raise JarvisError("run is incomplete; resume it before requesting a final report")
    return store.artifacts.get(event["payload"]["report_ref"])


def markdown(store: Store, report: dict[str, Any]) -> str:
    lines = ["# Dream-RSI research report", "", f"Scope: **{report['scope']}**", ""]
    if report["kind"] == "DreamStudyReport":
        lines.extend(
            [
                f"Study: `{report['study_id']}`",
                "",
                "| Round | Research phase | Decision | Reason |",
                "| --- | --- | --- | --- |",
            ]
        )
        for row in report["rounds"]:
            lines.append(
                f"| {row['index'] + 1} | {row['stage']} | {row['status']} | {row['reason']} |"
            )
        lines.extend(["", f"Active policy: `{report['final_active_policy_id']}`", ""])
        for row in report["rounds"]:
            child = store.artifacts.get(row["report_ref"])
            tokens = child["dreaming_usage"]["actual"]["llm_tokens"]
            lines.extend(
                [
                    f"## Round {row['index'] + 1}",
                    "",
                    f"Run: `{row['round_id']}`; developer tokens: **{tokens}**.",
                    "",
                    f"Next action: {row['next_action']}",
                    "",
                ]
            )
            if child["heldout"]:
                h = child["heldout"]
                lines.append(
                    f"Policy-heldout target attainment: incumbent {h['incumbent']['successes']}/{h['incumbent']['worlds']}, finalist {h['finalist']['successes']}/{h['finalist']['worlds']}."
                )
            if child["fresh"]:
                f = child["fresh"]
                lines.extend(
                    [
                        "",
                        f"Fresh locked target attainment: incumbent {f['incumbent']['successes']}/{f['incumbent']['worlds']}, finalist {f['finalist']['successes']}/{f['finalist']['worlds']}.",
                        "",
                    ]
                )
        if report["canary_ref"]:
            canary = verify_canary(store, report["canary_ref"])
            lines.extend(
                [
                    "## Previously unseen condition-shift canary",
                    "",
                    f"Non-regression gate: **{'PASS' if canary['safe'] else 'FAIL — rolled back'}**.",
                    "",
                    f"Evidence: `{report['canary_ref']}`",
                    "",
                ]
            )
        lines.extend([report["conclusion"], ""])
    else:
        lines.extend(
            [
                f"Round: `{report['round_id']}`; decision: **{report['status']}**.",
                "",
                report["reason"],
                "",
                "| Candidate | Complete | Target attainment | Recorded tool calls |",
                "| --- | --- | --- | --- |",
            ]
        )
        for identity, value in report["development"].items():
            lines.append(
                f"| `{identity}` | {value['complete']} | {value['successes']}/{value['worlds']} | {value['tool_calls']} |"
            )
        lines.extend(
            [
                "",
                f"Measured developer usage: `{report['dreaming_usage']['actual']}`",
                "",
                f"Pending reservations: `{report['dreaming_usage']['pending']}`",
                "",
            ]
        )
    lines.extend(["## Limits", "", *["- " + limit for limit in report["limitations"]], ""])
    return "\n".join(lines)


def verify_study(store: Store, run_id: str) -> dict[str, Any]:
    report = completed_report(store, run_id)
    registered = [e["payload"] for e in store.events(run_id) if e["event_type"] == "ROUND_RECORDED"]
    if report["rounds"] != registered or len(registered) != 3:
        raise JarvisError("study round evidence mismatch")
    for row in report["rounds"]:
        checked = verify_round(store, row["round_id"])
        if checked["report_ref"] != row["report_ref"]:
            raise JarvisError("study round report mismatch")
    if report["canary_ref"]:
        verify_canary(store, report["canary_ref"])
    active, _ = PolicyRegistry(store).active(report["registry_id"])
    if active.identity != report["final_active_policy_id"]:
        raise JarvisError("study final active policy mismatch")
    return {
        "status": "VERIFIED",
        "study_id": run_id,
        "rounds": 3,
        "canary": bool(report["canary_ref"]),
        "active_policy_id": active.identity,
    }


def run(store: Store, args: argparse.Namespace) -> Any:
    op = args.operation
    if op == "collect":
        builder = PoolBuilder(store)
        cid = builder.create(omit=("diagnose",) if args.omit_diagnose else ())
        return {"collection_id": cid, "pool_ref": builder.run(cid)}
    if op in {"dream", "study"}:
        config = AgentConfig(executable=args.codex_executable, model=args.model, max_calls=1)
        if op == "study":
            engine = Study(store)
            cid = engine.create(developer=args.developer, config=config)
            print("Study created: " + cid, file=sys.stderr, flush=True)
            ref = engine.run(cid)
        else:
            dream_engine = DreamEngine(store)
            cid = dream_engine.create(args.pool, developer=args.developer, config=config)
            print("Dream round created: " + cid, file=sys.stderr, flush=True)
            ref = dream_engine.run(cid)
        return {"run_id": cid, "report_ref": ref, "report": store.artifacts.get(ref)}
    if op == "resume":
        kind = store.manifest(args.run_id)["kind"]
        if kind == "DreamStudy":
            ref = Study(store).run(args.run_id)
        elif kind == "DreamRound":
            ref = DreamEngine(store).run(args.run_id)
        elif kind == "HistoryPoolCollection":
            return {"pool_ref": PoolBuilder(store).run(args.run_id)}
        else:
            raise JarvisError("unsupported Dream run kind")
        return {"run_id": args.run_id, "report_ref": ref}
    if op == "report":
        result = completed_report(store, args.run_id)
        return markdown(store, result) if args.format == "markdown" else result
    if op == "status":
        return {
            "run_id": args.run_id,
            "kind": store.manifest(args.run_id)["kind"],
            "status": store.status(args.run_id),
            "resources": store.resource_totals(args.run_id),
            "events": len(store.events(args.run_id)),
        }
    if op == "verify":
        kind = store.manifest(args.run_id)["kind"]
        return (
            verify_study(store, args.run_id)
            if kind == "DreamStudy"
            else verify_round(store, args.run_id)
        )
    if op == "coverage":
        pool = verify_pool(store, args.pool)
        return {
            "scope": "observed-parent frontiers only",
            "trees": len(pool.members),
            "members": [
                {
                    "family": m.fixture.family,
                    "cohort": m.cohort,
                    "tree_ref": m.tree_ref,
                    "recorded_edges": len(store.artifacts.get(m.tree_ref)["edges"]),
                    "eligible_edges": store.artifacts.get(m.tree_ref)["eligible_edges"],
                }
                for m in pool.members
            ],
        }
    if op == "replay-tree":
        from jarvis.branch_replay import replay_tree

        return replay_tree(
            store,
            args.tree,
            ExplorationPolicy.model_validate(yaml.safe_load(args.policy.read_text())),
        )
    if op == "space":
        from jarvis.search_space import SearchSpace, enumerate_settings, hermes_space

        spec = (
            SearchSpace.model_validate(yaml.safe_load(args.spec.read_text()))
            if args.spec
            else hermes_space()
        )
        return {
            "scope": "DESIGN_ONLY",
            "space": spec.model_dump(mode="json"),
            "candidates": enumerate_settings(
                spec, {"frozen_threshold_comparison", "frozen_cancel_comparison"}
            ),
        }
    raise JarvisError("unknown Dream-RSI operation")
