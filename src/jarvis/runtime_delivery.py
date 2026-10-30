"""Deterministic, provider-free delivery for every exit from the autonomous runner."""

import json
import os
from typing import Any

from jarvis.contracts import IntegrityError
from jarvis.identity import digest, file_digest
from jarvis.storage import Store


def export_exit(store: Store, owner: str, exit_result: dict[str, Any]) -> dict[str, Any]:
    from jarvis.autonomy_report import deliverable

    read_errors: dict[str, str] = {}

    def safe_events(member: str) -> list[dict[str, Any]]:
        try:
            return store.events(member)
        except Exception as exc:
            read_errors[member] = type(exc).__name__ + ": " + str(exc)[:500]
            return []

    try:
        report = deliverable(store, owner)
    except Exception as exc:
        # A failed audit must still be delivered truthfully; do not synthesize a verdict.
        report = {
            "kind": "ResearchDeliverable",
            "session": owner,
            "status": "UNVERIFIED",
            "validation_verdict": "UNVERIFIED",
            "audit": {"validation": "FAILED", "error": type(exc).__name__ + ": " + str(exc)[:1000]},
            "markdown": "# 研究部分交付\n\n證據稽核未完成；不得採用候選或推論研究成果。\n",
        }
    manifest = store.manifest(owner)
    events = safe_events(owner)
    children = [e["payload"]["child"] for e in events if e["event_type"] == "ACTION_LAUNCHED"]
    pending = [
        {"owner": member, "job_id": j["id"], "status": j["status"], "request_ref": j["request_ref"]}
        for member in [owner, *children]
        for j in store.jobs(member)
        if not j["finalized"]
    ]
    recoveries = [
        {"owner": member, "event": e["event_type"], **e["payload"]}
        for member in [owner, *children]
        for e in safe_events(member)
        if e["event_type"]
        in {
            "ROLE_TRANSPORT_RETRY",
            "ROLE_RESERVATION_OVERRUN",
            "RUNTIME_PROVIDER_RETRY",
            "DIRECTOR_FAILURE",
            "CRITIC_FAILURE",
            "WORKER_PROVIDER_FAILURE",
            "SESSION_PARTIAL_STOP",
            "JOB_SETTLED_AT_RESERVATION",
        }
    ]
    settlements = [
        {
            "owner": member,
            "job_id": job["id"],
            "charged": json.loads(job["actual"]),
            "basis": "RESERVATION_NOT_OBSERVED_USAGE",
        }
        for member in [owner, *children]
        for job in store.jobs(member)
        if job["status"] == "SETTLED_AT_RESERVATION" and job["actual"]
    ]
    stops = [e["payload"] for e in events if e["event_type"] == "SESSION_PARTIAL_STOP"]
    recoverable = exit_result.get("status") == "NEEDS_ATTENTION" and bool(
        stops and stops[-1].get("recoverable")
    )
    resume = (
        "Recoverable role stop: `autonomy resume` grants fresh retry rounds in this same session, then `autonomy run` continues; the pinned source and budget are unchanged."
        if recoverable
        else "This exit requires diagnosis/configuration review before resumption; resume only changes owner control and cannot clear a terminal status or change the pinned source. No budget or session is automatically recreated."
        if exit_result.get("status")
        in {"NEEDS_ATTENTION", "BUDGET_EXHAUSTED", "INFRASTRUCTURE_INTERRUPTED"}
        else "Same session and immutable source; adopt existing receipts. Unknown pending jobs require reconciliation. Owner pause/checkpoint requires owner resume. No new budget or successor session is created."
    )
    checkpoint = {
        "exit": exit_result,
        "control": store.get_control(owner),
        "pending_jobs": pending,
        "workflow_complete": exit_result.get("status") == "CLOSED"
        and not pending
        and report["audit"]["validation"] == "VERIFIED",
        "research_improvement": report.get("validation_verdict"),
        "usage": store.resource_totals_tree(owner),
        "usage_accounting": {
            "includes_unobserved_reservation_charges": bool(settlements),
            "reservation_settlements": settlements,
            "scope": "The ledger's actual totals include these provisional charges; they are not measured provider tokens or billing. Other costs are provider/executor-reported, including explicitly simulated fixture units.",
        },
        "budget": manifest["spec"]["budget"],
        "source_identity": manifest.get("source_identity"),
        "world_id": manifest["world_id"],
        "event_heads": {
            m: (rows[-1] if (rows := safe_events(m)) else None) for m in [owner, *children]
        },
        "journal_read_errors": read_errors,
        "recovery_history": recoveries,
        "stop_details": stops[-1] if stops else None,
        "resume_point": resume,
    }
    report["runtime_exit"] = checkpoint
    report["markdown"] += (
        "\n\n## 執行收尾\n\n"
        f"- 執行結果：{exit_result.get('status')}；原因：{exit_result.get('reason') or exit_result.get('error') or '見研究判定'}\n"
        f"- 流程完成：{checkpoint['workflow_complete']}；待核對工作：{len(pending)}\n"
        f"- 用量帳務：{len(settlements)} 筆工作以預留額度暫估入帳；這些數字不是實測模型用量或帳單。\n"
        f"- 接續位置：{checkpoint['resume_point']}\n"
    )
    report_ref = store.artifacts.put(report)
    root = store.root / "deliveries" / owner
    root.mkdir(parents=True, exist_ok=True)
    final = root / report_ref[7:]
    if not final.exists():
        staging = root / (report_ref[7:] + ".staging")
        staging.mkdir(exist_ok=True)
        (staging / "deliverable.json").write_text(
            json.dumps(
                {k: v for k, v in report.items() if k != "markdown"},
                ensure_ascii=False,
                indent=2,
            )
        )
        (staging / "deliverable.md").write_text(report["markdown"])
        (staging / "manifest.json").write_text(
            json.dumps(
                {
                    "kind": "RuntimeDeliveryManifest",
                    "report_ref": report_ref,
                    "files": {
                        p.name: file_digest(p)
                        for p in sorted(staging.iterdir())
                        if p.name != "manifest.json"
                    },
                },
                indent=2,
            )
        )
        staging.rename(final)
    sealed_manifest = json.loads((final / "manifest.json").read_text())
    if (
        sealed_manifest.get("report_ref") != report_ref
        or set(sealed_manifest.get("files", {})) != {"deliverable.json", "deliverable.md"}
        or any(file_digest(final / name) != ref for name, ref in sealed_manifest["files"].items())
    ):
        raise IntegrityError("sealed delivery changed; retained for inspection")
    pointer = {"report_ref": report_ref, "directory": str(final.resolve())}
    temporary = root / "latest.tmp"
    temporary.write_text(json.dumps(pointer, indent=2))
    os.replace(temporary, root / "latest.json")
    return {**pointer, "manifest_ref": digest(json.loads((final / "manifest.json").read_text()))}
