"""Offline, receipt-backed controls visible to research and independent critique."""

import difflib
from typing import Any

from jarvis.contracts import AdmissionError
from jarvis.identity import digest
from jarvis.iris import render_candidate
from jarvis.iris_contracts import IrisEnvironment, IrisExperiment
from jarvis.research_queue import queue_tasks, verify_queue
from jarvis.storage import Store


def proposal_evidence(
    store: Store, queue: str, proposals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    env = IrisEnvironment.model_validate(store.manifest(queue)["spec"]["environment"])
    baseline = store.artifacts.get(queue_tasks(store, queue)[0]["cpp_ref"])["cpp"]
    proof = []
    for proposal in proposals:
        experiment = IrisExperiment.model_validate(proposal["experiment"])
        try:
            text = render_candidate(env, experiment.settings)
        except AdmissionError as exc:
            proof.append(
                {
                    "experiment_ref": proposal["experiment_ref"],
                    "status": "RENDER_REJECTED",
                    "reason": str(exc),
                }
            )
            continue
        ref = store.artifacts.put({"cpp": text})
        diff = [
            line
            for line in difflib.unified_diff(baseline.splitlines(), text.splitlines(), n=0)
            if line[:1] in {"+", "-"} and not line.startswith(("+++", "---"))
        ]
        proof.append(
            {
                "experiment_ref": proposal["experiment_ref"],
                "status": "RENDERED_NOT_YET_EXECUTED",
                "candidate_ref": ref,
                "local_diff": diff[:40],
                "diff_lines": len(diff),
                "baseline_ref": queue_tasks(store, queue)[0]["cpp_ref"],
                "boundary": "Trusted deterministic rendering under declared bindings; compiler/backend execution follows approval and admission.",
            }
        )
    return proof


def execution_evidence(
    store: Store, queues: list[str], *, before: str | None = None
) -> list[dict[str, Any]]:
    evidence = []
    for qid in queues:
        verify_queue(store, qid)
        env = store.manifest(qid)["spec"]["environment"]
        rows = queue_tasks(store, qid, before=before)
        baseline = store.artifacts.get(rows[0]["cpp_ref"])["cpp"] if rows else ""
        for row in rows:
            cpp = store.artifacts.get(row["cpp_ref"])["cpp"]
            diff = [
                line
                for line in difflib.unified_diff(baseline.splitlines(), cpp.splitlines(), n=0)
                if line[:1] in {"+", "-"} and not line.startswith(("+++", "---"))
            ]
            receipt = (
                store.artifacts.get(row["preflight_ref"]) if row.get("preflight_ref") else None
            )
            result = store.artifacts.get(row["result_ref"]) if row.get("result_ref") else None
            evidence.append(
                {
                    "queue": qid,
                    "task": row["task_id"],
                    "status": row["status"],
                    "experiment_ref": row["experiment_ref"],
                    "result_ref": row.get("result_ref"),
                    "candidate_ref": row["cpp_ref"],
                    "candidate_content_identity": digest({"cpp": cpp}),
                    "declared_settings": store.artifacts.get(row["experiment_ref"])["settings"],
                    "local_diff_from_queue_baseline": diff[:40],
                    "diff_lines": len(diff),
                    "compile_receipt_ref": row.get("preflight_ref"),
                    "compile_status": store.artifacts.get(receipt["result_ref"])["status"]
                    if receipt
                    else "NOT_DECLARED",
                    "fixed_controls": {
                        k: env[k]
                        for k in [
                            "template_sha256",
                            "model_lineage",
                            "side",
                            "period",
                            "evaluator_sha256",
                        ]
                    },
                    "observed_provenance": result.get("provenance") if result else None,
                    "remote_commit": result.get("commit") if result else None,
                    "boundary": "Verified local candidate bytes, compile receipt and admitted result lineage. Hidden remote caller trace/formula and complete live costs are not established. A negative candidate is information for other hypotheses, not a task-wide stop.",
                }
            )
    return evidence
