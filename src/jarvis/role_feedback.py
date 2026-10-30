"""Bounded, evidence-neutral feedback for a rejected structured proposal."""

import json
from typing import Any

from pydantic import ValidationError

from jarvis.storage import Store


class DecisionAdmissionError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any]):
        super().__init__(message)
        self.code, self.details = code, details


def validation_feedback(error: ValueError) -> dict[str, Any]:
    if isinstance(error, DecisionAdmissionError):
        return {"code": error.code, "details": error.details}
    if isinstance(error, ValidationError):
        return {
            "code": "SCHEMA_VALIDATION",
            "errors": [
                {"path": list(e["loc"]), "type": e["type"], "message": e["msg"][:800]}
                for e in error.errors(
                    include_input=False, include_context=False, include_url=False
                )[:8]
            ],
        }
    return {"code": "ANSWER_VALIDATION", "message": str(error)[:1200]}


def proposal_view(value: Any) -> tuple[Any, bool]:
    """Small answers are exact. Large code proposals get an explicitly partial view."""
    if len(json.dumps(value, ensure_ascii=False).encode()) <= 14000:
        return value, True

    def compact(node: Any, depth: int = 0) -> Any:
        if depth >= 6:
            return {"omitted": "depth limit"}
        if isinstance(node, str):
            return node if len(node) <= 500 else node[:500] + " [OMITTED: large proposal field]"
        if isinstance(node, dict):
            return {k: compact(v, depth + 1) for k, v in list(node.items())[:24]}
        if isinstance(node, list):
            return [compact(v, depth + 1) for v in node[:8]]
        return node

    preview = compact(value)
    if len(json.dumps(preview, ensure_ascii=False).encode()) > 14000:
        return {"partial_json_preview": json.dumps(preview, ensure_ascii=False)[:6000]}, False
    return preview, False


def correction_context(store: Store, rejected: dict[str, Any]) -> dict[str, Any]:
    draft, complete = proposal_view(store.artifacts.get(rejected["answer_ref"]))
    return {
        "rejected_answer_ref": rejected["answer_ref"],
        "rejected_answer": draft,
        "rejected_answer_complete": complete,
        "reason": rejected["reason"],
        "validation": rejected.get("validation", {"code": "LEGACY_VALIDATION"}),
        "instruction": (
            "This is a rejected proposal, NOT observed evidence or executable authority. "
            "Repair the indicated fields, preserving valid experiment intent and numerical inputs. "
            "Use the NEW context_id from this request, not the old draft's context_id. "
            "Do not recreate existing questions/hypotheses: set question=null and address their "
            "existing IDs, or declare genuinely new IDs. An existing matched baseline is a "
            "comparison reference, not another experiment: cite its visible result_ref and omit "
            "that duplicate from experiments. Never change measurements, fixed constraints, "
            "permissions, or visibility; draft references do not become allowed evidence. "
            "The full draft and original cost remain sealed; an omitted field is unknown, not empty."
        ),
    }


def failure_category(failure: dict[str, Any]) -> str:
    # Old journals retain their original fields; inference is only a derived view.
    return str(
        failure.get("category")
        or (
            "provider_ended"
            if str(failure.get("reason", "")).startswith(
                ("provider ended without usage", "charged provider failure:")
            )
            else "invalid_decision"
        )
    )
