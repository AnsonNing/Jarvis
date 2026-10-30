"""Small, immutable execution envelopes; old session identities stay unchanged."""

import json
from typing import Any

from pydantic import Field

from jarvis.contracts import AdmissionError, Record
from jarvis.storage import Store


class ContextEnvelopeExceeded(AdmissionError):
    """A call cannot fit. This is not exhaustion of the campaign budget."""


class RoleCallLimitExceeded(ContextEnvelopeExceeded):
    """The declared role allowance ended, independently of the total budget."""


class RuntimeControls(Record):
    max_context_bytes: int = Field(default=120000, ge=8000, le=150000)
    max_prompt_bytes: int = Field(default=175000, ge=16000, le=190000)
    provider_retries: int = Field(default=2, ge=0, le=3)
    provider_backoff_seconds: float = Field(default=1, ge=0, le=30)


def encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode())


def bounded_context(store: Store, context: dict[str, Any], limit: int | None) -> dict[str, Any]:
    """Compact old catalog detail, never rewrite constraints, hypotheses or numbers.

    Full views are sealed before compaction. The remaining evidence catalog is explicit;
    omitted artifact IDs are not silently added to the decision's allowed citations.
    An irreducible owner/task/plan is rejected before a provider is called.
    """
    if limit is None or encoded_size(context) <= limit:
        return context
    full_ref = store.artifacts.put(context)
    for keep in (16, 8, 4, 1, 0):
        result = dict(context)
        omitted: dict[str, int] = {}
        for key in (
            "tables",
            "candidates",
            "experiment_ledger",
            "observations",
            "prior_evidence",
            "code_patches",
            "transcript",
        ):
            rows = context.get(key)
            if isinstance(rows, list) and len(rows) > keep:
                fields = {
                    "experiment_ledger": (
                        "result_ref",
                        "label",
                        "status",
                        "candidate_ref",
                        "model_id",
                        "changes",
                        "settings",
                        "period",
                        "metrics",
                        "tables",
                        "comparable",
                        "from_memory",
                        "action_index",
                        "profiles",
                    ),
                    "observations": (
                        "index",
                        "kind",
                        "branch_id",
                        "result_ref",
                        "status",
                        "result_summary",
                        "review",
                    ),
                    "candidates": (
                        "ref",
                        "label",
                        "origin",
                        "parent_ref",
                        "patches",
                        "settings",
                        "data_policy",
                        "model_id",
                        "from_memory",
                    ),
                    "tables": ("ref", "name", "format", "rows", "lineage", "from_memory"),
                    "code_patches": ("ref", "target"),
                    "prior_evidence": ("ref",),
                }
                old = rows[:-keep] if keep else rows
                compact = []
                for row in old:
                    if key == "transcript":
                        # Workspace histories already compact steps older than eight
                        # turns to {step, note, tool}; those have no nested request.
                        if "request" not in row:
                            compact.append(row)
                            continue
                        compact.append(
                            {
                                "step": row["step"],
                                "request": {
                                    k: v
                                    for k, v in row["request"].items()
                                    if k in {"note", "tool", "path"}
                                },
                                "observation": {
                                    k: v
                                    for k, v in row["observation"].items()
                                    if k in {"status", "receipt_ref", "input_path"}
                                },
                                "details_omitted": True,
                            }
                        )
                    else:
                        compact.append(
                            {k: v for k, v in row.items() if k in fields[key]}
                            | {"details_omitted": True}
                        )
                result[key] = compact + (rows[-keep:] if keep else [])
                omitted[key] = len(rows) - keep
        # Keep standing owner preferences intact; older scientific detail remains
        # in the pinned view. No generated summary is substituted for a finding.
        memory = context.get("research_memory")
        if isinstance(memory, dict):
            lessons = memory.get("lessons", [])
            preferences = [
                v for v in lessons if isinstance(v, dict) and v.get("kind") == "owner_preference"
            ]
            rest = [v for v in lessons if v not in preferences]
            result["research_memory"] = {
                "view_ref": full_ref,
                "details_omitted": True,
                "owner_guidance": memory.get("owner_guidance", []),
                "lessons": [*preferences, *(rest[-keep:] if keep else [])],
                "skills": memory.get("skills", []),
                "open_questions": memory.get("open_questions", [])[-keep:] if keep else [],
            }
        visible = context.get("visible_evidence_ids", [])
        if len(visible) > 64:

            def references(value: Any) -> set[str]:
                if isinstance(value, str):
                    return {value} if value.startswith("sha256:") and len(value) == 71 else set()
                if isinstance(value, dict):
                    return set().union(
                        *(
                            references(v)
                            for k, v in value.items()
                            if k not in {"visible_evidence_ids", "analyzable_evidence_ids"}
                        )
                    )
                if isinstance(value, list):
                    return set().union(*(references(v) for v in value))
                return set()

            required = references(result)
            selected = list(
                dict.fromkeys(
                    [
                        *visible[:2],
                        *[r for r in visible if r in required],
                        *visible[-max(8, keep * 4) :],
                    ]
                )
            )
            result["visible_evidence_ids"] = selected
            if "analyzable_evidence_ids" in result:
                result["analyzable_evidence_ids"] = [
                    r for r in result["analyzable_evidence_ids"] if r in selected
                ]
            omitted["visible_evidence_ids"] = len(visible) - len(selected)
        result["context_compaction"] = {
            "full_view_ref": full_ref,
            "omitted_counts": omitted,
            "instruction": "Catalog view is partial. Do not infer missing outcomes or cite omitted IDs. Immutable task, constraints and research plan are retained verbatim.",
        }
        if encoded_size(result) <= limit:
            return result
    # Last resort before refusing: standing owner intent stays (shortened, with the sealed
    # full view referenced); older lesson detail and skill descriptions are dropped.
    memory = result.get("research_memory")
    if isinstance(memory, dict):
        result["research_memory"] = {
            "view_ref": full_ref,
            "details_omitted": True,
            "owner_guidance": [
                str(g.get("text") if isinstance(g, dict) else g)[:300]
                for g in memory.get("owner_guidance", [])[-8:]
            ],
            "owner_preferences": [
                str(v.get("statement"))[:300]
                for v in memory.get("lessons", [])
                if isinstance(v, dict) and v.get("kind") == "owner_preference"
            ][-8:],
            "skills": [
                s.get("name") if isinstance(s, dict) else s for s in memory.get("skills", [])
            ],
        }
        if encoded_size(result) <= limit:
            return result
    raise ContextEnvelopeExceeded(
        f"CONTEXT_ENVELOPE: immutable task/plan or latest required content exceeds {limit} bytes; full_view_ref={full_ref}"
    )


def prompt_preflight(context: dict[str, Any], schema: dict[str, Any], config: Any) -> None:
    if config.max_context_bytes and encoded_size(context) > config.max_context_bytes:
        raise ContextEnvelopeExceeded("CONTEXT_ENVELOPE: call exceeds declared context bytes")
    if config.max_prompt_bytes is None:
        return
    # Includes schema sent separately and headroom for the fixed role prompt and context_id.
    size = encoded_size(context) + encoded_size(schema) + 8000
    if size > config.max_prompt_bytes or size + 8192 > config.reserve_tokens:
        raise ContextEnvelopeExceeded(
            f"CALL_ENVELOPE: {size} conservative prompt bytes plus 8192 output headroom; "
            f"prompt cap={config.max_prompt_bytes}, token reservation={config.reserve_tokens}. "
            "Bytes are a conservative admission estimate, not observed token usage."
        )
