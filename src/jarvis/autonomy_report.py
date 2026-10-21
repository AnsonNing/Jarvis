"""Trusted outcome, offline audit, exact replay and the researcher-style deliverable."""

from typing import Any

from jarvis.autonomy_contracts import AutonomyContract, AutonomyDecision, AutonomySessionSpec
from jarvis.autonomy_jobs import verify_local_jobs
from jarvis.candidates import compare_results, select_finalist
from jarvis.contracts import Cost, IntegrityError
from jarvis.identity import digest
from jarvis.research_roles import verify_roles
from jarvis.storage import Store


def _payloads(store: Store, owner: str, kind: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in store.events(owner) if e["event_type"] == kind]


def _contract(store: Store, owner: str) -> tuple[AutonomySessionSpec, AutonomyContract]:
    spec = AutonomySessionSpec.model_validate(store.manifest(owner)["spec"])
    return spec, AutonomyContract.model_validate(store.artifacts.get(spec.contract_ref))


def observations(store: Store, owner: str) -> list[dict[str, Any]]:
    refs = [r for p in _payloads(store, owner, "ARTIFACTS_REGISTERED") for r in p["observations"]]
    return [{**store.artifacts.get(r), "result_ref": r} for r in refs]


def session_outcome(store: Store, owner: str) -> dict[str, Any]:
    """Recompute the comparison and finalist from observations, not from Director claims."""
    _, contract = _contract(store, owner)
    rows = [o for o in observations(store, owner) if o["status"] == "SUCCEEDED"]
    period = contract.periods[0]
    baseline = next(
        (
            o
            for o in rows
            if o["candidate_origin"] == "owner_baseline"
            and not o["changes"]
            and o["period"] == period
        ),
        None,
    )
    usage = store.resource_totals_tree(owner)
    decisions = len(_payloads(store, owner, "AUTONOMY_DECISION"))
    if baseline is None:
        return {"status": "NO_BASELINE_OBSERVATION", "usage": usage, "decisions": decisions}
    others = [
        o for o in rows if o["result_ref"] != baseline["result_ref"] and o["period"] == period
    ]
    if not others:
        return {
            "status": "NO_CANDIDATES",
            "usage": usage,
            "decisions": decisions,
            "baseline": baseline,
        }
    comparison = compare_results(contract, baseline, others)
    finalist = select_finalist(contract, comparison)
    return {
        "status": "COMPARED",
        "baseline_result_ref": baseline["result_ref"],
        "baseline_metrics": baseline["metrics"],
        "comparison": comparison,
        "finalist": finalist,
        "candidates_tried": len(others),
        "usage": usage,
        "decisions": decisions,
    }


def verify_session(store: Store, owner: str) -> dict[str, Any]:
    from jarvis.autonomous_research import AutonomousResearch

    manifest = store.manifest(owner)
    if manifest["kind"] != "AutonomousResearchSession":
        raise IntegrityError("not an autonomous research session")
    events = store.events(owner)
    recorded = {p["answer_ref"]: p for p in _payloads(store, owner, "LAB_ROLE_RECORDED")}
    # A verifier needs no executors: reconstruct derived state from the ledger only.
    engine = AutonomousResearch.__new__(AutonomousResearch)
    engine.store = store
    decisions = 0
    for event in events:
        p = event["payload"]
        if event["event_type"] == "AUTONOMY_DECISION":
            if p["index"] != decisions:
                raise IntegrityError("decision indices are not contiguous")
            decisions += 1
            role = recorded.get(p["decision_ref"])
            if role is None or role["context_ref"] != p["context_ref"]:
                raise IntegrityError("decision lacks its causal role receipt")
            context = store.artifacts.get(p["context_ref"])
            decision = AutonomyDecision.model_validate(store.artifacts.get(p["decision_ref"]))
            if decision.context_id != digest(context):
                raise IntegrityError("decision context changed")
            known = engine.state(owner, before_sequence=event["sequence"])
            if not set(context["visible_evidence_ids"]) <= set(known["visible"]):
                raise IntegrityError("decision context used evidence that was not yet available")
            if sorted(r.guidance_id for r in decision.guidance_responses) != sorted(
                context["pending_guidance_ids"]
            ):
                raise IntegrityError("pending owner guidance was not answered")
            if not set(decision.action.evidence_refs) <= set(context["visible_evidence_ids"]):
                raise IntegrityError("decision cited invisible evidence")
        elif event["event_type"] == "ACTION_REVIEWED":
            if p["review_ref"] not in recorded:
                raise IntegrityError("review lacks its separate role receipt")
    state = engine.state(owner)
    for index, child in state["launched"].items():
        if store.manifest(child)["budget_owner"] != owner:
            raise IntegrityError("action child escaped the session budget")
        results = _payloads(store, child, "ACTION_RESULT")
        if index in state["completed"]:
            if not results or results[0]["result_ref"] != state["completed"][index]["result_ref"]:
                raise IntegrityError("action result lineage changed")
        verify_roles(store, child)
        verify_local_jobs(store, child)
        for step in _payloads(store, child, "WORKER_STEP"):
            store.artifacts.get(step["step_ref"])
            store.artifacts.get(step["observation_ref"])
    verify_roles(store, owner)
    verify_local_jobs(store, owner)
    status = store.status(owner)
    usage = store.resource_totals_tree(owner)
    if status == "CLOSED":
        stops = _payloads(store, owner, "SESSION_STOPPED")
        last = store.artifacts.get(state["decisions"][-1]["decision_ref"])["action"]
        if len(stops) != 1 or last["kind"] != "STOP" or last["scope"] != "session":
            raise IntegrityError("closed session lacks its accepted session STOP")
        if any(usage["pending"].values()):
            raise IntegrityError("closed session retains unsettled reservations")
    return {
        "validation": "VERIFIED",
        "status": status,
        "decisions": decisions,
        "actions_launched": len(state["launched"]),
        "usage": usage,
        "external_calls": 0,
    }


def replay_session(store: Store, owner: str, decisions: list[str] | None = None) -> dict[str, Any]:
    """Exact replay of recorded decisions; any unrecorded branch is INSUFFICIENT_COVERAGE."""
    verify_session(store, owner)
    recorded = _payloads(store, owner, "AUTONOMY_DECISION")
    completed = {p["index"]: p for p in _payloads(store, owner, "ACTION_COMPLETED")}
    requested = [p["decision_ref"] for p in recorded] if decisions is None else decisions
    for index, ref in enumerate(requested):
        if (
            index >= len(recorded)
            or recorded[index]["decision_ref"] != ref
            or index not in completed
        ):
            return {"status": "INSUFFICIENT_COVERAGE", "at": index, "external_calls": 0}
    return {
        "status": "COMPLETE",
        "world_id": store.manifest(owner)["world_id"],
        "decisions": requested,
        "outcomes": [completed[i]["result_ref"] for i in range(len(requested))],
        "external_calls": 0,
    }


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.1f}" if abs(value) >= 100 else f"{value:.4g}"
    return str(value)


def deliverable(store: Store, owner: str) -> dict[str, Any]:
    from jarvis.autonomous_research import AutonomousResearch

    audit = verify_session(store, owner)
    spec, contract = _contract(store, owner)
    engine = AutonomousResearch.__new__(AutonomousResearch)
    engine.store = store
    state = engine.state(owner)
    outcome = session_outcome(store, owner)
    plan = engine.plan(owner, state)
    actions = []
    for p in state["decisions"]:
        d = store.artifacts.get(p["decision_ref"])
        completed = state["completed"].get(p["index"])
        result = store.artifacts.get(completed["result_ref"]) if completed else None
        review = state["reviews"].get(p["index"])
        actions.append(
            {
                "index": p["index"],
                "kind": d["action"]["kind"],
                "branch_id": d["action"]["branch_id"],
                "rationale": d["action"]["rationale"],
                "status": result.get("status") if result else "RUNNING_OR_UNKNOWN",
                "summary": (result or {}).get("summary"),
                "review": store.artifacts.get(review["review_ref"]) if review else None,
            }
        )
    candidates = []
    for ref in state["candidates"]:
        c = store.artifacts.get(ref)
        model = store.artifacts.get(c["model_ref"]) if c["model_ref"] else None
        candidates.append(
            {
                "ref": ref,
                "label": c["label"],
                "origin": c["origin"],
                "model_id": model["model_id"] if model else None,
                "model_validation": model["validation"] if model else None,
                "patches": [store.artifacts.get(r)["target"] for r in c["patch_refs"]],
                "settings": c["settings"],
                "data_policy": c["data_policy"],
            }
        )
    stops = _payloads(store, owner, "SESSION_STOPPED")
    next_questions = sorted(
        {
            q
            for r in state["reviews"].values()
            for q in store.artifacts.get(r["review_ref"])["next_questions"]
        }
    )
    verdict = (
        "DEVELOPMENT_IMPROVEMENT"
        if outcome.get("finalist", {}).get("status") == "SELECTED"
        else "NO_DEVELOPMENT_IMPROVEMENT"
        if outcome["status"] == "COMPARED"
        else "INSUFFICIENT_EVIDENCE"
    )
    report = {
        "kind": "ResearchDeliverable",
        "session": owner,
        "status": audit["status"],
        "audit": audit,
        "task": {
            k: contract.model_dump(mode="json")[k]
            for k in ("task_id", "instruction", "objective", "focus", "exposure")
        },
        "baseline": {
            "candidate_ref": contract.baseline_candidate_ref,
            "evaluator_id": contract.evaluator_id,
            "dataset_id": contract.dataset_id,
            "metrics": outcome.get("baseline_metrics"),
        },
        "research_plan": plan,
        "actions": actions,
        "candidates": candidates,
        "comparison": outcome.get("comparison"),
        "finalist": outcome.get("finalist"),
        "director_stop": stops[-1] if stops else None,
        "validation_verdict": verdict,
        "validation_scope": "Development comparison on "
        + contract.exposure
        + " data. Independent validation needs a sealed period or fresh data and is NOT_RUN here.",
        "owner_guidance": [
            {**g, "response": state["responses"].get(gid)} for gid, g in state["guidance"].items()
        ],
        "next_research_directions": next_questions,
        "usage": outcome["usage"],
        "decisions": outcome["decisions"],
        "policy_ref": spec.policy_ref,
    }
    report["markdown"] = render_markdown(report)
    return report


def render_markdown(r: dict[str, Any]) -> str:
    lines = [
        f"# 研究交付：{r['task']['task_id']}",
        "",
        f"- 狀態：{r['status']}；稽核：{r['audit']['validation']}；決策數：{r['decisions']}",
        f"- 結論等級：**{r['validation_verdict']}**",
        f"- 範圍：{r['validation_scope']}",
        "",
        "## 任務",
        r["task"]["instruction"],
        "",
        "## Baseline",
    ]
    if r["baseline"]["metrics"]:
        lines.append(", ".join(f"{k}={_fmt(v)}" for k, v in r["baseline"]["metrics"].items()))
    lines += ["", "## 研究分支"]
    for b in r["research_plan"]["branches"]:
        closure = b["closure"]["conclusion"] if b["closure"] else "open"
        lines.append(f"- `{b['branch_id']}` ({b['kind']}): {b['goal']} → {closure}")
    lines += ["", "## 假設與判定"]
    for q in r["research_plan"]["questions"]:
        lines.append(f"- {q['question']}")
        for h in q["hypotheses"]:
            verdicts = [
                v["verdict"] for v in q["verdicts"] if v["hypothesis_id"] == h["hypothesis_id"]
            ]
            lines.append(
                f"  - {h['hypothesis_id']}: {h['claim']} → {', '.join(verdicts) or '未判定'}"
            )
    lines += ["", "## 研究歷程"]
    for a in r["actions"]:
        rec = a["review"]["recommendation"] if a["review"] else "-"
        lines.append(
            f"{a['index']}. **{a['kind']}** [{a['branch_id']}] {a['status']} — {a['rationale'][:160]} (Critic: {rec})"
        )
    lines += ["", "## 候選"]
    for c in r["candidates"]:
        lines.append(
            f"- {c['label']} ({c['origin']}): model={c['model_id']}, patches={c['patches'] or '-'}, data={c['data_policy']}"
        )
    if r["comparison"]:
        names = sorted(r["comparison"]["baseline_metrics"])
        lines += [
            "",
            "## 比較（相對 baseline）",
            "| 候選 | " + " | ".join(names) + " | profiles |",
            "|" + "---|" * (len(names) + 2),
        ]
        for row in r["comparison"]["rows"]:
            profile_text = ", ".join(f"{p['profile']}={p['verdict']}" for p in row["profiles"])
            lines.append(
                f"| {row['label']} | "
                + " | ".join(_fmt(row["metrics"].get(m)) for m in names)
                + f" | {profile_text} |"
            )
        lines.append(
            f"\nPareto front: {', '.join(x[7:15] for x in r['comparison']['pareto_front'])}"
        )
    lines += ["", "## Finalist（依預先宣告規則）"]
    finalist = r["finalist"] or {}
    if finalist.get("status") == "SELECTED" and r["comparison"]:
        row = next(x for x in r["comparison"]["rows"] if x["result_ref"] == finalist["result_ref"])
        lines.append(
            f"**{row['label']}**（profile {finalist['profile']}）: "
            + ", ".join(f"{k}={_fmt(v)}" for k, v in sorted(row["metrics"].items()))
            + f"\n\nmodel={row['model_id']}；data={row['data_policy']}；result `{finalist['result_ref'][7:19]}`"
        )
    else:
        lines.append("沒有候選通過任何 acceptance profile。")
    if r["owner_guidance"]:
        lines += ["", "## 人類建議與回應"]
        for g in r["owner_guidance"]:
            resp = g["response"]
            lines.append(
                f"- {g['guidance_id']}: {g['text']} → {'採納' if resp and resp['adopted'] else '未採納'}：{resp['response'] if resp else '-'}"
            )
    lines += ["", "## 後續方向"] + [
        f"- {q}" for q in r["next_research_directions"] or ["（Critic 未提出）"]
    ]
    cost = Cost.model_validate({k: v for k, v in r["usage"]["actual"].items()})
    lines += ["", f"## 成本\n{cost.model_dump(exclude={'schema_version'})}"]
    return "\n".join(lines) + "\n"
