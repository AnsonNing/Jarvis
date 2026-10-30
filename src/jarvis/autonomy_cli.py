"""`jarvis autonomy ...`: declare, run, steer and audit autonomous research sessions."""

import argparse
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml

from jarvis.autonomy_contracts import AutonomySessionSpec
from jarvis.contracts import AdmissionError, Budget, JarvisError
from jarvis.researcher import AgentConfig
from jarvis.storage import Store


def provider_factory(agent: str, *, eager_stop: bool = False, relay: Path | None = None) -> Any:
    """codex / claude (headless CLIs), relay (answered through files), fixture (scripted)."""
    if agent == "runtime-fixture":
        from jarvis.runtime_fixture import RuntimeFixtureProvider

        return lambda config, role: RuntimeFixtureProvider(role)
    if agent == "planning-fixture":
        from jarvis.planning_fixture import PlanningFixtureProvider

        return lambda config, role: PlanningFixtureProvider(role)
    if agent == "codex":
        from jarvis.providers import CodexProvider

        return lambda config, role: CodexProvider(config)
    if agent == "claude":
        from jarvis.providers import ClaudeCodeProvider

        return lambda config, role: ClaudeCodeProvider(config)
    if agent == "relay":
        from jarvis.providers import RelayProvider

        if relay is None:
            raise AdmissionError("relay agent needs a relay directory")
        return lambda config, role: RelayProvider(config, relay)
    from jarvis.quant_fixture import QuantFixtureProvider

    return lambda config, role: QuantFixtureProvider(role, eager_stop=eager_stop)


def load_adapter(declaration: dict[str, Any]) -> Any:
    import sys

    if declaration.get("adapter_path"):
        path = str(Path(declaration["adapter_path"]).resolve())
        if path not in sys.path:
            sys.path.insert(0, path)
    module, _, name = str(declaration["adapter"]).partition(":")
    if not module or not name:
        raise AdmissionError("adapter must be module:function")
    return getattr(importlib.import_module(module), name)


def add_commands(commands: Any) -> None:
    auto = commands.add_parser("autonomy").add_subparsers(dest="operation", required=True)
    auto.add_parser("sandbox-check")
    fixture = auto.add_parser("fixture")
    fixture.add_argument("--policy", choices=["q0", "explain"], default="explain")
    fixture.add_argument(
        "--agent", choices=["fixture", "codex", "claude", "relay"], default="fixture"
    )
    fixture.add_argument("--guidance")
    fixture.add_argument("--eager-stop", action="store_true")
    fixture.add_argument("--max-decisions", type=int, default=30)
    fixture.add_argument("--effort", choices=["low", "medium", "high", "xhigh"], default="high")
    fixture.add_argument("--output", type=Path)
    dream = auto.add_parser("dream-fixture")
    dream.add_argument(
        "--agent", choices=["fixture", "codex", "claude", "relay"], default="fixture"
    )
    dream.add_argument("--max-decisions", type=int, default=30)
    auto.add_parser("start").add_argument("declaration", type=Path)
    run = auto.add_parser("run")
    run.add_argument("owner")
    run.add_argument("--max-steps", type=int, default=100000)
    run.add_argument("--wall-seconds", type=float, default=86400)
    advise = auto.add_parser("advise")
    advise.add_argument("owner")
    advise.add_argument("text")
    advise.add_argument("--redirect", action="store_true")
    for name in ("status", "report", "replay", "pause", "resume", "cancel"):
        auto.add_parser(name).add_argument("owner")
    deliver = auto.add_parser("deliverable")
    deliver.add_argument("owner")
    deliver.add_argument("directory", type=Path)
    auto.add_parser("policy-registry")
    auto.add_parser("brief").add_argument("--domain")
    dream_cmd = auto.add_parser("dream").add_subparsers(dest="dream_op", required=True)
    dream_cmd.add_parser("start").add_argument("declaration", type=Path)
    for name in ("run", "report"):
        dream_cmd.add_parser(name).add_argument("round")
    memory = auto.add_parser("memory").add_subparsers(dest="memory_op", required=True)
    show = memory.add_parser("show")
    show.add_argument("domain")
    consolidate = memory.add_parser("consolidate")
    consolidate.add_argument(
        "--agent", choices=["fixture", "codex", "claude", "relay", "none"], default="codex"
    )
    consolidate.add_argument("--session")
    export = memory.add_parser("export")
    export.add_argument("domain")
    export.add_argument("directory", type=Path)
    memory.add_parser("import").add_argument("directory", type=Path)
    note = memory.add_parser("note")
    note.add_argument("domain")
    note.add_argument("text")


def sandbox_check() -> dict[str, Any]:
    from jarvis.quant_world import analysis_environment
    from jarvis.workbench import Workbench

    with TemporaryDirectory(prefix="jarvis-workbench-check-") as tmp:
        result = Workbench(analysis_environment()).probe(Path(tmp) / "probe")
    return {
        "status": result["status"],
        "flags": result.get("flags"),
        "workbench": result["workbench"],
        "external_calls": 0,
    }


def _declared(store: Store, owner: str) -> dict[str, Any]:
    events = [
        e["payload"] for e in store.events(owner) if e["event_type"] == "DECLARATION_RECORDED"
    ]
    if not events:
        raise AdmissionError("session has no recorded declaration")
    return dict(store.artifacts.get(events[0]["declaration_ref"]))


def engine_for(store: Store, declaration: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    from jarvis.autonomous_research import AutonomousResearch

    built = load_adapter(declaration)(store, declaration.get("adapter_args", {}))
    if any(k in declaration for k in ("analysis_delivery", "research_stages", "research_planning")):
        from jarvis.autonomy_contracts import (
            AnalysisDelivery,
            AutonomyContract,
            ResearchPlanning,
            ResearchStages,
        )

        contract = store.artifacts.get(built["contract_ref"])
        if "analysis_delivery" in declaration:
            contract["analysis_delivery"] = AnalysisDelivery.model_validate(
                declaration["analysis_delivery"]
            ).model_dump(mode="json")
        if "research_stages" in declaration:
            contract["stages"] = ResearchStages.model_validate(
                declaration["research_stages"]
            ).model_dump(mode="json")
        if "research_planning" in declaration:
            contract["planning"] = ResearchPlanning.model_validate(
                declaration["research_planning"]
            ).model_dump(mode="json")
        built["contract_ref"] = store.artifacts.put(AutonomyContract.model_validate(contract))
    agent = declaration.get("agent", "codex")
    factory = provider_factory(agent, relay=store.root / "relay")
    remember = bool(declaration.get("memory", True))
    return AutonomousResearch(store, built["executors"], factory, consolidate=remember), built


def memory_and_policy(
    store: Store, contract_ref: str, *, memory: bool = True
) -> tuple[str | None, str]:
    """Every new session starts from the domain's memory and its currently adopted policy."""
    from jarvis.autonomy_dream import PolicyRegistry
    from jarvis.research_memory import KnowledgeBase

    domain = store.artifacts.get(contract_ref)["domain"]
    kb = KnowledgeBase(store)
    if memory:
        remember_ended_sessions(store, kb, domain)
    policy = PolicyRegistry(store).active(kb.registry(domain))
    return (kb.pin(domain) if memory else None), policy


def remember_ended_sessions(store: Store, kb: Any, domain: str) -> list[str]:
    """Record the trusted facts of every ended or paused session of the domain not yet in memory,
    so a successor sees its predecessors' collections, models and results without a manual
    consolidation. Lessons and skills still need `autonomy memory consolidate` with an agent."""
    from jarvis.research_memory import session_domain

    with store.connect() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM campaigns")]
    added = []
    for cid in ids:
        m = store.manifest(cid)
        if m.get("kind") != "AutonomousResearchSession" or m.get("budget_owner"):
            continue
        try:
            if session_domain(store, cid) != domain:
                continue
            before = kb.consolidated(kb.ensure(domain)).get(cid)
            if not before:
                kb.consolidate(cid)
                added.append(cid)
        except JarvisError:
            continue  # still running, or not a consolidatable session
    return added


def status(store: Store, owner: str) -> dict[str, Any]:
    """Everything an operator needs to answer "how is it going": progress, latest findings,
    best result so far, Critic concerns, owner guidance and answers, budget, last issue."""
    from jarvis.autonomous_research import AutonomousResearch, contract_of
    from jarvis.autonomy_report import session_outcome

    engine = AutonomousResearch.__new__(AutonomousResearch)
    engine.store = store
    state = engine.state(owner)
    spec = engine.spec(owner)
    contract = contract_of(store, spec)
    recent = []
    for p in state["decisions"][-8:]:
        decision = store.artifacts.get(p["decision_ref"])
        action = decision["action"]
        done = state["completed"].get(p["index"])
        result = store.artifacts.get(done["result_ref"]) if done else {}
        review_entry = state["reviews"].get(p["index"])
        review = store.artifacts.get(review_entry["review_ref"]) if review_entry else None
        recent.append(
            {
                "index": p["index"],
                "kind": action["kind"],
                "branch": action["branch_id"],
                "why": str(action.get("rationale", ""))[:240],
                "status": result.get("status") if done else "RUNNING",
                "summary": str(result.get("summary") or "")[:400] or None,
                "critic": {
                    "recommendation": review["recommendation"],
                    "concerns": [str(c)[:240] for c in review["concerns"][:3]],
                }
                if review
                else None,
            }
        )
    try:
        outcome = session_outcome(store, owner)
    except Exception as exc:  # status must never fail because a comparison cannot be formed
        outcome = {"status": "UNAVAILABLE", "error": str(exc)[:200]}
    best: dict[str, Any] = {"status": outcome["status"]}
    if outcome["status"] == "COMPARED":
        rows = outcome["comparison"]["rows"]
        first = contract.acceptance_profiles[0].objectives[0].metric
        maximize = next(m.direction for m in contract.metrics if m.name == first) == "maximize"
        top = sorted(rows, key=lambda r: r["metrics"].get(first, 0), reverse=maximize)[:5]
        best = {
            "status": "COMPARED",
            "period": outcome["comparison"]["period"],
            "baseline_metrics": outcome["baseline_metrics"],
            "finalist": outcome["finalist"],
            "top_by_" + first: [
                {
                    "label": r["label"],
                    "result_ref": r["result_ref"],
                    "metrics": r["metrics"],
                    "verdicts": {pr["profile"]: pr["verdict"] for pr in r["profiles"]},
                }
                for r in top
            ],
            "candidates_tried": outcome["candidates_tried"],
        }
    reviews = [store.artifacts.get(r["review_ref"]) for _, r in sorted(state["reviews"].items())]
    issues = [
        {"event": e["event_type"], "reason": str(e["payload"].get("reason"))[:400]}
        for e in store.events(owner)
        if e["event_type"]
        in {
            "DIRECTOR_FAILURE",
            "CRITIC_FAILURE",
            "PROVIDER_PAUSED",
            "LAB_ROLE_REJECTED",
            "SESSION_PARTIAL_STOP",
        }
    ]
    memory = state.get("memory") or {}
    return {
        "owner": owner,
        "task": {
            "task_id": contract.task_id,
            "domain": contract.domain,
            "objective": contract.objective,
        },
        "status": store.status(owner),
        "control": store.get_control(owner),
        "decisions": {
            "used": len(state["decisions"]),
            "max": spec.max_decisions,
        },
        "plan": engine.plan(owner, state),
        **({"owner_checkpoint": state.get("owner_checkpoint")} if contract.planning else {}),
        "running": state["running"],
        "recent_actions": recent,
        "best_so_far": best,
        **(
            {
                "stage_gate": outcome["stage_gate"],
                "research_stages": outcome["stages"],
                "selection_finalist": outcome["selection_finalist"],
                "confirmation": outcome["confirmation"],
                "independent_validation": "NOT_RUN",
            }
            if contract.stages
            else {}
        ),
        "open_questions": list(reviews[-1]["next_questions"]) if reviews else [],
        "owner_guidance": [
            {
                "guidance_id": gid,
                "text": str(g["text"])[:300],
                "status": "pending" if gid in state["pending_guidance"] else "answered",
                "adopted": (state["responses"].get(gid) or {}).get("adopted"),
                "response": str((state["responses"].get(gid) or {}).get("response") or "")[:400]
                or None,
            }
            for gid, g in state["guidance"].items()
        ],
        "pending_guidance": state["pending_guidance"],
        "last_issue": issues[-1] if issues else None,
        "memory": {
            "sessions": len(memory.get("sessions", [])),
            "lessons": len(memory.get("lessons", [])),
            "skills": [x["name"] for x in memory.get("skills", [])],
            "owner_guidance": len(memory.get("owner_guidance", [])),
        }
        if memory
        else None,
        "usage": store.resource_totals_tree(owner),
        "budget": spec.budget.model_dump(mode="json"),
    }


def brief(store: Store, domain: str | None = None) -> dict[str, Any]:
    """One call to resume work in a new conversation: every session, Dream round and domain
    memory in this store, with what to do next."""
    from jarvis.autonomy_dream import PolicyRegistry
    from jarvis.autonomy_policy import ResearchPolicy, describe
    from jarvis.autonomy_report import session_outcome
    from jarvis.research_memory import KnowledgeBase, session_domain

    with store.connect() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM campaigns")]
    sessions, rounds, next_steps = [], [], []
    domains: set[str] = set()
    for cid in ids:
        manifest = store.manifest(cid)
        if manifest.get("kind") == "AutonomyDreamRound":
            closed = [
                e["payload"] for e in store.events(cid) if e["event_type"] == "DREAM_ROUND_CLOSED"
            ]
            rounds.append(
                {
                    "round": cid,
                    "status": store.status(cid),
                    "outcome": closed[-1] if closed else None,
                }
            )
            continue
        if manifest.get("kind") != "AutonomousResearchSession" or manifest.get("budget_owner"):
            continue
        name = session_domain(store, cid)
        if domain and name != domain:
            continue
        domains.add(name)
        try:
            outcome = session_outcome(store, cid)
        except Exception:
            outcome = {"status": "UNAVAILABLE"}
        stops = [e["payload"] for e in store.events(cid) if e["event_type"] == "SESSION_STOPPED"]
        state, control = store.status(cid), store.get_control(cid)
        sessions.append(
            {
                "owner": cid,
                "domain": name,
                "status": state,
                "control": control,
                "decisions": sum(e["event_type"] == "AUTONOMY_DECISION" for e in store.events(cid)),
                "conclusion": stops[-1].get("conclusion") if stops else None,
                "finalist": outcome.get("finalist"),
            }
        )
        if control == "PAUSED":
            next_steps.append(f"{cid}: paused; `autonomy resume {cid}` then `autonomy run {cid}`")
        elif state in {"READY", "RUNNING"} and control != "CANCELLED":
            next_steps.append(
                f"{cid}: not finished; `autonomy status {cid}`, continue with `autonomy run {cid}` "
                "(only if no other process is running it)"
            )
        elif state == "NEEDS_ATTENTION":
            last_stop = [
                e["payload"] for e in store.events(cid) if e["event_type"] == "SESSION_PARTIAL_STOP"
            ]
            if last_stop and last_stop[-1].get("recoverable"):
                next_steps.append(
                    f"{cid}: stopped after repeated {last_stop[-1].get('role')} failures; "
                    f"`autonomy resume {cid}` then `autonomy run {cid}` continues the same session"
                )
            else:
                next_steps.append(
                    f"{cid}: needs attention; read `autonomy status {cid}` last_issue"
                )
    kb = KnowledgeBase(store)
    memory = {}
    for name in sorted(domains | ({domain} if domain else set())):
        snap = kb.snapshot(name)
        lessons = sorted(snap["lessons"], key=lambda x: x["kind"] != "owner_preference")
        policy_ref = PolicyRegistry(store).active(kb.registry(name))
        remembered = {s["session"] for s in snap["sessions"]}
        unremembered = [
            s["owner"]
            for s in sessions
            if s["domain"] == name
            and s["owner"] not in remembered
            and (
                s["status"] in {"CLOSED", "BUDGET_EXHAUSTED", "NEEDS_ATTENTION"}
                or s["control"] == "PAUSED"
            )
        ]
        if unremembered:
            next_steps.append(
                f"{name}: {len(unremembered)} ended session(s) not in memory; `autonomy memory consolidate`"
            )
        memory[name] = {
            "sessions_remembered": len(snap["sessions"]),
            "lessons": [f"[{x['kind']}] {x['statement'][:300]}" for x in lessons[:25]],
            "owner_guidance": [str(g["text"])[:300] for g in snap["owner_guidance"][-15:]],
            "skills": [
                f"{s['name']} v{s['version']}: {s['description'][:160]}" for s in snap["skills"]
            ],
            "experiments": len(snap["experiments"]),
            "open_questions": snap["open_questions"][-8:],
            "active_policy": describe(
                ResearchPolicy.model_validate(store.artifacts.get(policy_ref))
            ),
            "not_in_memory": unremembered,
        }
    return {
        "store": str(store.root),
        "sessions": sessions,
        "dream_rounds": rounds,
        "memory": memory,
        "next_steps": next_steps,
    }


def run_fixture(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    from jarvis.autonomous_research import AutonomousResearch
    from jarvis.autonomy_policy import ResearchPolicy
    from jarvis.autonomy_report import deliverable, replay_session, verify_session
    from jarvis.quant_world import build_world, world_executors

    world = build_world(store, store.root / "quant-world")
    policy_ref = None
    if args.policy == "explain":
        policy_ref = store.artifacts.put(
            ResearchPolicy(
                name="explain-then-structure",
                guidance="After any gain, explain it with trade-level analysis before more tuning; check retraining when inputs shift.",
                explain_gains_before_more_backtests=True,
                require_model_branch_before_stop=True,
                min_analyses_before_stop=1,
                max_values_per_setting=4,
                rationale="Declared fixture policy",
                origin="declared_fixture",
            )
        )
    config = AgentConfig(
        model="gpt-6.1-sol",
        reasoning_effort=args.effort,
        max_calls=1000,
        reserve_tokens=120000,
        timeout_seconds=900,
    )
    engine = AutonomousResearch(
        store,
        world_executors(world),
        provider_factory(args.agent, eager_stop=args.eager_stop, relay=store.root / "relay"),
        consolidate=True,
    )
    memory_ref, active_policy = memory_and_policy(store, world["contract_ref"])
    owner = engine.create(
        AutonomySessionSpec(
            contract_ref=world["contract_ref"],
            memory_ref=memory_ref,
            policy_ref=policy_ref or active_policy,
            max_decisions=args.max_decisions,
            max_concurrent_actions=1 if args.agent == "fixture" else 2,
            role_config={
                r: config.model_dump(mode="json")
                for r in ("director", "critic", "worker", "consultant")
            },
        )
    )
    if args.guidance:
        engine.advise(owner, args.guidance)
    result = engine.run(owner, wall_seconds=7200)
    report = deliverable(store, owner)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "deliverable.md").write_text(report["markdown"])
        (args.output / "deliverable.json").write_text(
            json.dumps(
                {k: v for k, v in report.items() if k != "markdown"}, ensure_ascii=False, indent=2
            )
        )
    return {
        "owner": owner,
        "run": result,
        "audit": verify_session(store, owner)["validation"],
        "replay": replay_session(store, owner)["status"],
        "finalist": report["finalist"],
        "validation_verdict": report["validation_verdict"],
        "decisions": report["decisions"],
        "actions": [a["kind"] for a in report["actions"]],
        "result_origin": "SIMULATED_FIXTURE",
        "providers": "scripted fixture (simulated token units)"
        if args.agent == "fixture"
        else "codex-cli",
    }


def run_dream_fixture(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    from jarvis.autonomy_dream import (
        AutonomyDream,
        AutonomyDreamSpec,
        DreamTask,
        PolicyRegistry,
        dream_report,
    )
    from jarvis.quant_world import QuantWorldFactory

    registry = PolicyRegistry(store).create()
    dream = AutonomyDream(
        store, QuantWorldFactory(), provider_factory(args.agent, relay=store.root / "relay")
    )
    round_id = dream.create(
        AutonomyDreamSpec(
            registry=registry,
            development=(
                DreamTask(task_id="dev1", family="balanced", seed=11),
                DreamTask(task_id="dev2", family="volatile", seed=12),
            ),
            validation=(
                DreamTask(task_id="val1", family="balanced", seed=21),
                DreamTask(task_id="val2", family="calm", seed=22),
            ),
            canary=(DreamTask(task_id="can1", family="volatile", seed=31),),
            session_max_decisions=args.max_decisions,
        )
    )
    outcome = dream.run(round_id)
    report = dream_report(store, round_id)
    return {
        "round": round_id,
        "outcome": outcome,
        "sessions": report["sessions"],
        "registry": report["registry_history"],
        "canary": report["canary"],
        "result_origin": "SIMULATED_FIXTURE",
    }


def dream_engine(store: Store, declaration: dict[str, Any]) -> Any:
    import sys

    from jarvis.autonomy_dream import AutonomyDream

    if declaration.get("adapter_path"):
        sys.path.insert(0, str(Path(declaration["adapter_path"]).resolve()))
    module, _, name = str(declaration["task_factory"]).partition(":")
    factory = getattr(importlib.import_module(module), name)(**declaration.get("factory_args", {}))
    agent = declaration.get("agent", "codex")
    return AutonomyDream(store, factory, provider_factory(agent, relay=store.root / "relay"))


def run_dream(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    """Dream-RSI on a domain: learns from finished sessions, validates on fresh tasks,
    and adopts into the domain policy registry that every later session starts from."""
    from jarvis.autonomy_dream import AutonomyDreamSpec, dream_report
    from jarvis.research_memory import KnowledgeBase, session_domain

    if args.dream_op == "start":
        declaration = yaml.safe_load(args.declaration.read_text())
        kb = KnowledgeBase(store)
        domain = declaration["domain"]
        sessions = declaration.get("development_sessions", "memory")
        if sessions == "memory":
            sessions = [
                s["session"]
                for s in kb.snapshot(domain)["sessions"]
                if s["status"] in {"CLOSED", "BUDGET_EXHAUSTED", "NEEDS_ATTENTION"}
            ]
        sessions = [s for s in sessions if session_domain(store, s) == domain]
        spec = AutonomyDreamSpec.model_validate(
            {
                "registry": kb.registry(domain),
                "development": declaration.get("development", []),
                "development_sessions": sessions,
                "validation": declaration["validation"],
                "canary": declaration["canary"],
                "session_max_decisions": int(declaration.get("session_max_decisions", 30)),
                **{
                    k: declaration[k]
                    for k in ("session_budget", "developer", "min_mean_gain")
                    if k in declaration
                },
            }
        )
        dream = dream_engine(store, declaration)
        round_id = dream.create(spec)
        store.append(
            round_id, "DECLARATION_RECORDED", {"declaration_ref": store.artifacts.put(declaration)}
        )
        return {
            "round": round_id,
            "development_sessions": len(sessions),
            "registry": spec.registry,
            "note": "Declared only; `autonomy dream run ROUND` executes validation sessions (LLM + backtests).",
        }
    declared = [
        e["payload"] for e in store.events(args.round) if e["event_type"] == "DECLARATION_RECORDED"
    ]
    if args.dream_op == "run":
        if not declared:
            raise AdmissionError("round has no recorded declaration")
        dream = dream_engine(store, store.artifacts.get(declared[0]["declaration_ref"]))
        return {"round": args.round, **dream.run(args.round)}
    return dream_report(store, args.round)


def run(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    from jarvis.autonomy_report import deliverable, replay_session, verify_session

    op = args.operation
    if op == "fixture":
        return run_fixture(store, args)
    if op == "dream-fixture":
        return run_dream_fixture(store, args)
    if op == "dream":
        return run_dream(store, args)
    if op == "memory":
        from jarvis.research_memory import KnowledgeBase

        kb = KnowledgeBase(store)
        if args.memory_op == "show":
            snap = kb.snapshot(args.domain)
            return {
                **{k: len(v) if isinstance(v, list) else v for k, v in snap.items()},
                "lessons_text": [x["statement"] for x in snap["lessons"]],
                "skills": snap["skills"],
                "registry": kb.registry(args.domain),
            }
        if args.memory_op == "consolidate":
            factory = (
                None
                if args.agent == "none"
                else provider_factory(args.agent, relay=store.root / "relay")
            )
            config = AgentConfig(
                model="gpt-6.1-sol",
                reasoning_effort="medium",
                max_calls=4000,
                reserve_tokens=120000,
                timeout_seconds=900,
            )
            if args.session:
                return kb.consolidate(args.session, provider_factory=factory, config=config)
            return {"results": kb.consolidate_store(provider_factory=factory, config=config)}
        if args.memory_op == "export":
            return kb.export(args.domain, args.directory)
        if args.memory_op == "note":
            return {
                **kb.note(args.domain, args.text),
                "note": "Standing owner guidance; every later session in this domain sees it.",
            }
        return kb.import_bundle(args.directory)
    if op == "brief":
        return brief(store, args.domain)
    if op == "policy-registry":
        from jarvis.autonomy_dream import PolicyRegistry

        return {"registry": PolicyRegistry(store).create()}
    if op == "start":
        declaration = yaml.safe_load(args.declaration.read_text())
        engine, built = engine_for(store, declaration)
        memory_ref, policy_ref = memory_and_policy(
            store, built["contract_ref"], memory=bool(declaration.get("memory", True))
        )
        if declaration.get("policy_registry"):
            from jarvis.autonomy_dream import PolicyRegistry

            policy_ref = PolicyRegistry(store).active(declaration["policy_registry"])
        spec = AutonomySessionSpec(
            memory_ref=memory_ref,
            contract_ref=built["contract_ref"],
            input_refs=tuple(built.get("input_refs", ())),
            policy_ref=policy_ref,
            budget=Budget.model_validate(declaration["budget"])
            if declaration.get("budget")
            else AutonomySessionSpec.model_fields["budget"].default,
            max_decisions=int(declaration.get("max_decisions", 200)),
            max_concurrent_actions=int(declaration.get("max_concurrent_actions", 2)),
            owner_checkpoints=bool(declaration.get("owner_checkpoints", True)),
            role_config=declaration.get("role_config", {}),
            runtime=declaration.get("runtime", {}),
        )
        owner = engine.create(spec)
        store.append(
            owner, "DECLARATION_RECORDED", {"declaration_ref": store.artifacts.put(declaration)}
        )
        return {
            "owner": owner,
            "status": store.status(owner),
            "decisions": 0,
            "note": "Declared only; use `autonomy run` to start research.",
            "memory_ref": memory_ref,
            "policy_ref": policy_ref,
        }
    owner = args.owner
    if op == "run":
        engine, _ = engine_for(store, _declared(store, owner))
        result = engine.run(owner, max_steps=args.max_steps, wall_seconds=args.wall_seconds)
        return {"owner": owner, **result, "status_detail": status(store, owner)}
    if op == "advise":
        from jarvis.autonomous_research import AutonomousResearch

        engine = AutonomousResearch.__new__(AutonomousResearch)
        engine.store = store
        gid = engine.advise(owner, args.text, mode="redirect" if args.redirect else "advise")
        return {
            "owner": owner,
            "guidance_id": gid,
            "note": "Visible to the next Director decision, which must respond.",
        }
    if op in {"pause", "resume", "cancel"}:
        recovered = None
        if op == "resume" and store.status(owner) == "NEEDS_ATTENTION":
            from jarvis.autonomous_research import AutonomousResearch

            engine = AutonomousResearch.__new__(AutonomousResearch)
            engine.store = store
            recovered = engine.recover(owner)["recovered"]
        store.control(owner, {"pause": "PAUSED", "resume": "RUNNING", "cancel": "CANCELLED"}[op])
        return {
            "owner": owner,
            "control": store.get_control(owner),
            "status": store.status(owner),
            **({"recovered": recovered, "note": "Run it again to continue."} if recovered else {}),
        }
    if op == "status":
        return status(store, owner)
    if op == "report":
        return verify_session(store, owner)
    if op == "replay":
        return replay_session(store, owner)
    if op == "deliverable":
        report = deliverable(store, owner)
        args.directory.mkdir(parents=True, exist_ok=True)
        (args.directory / "deliverable.md").write_text(report["markdown"])
        (args.directory / "deliverable.json").write_text(
            json.dumps(
                {k: v for k, v in report.items() if k != "markdown"}, ensure_ascii=False, indent=2
            )
        )
        return {
            "owner": owner,
            "written": [
                str(args.directory / "deliverable.md"),
                str(args.directory / "deliverable.json"),
            ],
            "validation_verdict": report["validation_verdict"],
        }
    if op == "sandbox-check":
        return sandbox_check()
    raise AdmissionError("unknown autonomy operation")
