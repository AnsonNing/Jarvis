"""Repair known invalid structured answers without repeating unknown operations."""

from collections.abc import Callable
from typing import Any

from jarvis.contracts import AdmissionError, IntegrityError, Record
from jarvis.identity import digest
from jarvis.providers import ResearchProvider
from jarvis.research_roles import RoleCaller, strict_schema
from jarvis.researcher import AgentConfig
from jarvis.role_feedback import correction_context, validation_feedback
from jarvis.runtime_controls import bounded_context
from jarvis.storage import Store


def evidence_role(
    store: Store,
    owner: str,
    key: str,
    role: str,
    context: dict[str, Any],
    model: type[Record],
    provider: ResearchProvider,
    config: AgentConfig,
    guidance: str,
    *,
    max_repairs: int = 2,
    validate: Callable[[Record, dict[str, Any]], None] | None = None,
) -> Record:
    def events(kind: str) -> list[dict[str, Any]]:
        return [e["payload"] for e in store.events(owner) if e["event_type"] == kind]

    correction = None
    for attempt in range(max_repairs + 1):
        attempt_key = key if attempt == 0 else f"{key}:repair:{attempt}"
        known = next((p for p in events("LAB_ROLE_RECORDED") if p["key"] == attempt_key), None)
        if known:
            answer = model.model_validate(store.artifacts.get(known["answer_ref"]))
            if validate:
                validate(answer, store.artifacts.get(known["context_ref"]))
            return answer
        frozen = bounded_context(
            store,
            {**context, **({"correction": correction} if correction else {})},
            config.max_context_bytes,
        )
        prior = next((p for p in events("LAB_ROLE_REQUESTED") if p["key"] == attempt_key), None)
        if prior is None:
            schema = strict_schema(model)
            schema["properties"]["context_id"]["enum"] = [digest(frozen)]
            for field in ["evidence_refs", "counter_evidence_refs"]:
                if field in schema["properties"]:
                    schema["properties"][field]["items"] = {
                        "type": "string",
                        "enum": frozen["visible_evidence_ids"],
                    }
            if "template_index" in schema["properties"] and "templates" in frozen:
                schema["properties"]["template_index"]["enum"] = [
                    x["index"] for x in frozen["templates"]
                ]
            prior = {
                "key": attempt_key,
                "role": role,
                "context_ref": store.artifacts.put(frozen),
                "schema_ref": store.artifacts.put(schema),
            }
            store.append(owner, "LAB_ROLE_REQUESTED", prior)
        if prior["role"] != role:
            raise IntegrityError("frozen role changed")
        frozen = store.artifacts.get(prior["context_ref"])
        rejected = next((p for p in events("LAB_ROLE_REJECTED") if p["key"] == attempt_key), None)
        if rejected is None:
            # Unknown usage/completion raises here; it never enters the answer-repair loop.
            response = RoleCaller(store, provider, config).call(
                owner,
                role,
                frozen,
                store.artifacts.get(prior["schema_ref"]),
                role_budget=True,
                guidance=guidance,
            )
            raw = response["answer"]
            try:
                answer = model.model_validate(raw)
                if answer.context_id != digest(frozen):  # type: ignore[attr-defined]
                    raise ValueError("stale context")
                if hasattr(answer, "evidence_refs"):
                    refs = set(answer.evidence_refs)
                    refs.update(getattr(answer, "counter_evidence_refs", ()))
                    if not refs <= set(frozen["visible_evidence_ids"]):
                        raise ValueError("references must be exact allowed evidence IDs")
                if validate:
                    validate(answer, frozen)
            except ValueError as exc:
                rejected = {
                    **prior,
                    "answer_ref": store.artifacts.put(raw),
                    "receipt_ref": response["receipt_ref"],
                    "reason": str(exc)[:1200],
                    "attempt": attempt,
                    "validation": validation_feedback(exc),
                }
                store.append(owner, "LAB_ROLE_REJECTED", rejected)
            else:
                store.append(
                    owner,
                    "LAB_ROLE_RECORDED",
                    {
                        **prior,
                        "answer_ref": store.artifacts.put(answer),
                        "receipt_ref": response["receipt_ref"],
                    },
                )
                return answer
        correction = correction_context(store, rejected)
    raise AdmissionError("known invalid role answer exceeded two corrective attempts")
