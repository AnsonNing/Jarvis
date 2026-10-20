"""Private, read-only export of actual research cowork, tools and skill lineage."""

import json
from pathlib import Path
from typing import Any

from jarvis.storage import Store, utcnow


def export_notebook(store: Store, owner: str, directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    members = store.resource_members(owner)
    records = []
    roles = []
    for member in members:
        jobs = {j["id"]: j for j in store.jobs(member)}
        for event in store.events(member):
            row = {"owner": member, **event}
            records.append(row)
            if event["event_type"] == "ROLE_COMPLETED":
                p = event["payload"]
                request = store.artifacts.get(jobs[p["job_id"]]["request_ref"])
                receipt = store.artifacts.get(p["receipt_ref"])
                roles.append(
                    {
                        "owner": member,
                        "recorded_at": event["recorded_at_utc"],
                        "role": request["role"],
                        "context_ref": request["context_id"],
                        "request_ref": jobs[p["job_id"]]["request_ref"],
                        "receipt_ref": p["receipt_ref"],
                        "model": request["config"].get("model"),
                        "effort": request["config"].get("reasoning_effort"),
                        "cost": receipt["cost"],
                        "answer": receipt["answer"],
                        "context": request["context"],
                    }
                )
    records.sort(key=lambda r: (r["recorded_at_utc"], r["owner"], r["sequence"]))
    roles.sort(key=lambda r: (r["recorded_at"], r["owner"], r["role"]))
    data = {
        "kind": "PrivateResearchNotebook",
        "owner": owner,
        "recorded_at": utcnow(),
        "status": store.status(owner),
        "members": {m: store.manifest(m) for m in members},
        "usage": store.resource_totals_tree(owner),
        "roles": roles,
        "events": records,
        "trace_scope": "Actual contexts, final structured answers, sources, receipts and observable decisions; no inferred hidden chain of thought",
    }
    (directory / "audit.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# 私有研究過程紀錄",
        f"研究：`{owner}`",
        f"狀態：`{data['status']}`",
        f"更新：{data['recorded_at']}",
        f"已完成角色呼叫：{len(roles)}",
        "完整時序、任務宣告與用量保存在同目錄 audit.json；原始 provider 輸入與輸出留在私有 store。",
        "## Agent cowork",
    ]
    for role in roles:
        lines += [
            f"### {role['recorded_at']} — {role['role']}",
            f"模型：`{role['model']}`；effort：`{role['effort']}`；owner：`{role['owner']}`",
            f"輸入：`{role['request_ref']}`；收據：`{role['receipt_ref']}`",
            "```json",
            json.dumps(role["answer"], ensure_ascii=False, indent=2),
            "```",
        ]
    lines += ["## 搜尋、實驗、Dream 與能力／skill 時序", "成功、失敗、不確定及未執行紀錄均保留。"]
    for row in records:
        kind = row["event_type"]
        if any(
            s in kind
            for s in [
                "TOOL",
                "QUOTA",
                "IRIS_",
                "DIRECTION",
                "CRITIC",
                "EXPERIENCE",
                "CAPABILITY",
                "SHADOW",
                "DREAM",
                "SESSION_",
            ]
        ):
            lines += [
                f"- {row['recorded_at_utc']} `{kind}`／`{row['owner']}`："
                + json.dumps(row["payload"], ensure_ascii=False)
            ]
    (directory / "NOTEBOOK.md").write_text("\n\n".join(lines) + "\n")
    return {
        "status": "EXPORTED",
        "owner": owner,
        "directory": str(directory),
        "role_calls": len(roles),
        "events": len(records),
        "external_calls": 0,
    }
