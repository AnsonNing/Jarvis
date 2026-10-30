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
    """This session's observations plus earlier sessions' (from its pinned memory)."""
    spec = AutonomySessionSpec.model_validate(store.manifest(owner)["spec"])
    remembered = store.artifacts.get(spec.memory_ref)["experiments"] if spec.memory_ref else []
    own = [r for p in _payloads(store, owner, "ARTIFACTS_REGISTERED") for r in p["observations"]]
    refs = list(dict.fromkeys([*remembered, *own]))
    return [{**store.artifacts.get(r), "result_ref": r} for r in refs]


def session_outcome(store: Store, owner: str) -> dict[str, Any]:
    """Recompute the comparison and finalist from observations, not from Director claims."""
    _, contract = _contract(store, owner)
    rows = [
        o
        for o in observations(store, owner)
        if o["status"] == "SUCCEEDED"
        # remembered experiments from other datasets are context, not comparable rows
        and (o.get("evaluator_id"), o.get("dataset_id"))
        == (contract.evaluator_id, contract.dataset_id)
    ]
    if contract.stages is not None:
        from jarvis.autonomy_stages import staged_outcome

        return {
            **staged_outcome(store, owner, contract, rows),
            "usage": store.resource_totals_tree(owner),
            "decisions": len(_payloads(store, owner, "AUTONOMY_DECISION")),
        }
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
            from jarvis.autonomy_planning import validate_planning

            _, contract = _contract(store, owner)
            try:
                validate_planning(store, contract, known, decision, context)
            except ValueError as exc:
                raise IntegrityError("invalid decision-time planning: " + str(exc)) from exc
        elif event["event_type"] == "ACTION_REVIEWED":
            if p["review_ref"] not in recorded:
                raise IntegrityError("review lacks its separate role receipt")
        elif event["event_type"] == "REVIEW_EVIDENCE_PACKED":
            evidence = store.artifacts.get(p["evidence_ref"])
            before = engine.state(owner, before_sequence=event["sequence"])
            if evidence["result_ref"] != p["result_ref"] or not set(evidence["source_refs"]) <= set(
                before["visible"]
            ):
                raise IntegrityError("review bundle escaped its recorded evidence boundary")
            for table in evidence["tables"]:
                if table["table_ref"] not in before["visible"]:
                    raise IntegrityError("review table was not available when the bundle was built")
                store.blobs.get(table["blob_ref"])
            for script in evidence["scripts"]:
                store.blobs.get(script["blob_ref"])
        elif event["event_type"] == "CONFIRMATION_CANDIDATE_FROZEN":
            from jarvis.autonomy_stages import configuration, staged_outcome

            _, contract = _contract(store, owner)
            if contract.stages is None:
                raise IntegrityError("confirmation freeze requires declared research stages")
            before = engine.state(owner, before_sequence=event["sequence"])
            rows = [
                {**store.artifacts.get(r), "result_ref": r}
                for r in before["observations"]
                if store.artifacts.get(r)["status"] == "SUCCEEDED"
                and (store.artifacts.get(r)["evaluator_id"], store.artifacts.get(r)["dataset_id"])
                == (contract.evaluator_id, contract.dataset_id)
            ]
            selected = staged_outcome(store, owner, contract, rows)
            winner = selected["selection_finalist"]
            if (
                winner.get("status") != "SELECTED"
                or winner["result_ref"] != p["selection_result_ref"]
            ):
                raise IntegrityError(
                    "confirmation freeze does not match the decision-time selection"
                )
            if (
                configuration(
                    store, contract, winner["candidate_ref"], selected["selection_settings"]
                )
                != p["configuration_id"]
            ):
                raise IntegrityError("confirmation freeze changed candidate/settings")
    state = engine.state(owner)
    for index, child in state["launched"].items():
        if store.manifest(child)["budget_owner"] != owner:
            raise IntegrityError("action child escaped the session budget")
        results = _payloads(store, child, "ACTION_RESULT")
        if index in state["completed"]:
            if not results or results[0]["result_ref"] != state["completed"][index]["result_ref"]:
                raise IntegrityError("action result lineage changed")
            _, contract = _contract(store, owner)
            value = store.artifacts.get(results[0]["result_ref"])
            if contract.analysis_delivery and value.get("delivery_status") == "VERIFIED":
                if not value.get("scripts") or not value.get("outputs"):
                    raise IntegrityError("verified analysis lacks its delivered scripts/outputs")
                if contract.analysis_delivery.require_tables and not value.get("output_tables"):
                    raise IntegrityError("verified analysis lacks evidence tables")
                if contract.analysis_delivery.require_reproduction and not (
                    value.get("reproducible", {}).get("status") == "PASS"
                    and value["reproducible"].get("all_identical") is True
                ):
                    raise IntegrityError("verified analysis lacks a passing clean rerun")
        verify_roles(store, child)
        verify_local_jobs(store, child)
        for step in _payloads(store, child, "WORKER_STEP"):
            request = store.artifacts.get(step["step_ref"])
            observation = store.artifacts.get(step["observation_ref"])
            if (
                request["tool"] == "run_native"
                and observation.get("kind") == "NativeExecutionReceipt"
            ):
                sealed = store.artifacts.get(observation["receipt_ref"])
                if sealed != {
                    k: v for k, v in observation.items() if k not in {"receipt_ref", "input_path"}
                }:
                    raise IntegrityError("native execution receipt changed")
                for path, ref in sealed["source_blobs"].items():
                    store.blobs.get(ref)
                    if ref != sealed["source_manifest"][path]:
                        raise IntegrityError("native source bytes changed during execution")
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


def active_research_directions(
    store: Store, state: dict[str, Any], *, respect_closures: bool
) -> list[str]:
    """Keep historical reviews, but do not reopen explicitly closed staged branches."""
    return sorted(
        {
            question
            for index, review in state["reviews"].items()
            if not respect_closures
            or state["branches"].get(state["completed"][index]["branch_id"], {}).get("status")
            != "closed"
            for question in store.artifacts.get(review["review_ref"])["next_questions"]
        }
    )


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
    next_questions = active_research_directions(
        store, state, respect_closures=contract.stages is not None or contract.planning is not None
    )
    verdict = (
        "DEVELOPMENT_IMPROVEMENT"
        if outcome.get("finalist", {}).get("status") == "SELECTED"
        else "NO_DEVELOPMENT_IMPROVEMENT"
        if outcome["status"] == "COMPARED"
        else "INSUFFICIENT_EVIDENCE"
    )
    if contract.stages and outcome.get("stage_gate") == "AWAITING_CONFIRMATION":
        verdict = "INSUFFICIENT_EVIDENCE"
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
        **(
            {
                "control": store.get_control(owner),
                "planning": plan["planning"],
                "planning_reviews": [
                    {
                        "index": p["index"],
                        "decision_ref": p["decision_ref"],
                        "review": d["planning_review"],
                    }
                    for p in state["decisions"]
                    if (d := store.artifacts.get(p["decision_ref"])).get("planning_review")
                    is not None
                ],
            }
            if contract.planning
            else {}
        ),
        **(
            {"review_evidence": [p["evidence_ref"] for p in state["review_evidence"].values()]}
            if state["review_evidence"]
            else {}
        ),
    }
    if contract.stages:
        report.update(
            research_stages=outcome["stages"],
            stage_gate=outcome["stage_gate"],
            selection_finalist=outcome["selection_finalist"],
            confirmation=outcome["confirmation"],
            independent_validation="NOT_RUN",
        )
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
        lines.append(
            "候選尚未通過所有宣告階段；目前不足以形成任務成果。"
            if r.get("stage_gate") == "AWAITING_CONFIRMATION"
            else "沒有候選通過所有必要的 acceptance 與階段門檻。"
        )
    if r.get("research_stages"):
        lines += ["", "## 研究階段", f"- 總門檻：{r['stage_gate']}"]
        for period, stage in r["research_stages"].items():
            lines.append(f"- {period} ({stage['stage']}): {stage['status']}")
        for check in r["confirmation"]:
            lines.append(f"- confirmation {check['period']}: {check['verdict']}")
        lines.append("- 獨立驗證：NOT_RUN；已曝光期間的確認仍屬開發證據。")
    if r["owner_guidance"]:
        lines += ["", "## 人類建議與回應"]
        for g in r["owner_guidance"]:
            resp = g["response"]
            lines.append(
                f"- {g['guidance_id']}: {g['text']} → {'採納' if resp and resp['adopted'] else '未採納'}：{resp['response'] if resp else '-'}"
            )
    if r.get("planning"):
        board = r["planning"]
        latest = board["latest_review"]
        lines += ["", "## 方向比較與任務停止理由"]
        if latest:
            review = latest["review"]
            lines += [review["summary"], "", review["selection_reason"]]
            for direction in review["directions"]:
                lines.append(
                    f"- {direction['direction_id']} ({direction['kind']}): {direction['disposition']}；下一步 {direction['next_action']}；預估投入 {direction['effort']}；{direction['reason']}"
                )
            if review["stop_basis"]:
                lines.append(f"- 任務停止依據：{review['stop_basis']}")
        if board["owner_checkpoint"]:
            lines.append("- 等待使用者指引：" + board["owner_checkpoint"]["question"])
        lines.append(
            "方向與投入是研究提案及估計；未執行方向沒有觀測績效。分支關閉不等於整體搜尋已窮盡。"
        )
    lines += ["", "## 後續方向"] + [
        f"- {q}" for q in r["next_research_directions"] or ["（Critic 未提出）"]
    ]
    cost = Cost.model_validate({k: v for k, v in r["usage"]["actual"].items()})
    lines += ["", f"## 成本\n{cost.model_dump(exclude={'schema_version'})}"]
    return "\n".join(lines) + "\n"
