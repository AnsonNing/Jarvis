"""Reports reference sealed numerical evidence and preserve unsuccessful attempts."""

import json
from typing import Any

from jarvis.history import nodes, verify
from jarvis.storage import Store


def report(store: Store, campaign_id: str) -> dict[str, Any]:
    verification = verify(store, campaign_id)
    manifest = store.manifest(campaign_id)
    experiments = []
    for node in nodes(store, campaign_id):
        request = store.artifacts.get(node["request_ref"])
        receipt = store.artifacts.get(node["receipt_ref"])
        evaluation = store.artifacts.get(node["evaluation_ref"])
        experiments.append(
            {
                "node_id": node["node_id"],
                "parent_id": node["parent_id"],
                "action": request["action"]["action_id"],
                "hypothesis": request["action"]["hypothesis"],
                "status": receipt["observation"]["status"],
                "metrics": receipt["observation"]["metrics"],
                "findings": receipt["observation"]["findings"],
                "evaluation": evaluation,
                "evaluation_ref": node["evaluation_ref"],
                "receipt_ref": node["receipt_ref"],
                "cost": receipt["cost"],
                "origin": receipt["observation"]["result_origin"],
                "critique": store.artifacts.get(node["critique_ref"])
                if node.get("critique_ref")
                else None,
                "finding": store.artifacts.get(node["finding_ref"])
                if node.get("finding_ref")
                else None,
            }
        )
    events = store.events(campaign_id)
    formal = next((e["payload"] for e in events if e["event_type"] == "FORMAL_RESULT"), None)
    if formal and "evaluation_ref" in formal:
        formal = {**formal, "evaluation": store.artifacts.get(formal["evaluation_ref"])}
    status = store.status(campaign_id)
    financial = manifest["task"]["domain"] == "hermes"
    outcome = "COMPLETED_WORKFLOW" if status == "CLOSED" else status
    if not financial and formal and "evaluation" in formal:
        outcome = (
            "VALIDATED_IMPROVEMENT_IN_SYNTHETIC_SCOPE"
            if formal["evaluation"]["acceptance"] == "PASS"
            else "NO_DEMONSTRATED_IMPROVEMENT"
        )
    scope_limits = list(
        dict.fromkeys(s for x in experiments for s in x["evaluation"]["limitations"])
    )
    value = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "status": status,
        "outcome": outcome,
        "goal": manifest["task"]["goal"],
        "task_identity": manifest["task_identity"],
        "world_id": manifest["world_id"],
        "policy_id": manifest["policy_id"],
        "scope": manifest["world"]["scope"],
        "controller": "deterministic Q0; no external LLM calls",
        "experiments": experiments,
        "formal": formal,
        "usage": store.usage(campaign_id),
        "verification": verification,
        "limitations": scope_limits
        + [
            "Generated-code execution and live LLM integration are not implemented",
            "No live trading, fresh strategy holdout, capability transfer or Dream-RSI improvement is established",
            "Existing artifacts are human-provided; analyses are not autonomous strategy discoveries",
        ],
    }
    return value


def render_markdown(value: dict[str, Any]) -> str:
    lines = [
        "# Jarvis research report",
        "",
        f"Campaign: `{value['campaign_id']}`",
        f"Status: **{value['status']}** · Outcome: **{value['outcome']}**",
        "",
        f"Goal: {value['goal']}",
        f"Scope: `{value['scope']}`",
        f"Controller: {value['controller']}",
        "",
        "| Action | Tool status | Evaluation | Primary value | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in value["experiments"]:
        ev = item["evaluation"]
        lines.append(
            f"| {item['action']} | {item['status']} | {ev['acceptance']} | {ev['value']} | `{item['evaluation_ref']}` |"
        )
    lines.extend(
        [
            "",
            "Every attempt, including negative results, remains in the evidence store.",
            "",
            f"Resource usage: `{value['usage']}`",
            "",
        ]
    )
    for item in value["experiments"]:
        lines.extend(["", f"## {item['action']}", ""])
        if item["metrics"]:
            lines.extend(["| Observed metric | Value |", "| --- | --- |"])
            lines.extend(f"| {name} | {number} |" for name, number in item["metrics"].items())
            lines.append("")
        for text in item["findings"]:
            try:
                finding = json.loads(text)
            except ValueError:
                lines.extend([text, ""])
                continue
            if "source_rows" in finding:
                lines.extend(
                    [
                        f"Source: {finding['source_rows']:,} rows; {finding['feature_slots']} feature slots.",
                        f"Declared policy: `{finding['selected_policy']['variant']} / {finding['selected_policy']['pair']}`.",
                        f"README matches current model metadata: {finding['readme_pair_matches_metadata']}.",
                        "",
                    ]
                )
            elif "scenarios" in finding:
                lines.extend(
                    [
                        finding["unit"],
                        "",
                        "| Extra cost per kept entry | Adjusted entry PL |",
                        "| --- | --- |",
                    ]
                )
                lines.extend(
                    f"| {r['additional_cost']} | {r['adjusted_entry_pl']} |"
                    for r in finding["scenarios"]
                )
                lines.extend(["", finding["fee_semantics"], ""])
            elif "per_product" in finding:
                lines.extend(["| Product | Kept entries | Entry PL |", "| --- | --- | --- |"])
                lines.extend(
                    f"| {r['product']} | {r['count']} | {r['sum']} |"
                    for r in finding["per_product"]
                )
                lines.extend(
                    [
                        "",
                        f"Positive active days: {finding['positive_days']}; negative active days: {finding['negative_days']}.",
                        "",
                    ]
                )
            elif "cancelled_entries" in finding:
                lines.extend(
                    [
                        f"Cancelled entries: {finding['cancelled_entries']}.",
                        f"Avoided source losses: {finding['avoided_loss_pl']}; sacrificed source profits: {finding['sacrificed_profit_pl']}.",
                        finding["assumption"],
                        "",
                    ]
                )
            else:
                lines.extend(["```json", json.dumps(finding, indent=2), "```", ""])
    if value["formal"]:
        lines.extend([f"Formal evaluation: `{value['formal']}`", ""])
    lines.extend(
        [
            "Limitations:",
            "",
            *[f"- {s}" for s in value["limitations"]],
            "",
            f"World: `{value['world_id']}`",
            f"Policy: `{value['policy_id']}`",
        ]
    )
    return "\n".join(lines) + "\n"
