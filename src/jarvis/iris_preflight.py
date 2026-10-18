"""Receipt-first compile admission; offline verification never invokes a compiler."""

import time
from typing import Any

from jarvis.contracts import AdmissionError, Cost, IntegrityError, JarvisError
from jarvis.identity import digest
from jarvis.iris_contracts import IrisEnvironment
from jarvis.iris_design import compile_candidate
from jarvis.storage import Store


def preflight(store: Store, queue: str, env: IrisEnvironment, cpp_ref: str) -> str | None:
    if env.design_profile is None:
        return None
    request = {
        "kind": "IrisCompileAdmission",
        "world_id": store.manifest(queue)["world_id"],
        "cpp_ref": cpp_ref,
        "profile_sha256": env.accepted_inputs[env.design_profile],
    }
    known = [j for j in store.jobs(queue) if j["request_ref"] == digest(request)]
    jid = (
        known[0]["id"]
        if known
        else store.reserve(queue, request, Cost(actions=1, compute_units=1, wall_seconds=65))
    )
    job = next(j for j in store.jobs(queue) if j["id"] == jid)
    receipt = store.read_receipt(jid)
    if receipt is None:
        if job["status"] != "QUEUED":
            store.set_status(queue, "NEEDS_RECONCILIATION")
            raise JarvisError("compile completion unknown; retained local reservation")
        store.start_job(jid)
        started = time.monotonic()
        result = compile_candidate(env, store.artifacts.get(cpp_ref)["cpp"])
        receipt = {
            "job_id": jid,
            "request_ref": digest(request),
            "world_id": request["world_id"],
            "result_ref": store.artifacts.put(result),
            "cost": Cost(
                actions=1, compute_units=1, wall_seconds=time.monotonic() - started
            ).model_dump(),
        }
        store.write_receipt(jid, receipt)
    store.finish_auxiliary_job(
        jid, "IRIS_COMPILE_CHECKED", {"cpp_ref": cpp_ref, "result_ref": receipt["result_ref"]}
    )
    if store.artifacts.get(receipt["result_ref"])["status"] != "PASS":
        raise AdmissionError("candidate failed local compile admission; no remote submission")
    return digest(receipt)


def verify_preflight(store: Store, queue: str, event: dict[str, Any]) -> None:
    p = event["payload"]
    manifest = store.manifest(queue)
    env = IrisEnvironment.model_validate(manifest["spec"]["environment"])
    if env.design_profile is None:
        return
    receipt = store.artifacts.get(p["preflight_ref"])
    jobs = [j for j in store.jobs(queue) if j["id"] == receipt["job_id"]]
    request = store.artifacts.get(jobs[0]["request_ref"]) if jobs else {}
    checks = [
        e
        for e in store.events(queue)
        if e["event_type"] == "IRIS_COMPILE_CHECKED" and e["payload"]["job_id"] == receipt["job_id"]
    ]
    if (
        not jobs
        or not jobs[0]["finalized"]
        or not checks
        or checks[0]["sequence"] >= event["sequence"]
        or checks[0]["payload"]["cpp_ref"] != p["cpp_ref"]
        or checks[0]["payload"]["result_ref"] != receipt["result_ref"]
        or checks[0]["payload"]["receipt_ref"] != p["preflight_ref"]
        or request
        != {
            "kind": "IrisCompileAdmission",
            "world_id": manifest["world_id"],
            "cpp_ref": p["cpp_ref"],
            "profile_sha256": env.accepted_inputs[env.design_profile],
        }
        or receipt["request_ref"] != digest(request)
        or receipt["world_id"] != manifest["world_id"]
        or Cost.model_validate_json(jobs[0]["actual"]) != Cost.model_validate(receipt["cost"])
        or store.artifacts.get(receipt["result_ref"])["status"] != "PASS"
    ):
        raise IntegrityError("strategy candidate lacks prior verified compile receipt")
