"""Bounded, trusted evidence views for a tools-disabled research reviewer.

All bytes come from already recorded artifacts/blobs. Previews are explicitly partial;
reproduction and hash checks do not certify scientific correctness or complete coverage.
"""

import csv
import io
import json
from itertools import islice
from typing import Any

from jarvis.contracts import IntegrityError
from jarvis.identity import raw_digest
from jarvis.storage import Store


def _text(value: Any, limit: int = 600) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + " [truncated]"


def table_evidence(store: Store, ref: str) -> dict[str, Any]:
    table = store.artifacts.get(ref)
    if table.get("kind") != "ObservedTable":
        raise IntegrityError("review table must be an ObservedTable")
    data = store.blobs.get(table["blob_ref"])
    evidence = {
        "table_ref": ref,
        "blob_ref": table["blob_ref"],
        "hash_verified": True,
        "name": table["name"],
        "rows": table["rows"],
        "bytes": len(data),
        "columns": table["columns"],
        "lineage": table["lineage"],
        "exposure": table["exposure"],
        "preview_is_partial": True,
    }
    if table["format"] == "csv":
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        evidence["preview"] = [
            {str(k): _text(v, 220) for k, v in list(row.items())[:16]} for row in islice(reader, 5)
        ]
    elif (
        table["name"] == "execution_source"
        and "experiment" in table["lineage"]
        and len(data) < 8 * 1024 * 1024
    ):
        proof = json.loads(data)
        if proof.get("kind") != "ExecutionSourceEvidence":
            raise IntegrityError("unexpected captured-source evidence kind")
        if (
            raw_digest(proof["candidate_source"].encode()) != proof["candidate_source_sha256"]
            or raw_digest(proof["baseline_source"].encode()) != proof["baseline_source_sha256"]
        ):
            raise IntegrityError("captured-source evidence bytes do not match its hashes")
        evidence["source_evidence"] = {
            k: (_text(v, 6000) if k == "source_diff" else v)
            for k, v in proof.items()
            if k not in {"baseline_source", "candidate_source"}
        }
        evidence["preview"] = (
            "Full source bytes retained in the immutable table blob; compact exact diff shown in source_evidence."
        )
    elif len(data) <= 12000:
        evidence["preview"] = _text(data.decode(), 2200)
    else:
        evidence["preview"] = "JSON preview omitted: exceeds bounded review view."
    return evidence


def build_review_bundle(
    store: Store,
    owner: str,
    index: int,
    result_ref: str,
    action: dict[str, Any],
    visible: list[str],
    child: str | None,
) -> dict[str, Any]:
    known = set(visible)
    if result_ref not in known:
        raise IntegrityError("review result is not yet visible")
    result = store.artifacts.get(result_ref)
    referenced = [*action.get("input_refs", []), *action.get("result_refs", [])]
    if result.get("kind") == "ImplementationResult":
        referenced.extend(result.get("input_refs", []))
    referenced.extend(
        action[k]
        for k in (
            "baseline_result_ref",
            "training_data_ref",
            "base_candidate_ref",
            "pipeline_patch_ref",
        )
        if action.get(k)
    )
    source_refs = list(dict.fromkeys([result_ref, *referenced]))
    if not set(source_refs) <= known:
        raise IntegrityError("review evidence escaped decision-time visibility")
    roots = [(ref, store.artifacts.get(ref)) for ref in source_refs]
    # Expand backtest observations only through registered visible references.
    for row in result.get("experiments", []):
        ref = row["result_ref"]
        if ref in known:
            roots.append((ref, store.artifacts.get(ref)))
            source_refs.append(ref)
    table_refs: list[str] = []
    records = []
    for ref, value in roots:
        if value.get("kind") == "ObservedTable":
            table_refs.append(ref)
        table_refs.extend(value.get("output_tables", []))
        if isinstance(value.get("tables"), dict):
            table_refs.extend(value["tables"].values())
        if value.get("kind") == "BacktestObservation":
            candidate = store.artifacts.get(value["candidate_ref"])
            model = (
                store.artifacts.get(candidate["model_ref"]) if candidate.get("model_ref") else None
            )
            provenance = value.get("provenance", {})
            records.append(
                {
                    "result_ref": ref,
                    "candidate_ref": value["candidate_ref"],
                    "candidate_origin": candidate["origin"],
                    "model_ref": candidate.get("model_ref"),
                    "model_id": model["model_id"] if model else None,
                    "model_files": model["files"] if model else {},
                    "patch_refs": candidate["patch_refs"],
                    "settings": value["settings"],
                    "evaluator_id": value["evaluator_id"],
                    "dataset_id": value["dataset_id"],
                    "period": value["period"],
                    "metrics": value["metrics"],
                    "provenance": provenance,
                    "missing_execution_fields": [
                        k
                        for k in (
                            "request_id",
                            "commit",
                            "raw_sha256",
                            "evaluator_sha256",
                            "calendar",
                        )
                        if not provenance.get(k)
                    ],
                }
            )
    registered = list(dict.fromkeys(r for r in table_refs if r in known))
    tables = [table_evidence(store, r) for r in registered[:12]]
    scripts = []
    for name, ref in list(result.get("scripts", {}).items())[:2]:
        scripts.append(
            {
                "name": name,
                "blob_ref": ref,
                "hash_verified": True,
                "preview": _text(store.blobs.get(ref).decode(), 1800),
            }
        )
    receipts = []
    native = []
    if child:
        for e in store.events(child):
            if e["event_type"] == "WORKER_STEP":
                observation = store.artifacts.get(e["payload"]["observation_ref"])
                if observation.get("kind") == "NativeExecutionReceipt":
                    native.append(
                        {
                            k: observation[k]
                            for k in (
                                "receipt_ref",
                                "source",
                                "status",
                                "compiler_sha256",
                                "binary_sha256",
                                "inputs_unchanged",
                                "scope",
                            )
                        }
                    )
        for job in store.jobs(child):
            receipt = store.artifacts.get(job["receipt_ref"]) if job["receipt_ref"] else None
            receipts.append(
                {
                    "job_id": job["id"],
                    "status": job["status"],
                    "receipt_ref": job["receipt_ref"],
                    "cost": receipt.get("cost") if receipt else None,
                }
            )
    bundle = {
        "kind": "TrustedReviewEvidence",
        "schema_version": 1,
        "session": owner,
        "action_index": index,
        "result_ref": result_ref,
        "source_refs": list(dict.fromkeys(source_refs)),
        "identity_semantics": {
            "table_ref": "SHA-256 of canonical ObservedTable metadata, not the data file",
            "blob_ref": "SHA-256 of the delivered file bytes; hash_verified checks this identity",
        },
        "result_status": result.get("status"),
        "delivery_status": result.get("delivery_status", "LEGACY_UNSPECIFIED"),
        "reproducible": result.get("reproducible"),
        "tables": tables,
        "tables_omitted": max(0, len(registered) - len(tables)),
        "unregistered_table_refs": [r for r in table_refs if r not in known],
        "scripts": scripts,
        "experiments": records,
        "execution_receipts": receipts[:24],
        **({"native_execution": native} if native else {}),
        "execution_receipts_omitted": max(0, len(receipts) - 24),
        "experiments_omitted": 0,
        "scope": "Recorded bytes and execution receipts only. Previews are partial; repeatability is not causal validity. Missing fields remain unknown.",
    }
    # Bound actual JSON size while retaining identities and explicit omissions.
    for table in reversed(tables):
        if len(json.dumps(bundle, ensure_ascii=False)) <= 24000:
            break
        table["preview"] = "Omitted to fit review budget; immutable table_ref retained."
    if len(json.dumps(bundle, ensure_ascii=False)) > 32000:
        # Large feature schemas/lineage must not make a review context unbounded.
        for table in tables:
            table["columns"] = table["columns"][:16]
            table["lineage"] = _text(table["lineage"], 600)
        for record in records:
            record["provenance"] = _text(record["provenance"], 1800)
    while len(json.dumps(bundle, ensure_ascii=False)) > 32000 and tables:
        tables.pop()
        bundle["tables_omitted"] += 1
    while len(json.dumps(bundle, ensure_ascii=False)) > 32000 and records:
        records.pop()
        bundle["experiments_omitted"] += 1
    return bundle
