"""Finite Iris research queue. Reserve once, submit once, collect by durable ID."""

import json
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from jarvis.campaign import code_provenance, source_identity
from jarvis.contracts import AdmissionError, Cost, IntegrityError, JarvisError
from jarvis.identity import digest, file_digest
from jarvis.iris import IrisTransport, LocalIrisTransport, render_candidate, verify_environment
from jarvis.iris_contracts import IrisAuthorization, IrisExperiment, IrisQueueSpec
from jarvis.storage import Store

TERMINAL = {"SUCCEEDED", "FAILED", "REJECTED", "INVALID_RESULT"}


def queue_tasks(store: Store, queue_id: str) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for event in store.events(queue_id):
        payload = event["payload"]
        if event["event_type"] == "RESEARCH_ENQUEUED":
            rows[payload["task_id"]] = {**payload, "status": "QUEUED"}
        elif event["event_type"] in {
            "IRIS_DISPATCH_RESERVED",
            "IRIS_IMPORT_RESERVED",
            "IRIS_LOCAL_IMPORT_RESERVED",
        }:
            rows[payload["task_id"]].update(payload, status="RUNNING")
        elif event["event_type"] == "IRIS_RESULT_RECORDED":
            rows[payload["task_id"]].update(payload)
    return list(rows.values())


def visible_results(store: Store, queue_id: str) -> list[dict[str, Any]]:
    return [
        {
            "task_id": t["task_id"],
            "experiment": store.artifacts.get(t["experiment_ref"]),
            "result_ref": t["result_ref"],
            "result": store.artifacts.get(t["result_ref"]),
        }
        for t in queue_tasks(store, queue_id)
        if t["status"] in TERMINAL
    ]


def queue_authorization(store: Store, queue_id: str) -> IrisAuthorization | None:
    spec = IrisQueueSpec.model_validate(store.manifest(queue_id)["spec"])
    recorded = [
        IrisAuthorization.model_validate(e["payload"]["authorization"])
        for e in store.events(queue_id)
        if e["event_type"] == "IRIS_AUTHORIZATION_RECORDED"
    ]
    if spec.authorization:
        recorded.append(spec.authorization)
    for prior in spec.prior_queues:
        if store.manifest(prior)["spec"]["environment"] == spec.environment.model_dump(mode="json"):
            value = queue_authorization(store, prior)
            if value:
                recorded.append(value)
    if len({digest(a) for a in recorded}) > 1:
        raise IntegrityError("conflicting submission grants across linked queues")
    return recorded[0] if recorded else None


class IrisResearchQueue:
    def __init__(
        self, store: Store, transport_factory: Callable[..., IrisTransport] = LocalIrisTransport
    ):
        self.store = store
        self.transport_factory = transport_factory

    def create(self, spec: IrisQueueSpec) -> str:
        verify_environment(spec.environment)
        for prior in spec.prior_queues:
            verify_queue(self.store, prior)
        transport = self.transport_factory(spec.environment)
        return self.store.create(
            {
                "kind": "IrisResearchQueue",
                "spec": spec.model_dump(mode="json"),
                "task": {"budget": spec.budget.model_dump()},
                "world_id": digest(spec),
                "source_identity": source_identity(),
                "code_provenance": code_provenance(),
                "transport": transport.identity(),
            }
        )

    def validate(self, queue_id: str) -> IrisQueueSpec:
        manifest = self.store.manifest(queue_id)
        if (
            manifest["kind"] != "IrisResearchQueue"
            or manifest["source_identity"] != source_identity()
        ):
            raise IntegrityError(
                "queue runtime changed; sealed queues remain available for offline audit"
            )
        spec = IrisQueueSpec.model_validate(manifest["spec"])
        verify_environment(spec.environment)
        if self.transport_factory(spec.environment).identity() != manifest["transport"]:
            raise IntegrityError("Iris transport identity changed")
        return spec

    def authorize(self, queue_id: str, authorization: IrisAuthorization) -> None:
        """Operator-only, single initial grant; never changes strategy/evidence."""
        with self.store.campaign_lock(queue_id):
            spec = self.validate(queue_id)
            existing = queue_authorization(self.store, queue_id)
            if existing:
                if existing == authorization:
                    return
                raise AdmissionError("submission grant is immutable once recorded")
            if authorization.environment_id != digest(
                spec.environment
            ) or authorization.expires_at <= datetime.now(UTC):
                raise AdmissionError(
                    "submission grant is expired or belongs to another environment"
                )
            if any(
                e["event_type"] == "IRIS_DISPATCH_RESERVED" for e in self.store.events(queue_id)
            ):
                raise IntegrityError("cannot retroactively authorize a submission")
            self.store.append(
                queue_id,
                "IRIS_AUTHORIZATION_RECORDED",
                {
                    "authorization": authorization.model_dump(mode="json"),
                    "origin": "explicit operator approval; unavailable to tool-free LLM",
                },
            )

    def enqueue(
        self, queue_id: str, experiment: IrisExperiment, *, allowed_evidence: set[str] | None = None
    ) -> str:
        with self.store.campaign_lock(queue_id):
            spec = self.validate(queue_id)
            rows = queue_tasks(self.store, queue_id)
            visible = {v["result_ref"] for v in visible_results(self.store, queue_id)}
            for prior in spec.prior_queues:
                visible.update(v["result_ref"] for v in visible_results(self.store, prior))
            if any(e["event_type"] == "HERMES_WORLD_SEALED" for e in self.store.events(queue_id)):
                raise AdmissionError("sealed epoch requires a new linked queue")
            allowed = visible if allowed_evidence is None else allowed_evidence & visible
            if not set(experiment.evidence_refs) <= allowed:
                raise AdmissionError("future or incompatible research evidence")
            text = render_candidate(spec.environment, experiment.settings)
            cpp_hash = digest({"cpp": text})
            for prior in spec.prior_queues:
                if self.store.manifest(prior)["spec"]["environment"] != spec.environment.model_dump(
                    mode="json"
                ):
                    continue
                for known in queue_tasks(self.store, prior):
                    if (
                        known["cpp_ref"] == cpp_hash
                        and known["status"] == "SUCCEEDED"
                        and experiment.family != "baseline"
                    ):
                        return str(known["task_id"])
            for row in rows:
                if row["cpp_ref"] == cpp_hash:
                    return str(row["task_id"])
            if len(rows) >= spec.max_tasks:
                raise AdmissionError("finite research queue capacity exhausted")
            if not rows and experiment.family != "baseline":
                raise AdmissionError("first queued task must reproduce the baseline")
            cpp_ref = self.store.artifacts.put({"cpp": text})
            task_id = "trial-" + digest({"world": digest(spec), "candidate": cpp_ref})[7:31]
            self.store.append(
                queue_id,
                "RESEARCH_ENQUEUED",
                {
                    "task_id": task_id,
                    "experiment_ref": self.store.artifacts.put(experiment),
                    "cpp_ref": cpp_ref,
                    "evidence_snapshot": sorted(allowed),
                    "preflight": "typed numeric/boolean changes inside declared regions only",
                },
            )
            return task_id

    def tick(
        self, queue_id: str, *, fault: str | None = None, selected_task: str | None = None
    ) -> dict[str, Any]:
        with self.store.campaign_lock(queue_id):
            spec = self.validate(queue_id)
            rows = queue_tasks(self.store, queue_id)
            if any(e["event_type"] == "HERMES_WORLD_SEALED" for e in self.store.events(queue_id)):
                return {"status": "SEALED"}
            active = [t for t in rows if t["status"] == "RUNNING"]
            transport = self.transport_factory(spec.environment)
            if active:
                row = active[0]
                job = next(j for j in self.store.jobs(queue_id) if j["id"] == row["job_id"])
                saved = self.store.read_receipt(job["id"])
                if saved is not None:
                    self._settle(queue_id, row, saved)
                    return {"status": "ADOPTED", "task_id": row["task_id"]}
                retained = self.store.artifacts.get(job["request_ref"])
                if retained.get("imported_from"):
                    self._finish_import(queue_id, row, job, retained)
                    return {"status": "ADOPTED", "task_id": row["task_id"]}
                if retained.get("local_import"):
                    return self._import_local(queue_id, row, spec, transport)
                if job["status"] == "QUEUED":
                    return self._submit(
                        queue_id,
                        row,
                        job["id"],
                        self.store.artifacts.get(job["request_ref"]),
                        transport,
                        fault,
                    )
                polls = [
                    e
                    for e in self.store.events(queue_id)
                    if e["event_type"] == "IRIS_COLLECTED"
                    and e["payload"]["task_id"] == row["task_id"]
                ]
                if len(polls) >= 128:
                    self.store.set_status(queue_id, "NEEDS_RECONCILIATION")
                    return {
                        "status": "NEEDS_RECONCILIATION",
                        "reason": "finite collection limit reached",
                        "request_id": row["request_id"],
                    }
                request = self.store.artifacts.get(job["request_ref"])
                started = time.monotonic()
                try:
                    result = transport.collect(request, self.store.root / "iris-jobs" / job["id"])
                except Exception as exc:
                    result = {
                        "status": "UNKNOWN",
                        "error_type": type(exc).__name__,
                        "request_id": row["request_id"],
                    }
                seconds = time.monotonic() - started
                self.store.append(
                    queue_id,
                    "IRIS_COLLECTED",
                    {
                        "task_id": row["task_id"],
                        "result_ref": self.store.artifacts.put(result),
                        "local_seconds": seconds,
                    },
                )
                if result.get("status") in {"PENDING", "UNKNOWN"}:
                    status = (
                        "WAITING_FOR_IRIS"
                        if result["status"] == "PENDING"
                        else "NEEDS_RECONCILIATION"
                    )
                    self.store.set_status(queue_id, status)
                    return {"status": status, "request_id": row["request_id"]}
                if (
                    result.get("request_id") != row["request_id"]
                    or result.get("status") not in TERMINAL
                ):
                    raise IntegrityError("unrecognized Iris collection response")
                if result["status"] == "SUCCEEDED":
                    try:
                        validate_iris_result(result, request, spec)
                    except (KeyError, ValueError, IntegrityError):
                        result = {
                            "status": "INVALID_RESULT",
                            "request_id": row["request_id"],
                            "invalid_result_ref": self.store.artifacts.put(result),
                            "reason": "result identity/accounting validation failed",
                        }
                poll_seconds = seconds + sum(e["payload"]["local_seconds"] for e in polls)
                submission = [
                    e
                    for e in self.store.events(queue_id)
                    if e["event_type"] == "IRIS_SUBMIT_RETURNED"
                    and e["payload"]["task_id"] == row["task_id"]
                ]
                duration = poll_seconds + sum(e["payload"]["local_seconds"] for e in submission)
                receipt = {
                    "job_id": job["id"],
                    "request_ref": job["request_ref"],
                    "world_id": self.store.manifest(queue_id)["world_id"],
                    "result_ref": self.store.artifacts.put(result),
                    "cost": Cost(
                        actions=1, tool_calls=1, compute_units=1, wall_seconds=duration
                    ).model_dump(),
                    "remote_usage": "one submission attempt; remote compute/queue time unavailable",
                    "request_id": row["request_id"],
                }
                self.store.write_receipt(job["id"], receipt)
                if fault == "after_receipt":
                    raise JarvisError("injected remote receipt interruption")
                self._settle(queue_id, row, receipt)
                return {
                    "status": result["status"],
                    "task_id": row["task_id"],
                    "request_id": row["request_id"],
                }
            if self.store.get_control(queue_id) in {"PAUSED", "CANCELLED"} or self.store.status(
                queue_id
            ) in {"PAUSED", "CANCELLED", "NEEDS_RECONCILIATION"}:
                return {"status": self.store.status(queue_id)}
            pending = [t for t in rows if t["status"] == "QUEUED"]
            if not pending:
                return {"status": "IDLE"}
            if rows[0]["status"] == "QUEUED" and spec.baseline is not None:
                return self._import_local(queue_id, rows[0], spec, transport)
            if rows[0]["status"] not in {"SUCCEEDED", "QUEUED"}:
                return {"status": "BASELINE_NOT_VERIFIED"}
            auth = queue_authorization(self.store, queue_id)
            if (
                auth is None
                or auth.environment_id != digest(spec.environment)
                or auth.expires_at <= datetime.now(UTC)
            ):
                return {
                    "status": "AWAITING_SUBMISSION_AUTHORIZATION",
                    "prepared_tasks": len(pending),
                }
            dispatched = [
                e
                for e in self.store.events(queue_id)
                if e["event_type"] == "IRIS_DISPATCH_RESERVED"
            ]
            if len(dispatched) >= auth.max_submissions:
                return {"status": "SUBMISSION_LIMIT"}
            chosen = (
                next((t for t in pending if t["task_id"] == selected_task), None)
                if selected_task
                else pending[0]
            )
            if chosen is None or (
                rows[0]["status"] != "SUCCEEDED" and chosen["task_id"] != rows[0]["task_id"]
            ):
                raise AdmissionError("selected task is not eligible before the baseline")
            row = chosen
            text = self.store.artifacts.get(row["cpp_ref"])["cpp"]
            folder = self.store.root / "private-candidates"
            folder.mkdir(exist_ok=True, mode=0o700)
            candidate = folder / (row["task_id"] + ".txt")
            if candidate.exists() and candidate.read_text() != text:
                raise IntegrityError("private candidate mutated")
            if not candidate.exists():
                candidate.write_text(text)
            candidate.chmod(0o400)
            request = {
                "task_id": row["task_id"],
                "request_id": secrets.token_hex(4),
                "candidate_path": str(candidate),
                "candidate_sha256": file_digest(candidate),
                "period": spec.environment.period.request_period,
                "world_id": self.store.manifest(queue_id)["world_id"],
                "authorization_ref": self.store.artifacts.put(auth),
            }
            orphan = [
                j
                for j in self.store.jobs(queue_id)
                if self.store.artifacts.get(j["request_ref"]).get("task_id") == row["task_id"]
            ]
            if orphan:
                if len(orphan) != 1 or orphan[0]["status"] != "QUEUED":
                    raise IntegrityError("unmapped started operation requires reconciliation")
                jid = orphan[0]["id"]
                request = self.store.artifacts.get(orphan[0]["request_ref"])
            else:
                # One finite authorization is shared across linked epoch queues.
                grant = digest(auth)
                with self.store.transaction() as conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS iris_grant_slots (grant_id TEXT, task_id TEXT, PRIMARY KEY(grant_id,task_id))"
                    )
                    existing = conn.execute(
                        "SELECT 1 FROM iris_grant_slots WHERE grant_id=? AND task_id=?",
                        (grant, row["task_id"]),
                    ).fetchone()
                    count = conn.execute(
                        "SELECT count(*) FROM iris_grant_slots WHERE grant_id=?", (grant,)
                    ).fetchone()[0]
                    if not existing and count >= auth.max_submissions:
                        return {"status": "SUBMISSION_LIMIT"}
                    conn.execute(
                        "INSERT OR IGNORE INTO iris_grant_slots VALUES (?,?)",
                        (grant, row["task_id"]),
                    )
                jid = self.store.reserve(
                    queue_id,
                    request,
                    Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=180),
                )
            if fault == "after_reserve":
                raise JarvisError("injected reservation interruption")
            self.store.append(
                queue_id,
                "IRIS_DISPATCH_RESERVED",
                {"task_id": row["task_id"], "job_id": jid, "request_id": request["request_id"]},
            )
            return self._submit(queue_id, row, jid, request, transport, fault)

    def _import_local(
        self, queue_id: str, row: dict[str, Any], spec: IrisQueueSpec, transport: IrisTransport
    ) -> dict[str, Any]:
        """Read-only reconstruction of an already accepted result; no upload/config."""
        from pathlib import Path

        proof = spec.baseline
        if proof is None or spec.environment.result_origin != "OBSERVED_IRIS_BACKTEST":
            raise AdmissionError("observed local baseline proof required")
        if (
            proof.manifest not in spec.environment.accepted_inputs
            or file_digest(Path(proof.raw)) != proof.raw_sha256
        ):
            raise IntegrityError("baseline proof is not pinned to the accepted package")
        case = next(
            c
            for c in json.loads(Path(proof.manifest).read_text())["cases"]
            if c["label"] == proof.label
        )
        request = {
            "task_id": row["task_id"],
            "request_id": case["request_id"],
            "candidate_sha256": spec.environment.template_sha256,
            "period": spec.environment.period.request_period,
            "world_id": self.store.manifest(queue_id)["world_id"],
            "local_import": proof.model_dump(mode="json"),
            "baseline_case": case,
        }
        retained = [j for j in self.store.jobs(queue_id) if j["request_ref"] == digest(request)]
        jid = (
            retained[0]["id"]
            if retained
            else self.store.reserve(
                queue_id, request, Cost(actions=1, compute_units=1, wall_seconds=180)
            )
        )
        if row["status"] == "QUEUED":
            self.store.append(
                queue_id,
                "IRIS_LOCAL_IMPORT_RESERVED",
                {"task_id": row["task_id"], "job_id": jid, "request_id": request["request_id"]},
            )
        job = next(j for j in self.store.jobs(queue_id) if j["id"] == jid)
        if job["status"] == "QUEUED":
            self.store.start_job(jid)
        started = time.monotonic()
        result = transport.import_baseline(request, self.store.root / "iris-jobs" / jid)
        if result.get("status") == "UNKNOWN":
            self.store.set_status(queue_id, "NEEDS_RECONCILIATION")
            return {
                "status": "NEEDS_RECONCILIATION",
                "request_id": request["request_id"],
                "reason": "local baseline evaluation incomplete; no remote operation",
            }
        validate_iris_result(result, request, spec)
        if (
            result["status"] != "SUCCEEDED"
            or result["provenance"]["raw_sha256"] != proof.raw_sha256
        ):
            raise IntegrityError("local baseline verification failed")
        receipt = {
            "job_id": jid,
            "request_ref": digest(request),
            "world_id": request["world_id"],
            "result_ref": self.store.artifacts.put(result),
            "cost": Cost(
                actions=1, compute_units=1, wall_seconds=time.monotonic() - started
            ).model_dump(),
            "origin": "read-only accepted baseline reconstruction; zero new remote submissions",
        }
        self.store.write_receipt(jid, receipt)
        self._settle(queue_id, {**row, "job_id": jid}, receipt)
        return {
            "status": "SUCCEEDED",
            "task_id": row["task_id"],
            "baseline_reused": True,
            "remote_submissions": 0,
        }

    def adopt_baseline(self, queue_id: str, prior_queue: str) -> None:
        """Reuse the exact same verified baseline; no second remote submission."""
        with self.store.campaign_lock(queue_id):
            spec = self.validate(queue_id)
            if prior_queue not in spec.prior_queues:
                raise AdmissionError("baseline source is not an admitted prior queue")
            previous = self.store.manifest(prior_queue)["spec"]["environment"]
            if previous != spec.environment.model_dump(mode="json"):
                raise IntegrityError(
                    "baseline reuse requires identical source, period and controls"
                )
            verify_queue(self.store, prior_queue)
            source = queue_tasks(self.store, prior_queue)[0]
            target = queue_tasks(self.store, queue_id)[0]
            if source["status"] != "SUCCEEDED":
                raise AdmissionError("prior baseline is not verified")
            if target["status"] != "QUEUED":
                return
            source_job = next(
                j for j in self.store.jobs(prior_queue) if j["id"] == source["job_id"]
            )
            original = self.store.artifacts.get(source_job["request_ref"])
            request = {
                **original,
                "task_id": target["task_id"],
                "world_id": self.store.manifest(queue_id)["world_id"],
                "imported_from": source["result_ref"],
            }
            retained = [
                j
                for j in self.store.jobs(queue_id)
                if self.store.artifacts.get(j["request_ref"]) == request
            ]
            jid = (
                retained[0]["id"]
                if retained
                else self.store.reserve(queue_id, request, Cost(actions=1))
            )
            self.store.append(
                queue_id,
                "IRIS_IMPORT_RESERVED",
                {"task_id": target["task_id"], "job_id": jid, "request_id": original["request_id"]},
            )
            job = next(j for j in self.store.jobs(queue_id) if j["id"] == jid)
            self._finish_import(queue_id, {**target, "job_id": jid}, job, request)

    def _finish_import(
        self, queue_id: str, row: dict[str, Any], job: dict[str, Any], request: dict[str, Any]
    ) -> None:
        if job["status"] == "QUEUED":
            self.store.start_job(job["id"])
        receipt = {
            "job_id": job["id"],
            "request_ref": digest(request),
            "world_id": request["world_id"],
            "result_ref": request["imported_from"],
            "cost": Cost(actions=1).model_dump(),
            "origin": "verified prior baseline reuse; no new remote execution",
        }
        self.store.write_receipt(job["id"], receipt)
        self._settle(queue_id, row, receipt)

    def _submit(
        self,
        queue_id: str,
        row: dict[str, Any],
        jid: str,
        request: dict[str, Any],
        transport: IrisTransport,
        fault: str | None,
    ) -> dict[str, Any]:
        self.store.start_job(jid)
        if fault == "after_start":
            raise JarvisError("injected uncertain submission; collect this same ID")
        started = time.monotonic()
        try:
            response = transport.submit(request, self.store.root / "iris-jobs" / jid)
        except Exception as exc:
            response = {
                "status": "UNKNOWN",
                "error_type": type(exc).__name__,
                "request_id": request["request_id"],
            }
        self.store.append(
            queue_id,
            "IRIS_SUBMIT_RETURNED",
            {
                "task_id": row["task_id"],
                "response_ref": self.store.artifacts.put(response),
                "local_seconds": time.monotonic() - started,
            },
        )
        self.store.set_status(
            queue_id,
            "WAITING_FOR_IRIS" if response.get("status") == "SUBMITTED" else "NEEDS_RECONCILIATION",
        )
        return {"status": self.store.status(queue_id), "request_id": request["request_id"]}

    def _settle(self, queue_id: str, row: dict[str, Any], receipt: dict[str, Any]) -> None:
        result = self.store.artifacts.get(receipt["result_ref"])
        self.store.finish_auxiliary_job(
            row["job_id"],
            "IRIS_RESULT_RECORDED",
            {
                "task_id": row["task_id"],
                "status": result["status"],
                "result_ref": receipt["result_ref"],
            },
        )
        self.store.set_status(queue_id, "READY" if result["status"] == "SUCCEEDED" else "PAUSED")


def validate_iris_result(
    result: dict[str, Any], request: dict[str, Any], spec: IrisQueueSpec
) -> None:
    import math

    if (
        result["candidate_sha256"] != request["candidate_sha256"]
        or not result["commit"]
        or result["result_origin"] != spec.environment.result_origin
        or result["provenance"]["calendar"]
        != [str(d) for d in spec.environment.period.trading_dates]
        or result["provenance"]["evaluator_sha256"] != spec.environment.evaluator_sha256
        or sorted(result["daily_pl"]) != [str(d) for d in spec.environment.period.trading_dates]
    ):
        raise IntegrityError("Iris candidate/calendar/evaluator identity mismatch")
    for key in ["entry_only_pl", "mdd", "worst_day", "count"]:
        if type(result[key]) not in {int, float} or not math.isfinite(result[key]):
            raise IntegrityError("nonfinite or invalid entry-only metric")
    if (
        type(result["count"]) is not int
        or result["count"] < 0
        or not isinstance(result.get("risk"), dict)
    ):
        raise IntegrityError("invalid count or missing risk vector")
    daily = [result["daily_pl"][str(d)] for d in spec.environment.period.trading_dates]
    if not all(type(x) in {int, float} and math.isfinite(x) for x in daily):
        raise IntegrityError("invalid daily metrics")
    equity = peak = mdd = 0.0
    for x in daily:
        equity += x
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    if (
        not math.isclose(sum(daily), result["entry_only_pl"], abs_tol=1e-8)
        or not math.isclose(mdd, result["mdd"], abs_tol=1e-8)
        or not math.isclose(min(daily), result["worst_day"], abs_tol=1e-8)
    ):
        raise IntegrityError("entry-only accounting mismatch")


def verify_queue(store: Store, queue_id: str) -> dict[str, Any]:
    """Offline journal/receipt audit; never opens upstream data or network."""
    manifest = store.manifest(queue_id)
    spec = IrisQueueSpec.model_validate(manifest["spec"])
    if manifest["world_id"] != digest(spec):
        raise IntegrityError("queue world changed")
    jobs = {j["id"]: j for j in store.jobs(queue_id)}
    events = store.events(queue_id)
    rows = queue_tasks(store, queue_id)
    dispatched: set[str] = set()
    external: set[str] = set()
    import hashlib

    visible: set[str] = set()
    for prior in spec.prior_queues:
        if store.manifest(prior)["created_at"] >= manifest["created_at"]:
            raise IntegrityError("prior queue is not in the past")
        visible.update(
            e["payload"]["result_ref"]
            for e in store.events(prior)
            if e["event_type"] == "IRIS_RESULT_RECORDED"
            and e["recorded_at_utc"] <= manifest["created_at"]
        )
    for event in events:
        p = event["payload"]
        if event["event_type"] == "RESEARCH_ENQUEUED":
            experiment = IrisExperiment.model_validate(store.artifacts.get(p["experiment_ref"]))
            store.artifacts.get(p["cpp_ref"])
            if not set(p["evidence_snapshot"]) <= visible or not set(
                experiment.evidence_refs
            ) <= set(p["evidence_snapshot"]):
                raise IntegrityError("queued proposal contains future evidence")
        if event["event_type"] == "IRIS_DISPATCH_RESERVED":
            if p["task_id"] in dispatched or p["request_id"] in external:
                raise IntegrityError("duplicate task or external submission")
            dispatched.add(p["task_id"])
            external.add(p["request_id"])
            request = store.artifacts.get(jobs[p["job_id"]]["request_ref"])
            row = next(t for t in rows if t["task_id"] == p["task_id"])
            text = store.artifacts.get(row["cpp_ref"])["cpp"]
            if (
                request["request_id"] != p["request_id"]
                or request["world_id"] != manifest["world_id"]
                or request["candidate_sha256"]
                != "sha256:" + hashlib.sha256(text.encode()).hexdigest()
                or request.get("authorization_ref") != digest(queue_authorization(store, queue_id))
            ):
                raise IntegrityError("external request lineage changed")
        if event["event_type"] == "IRIS_RESULT_RECORDED":
            job = jobs[p["job_id"]]
            receipt = store.artifacts.get(p["receipt_ref"])
            result = store.artifacts.get(p["result_ref"])
            request = store.artifacts.get(job["request_ref"])
            if (
                not job["finalized"]
                or receipt["result_ref"] != p["result_ref"]
                or receipt["job_id"] != job["id"]
                or receipt["request_ref"] != job["request_ref"]
                or receipt["world_id"] != manifest["world_id"]
                or Cost.model_validate_json(job["actual"]) != Cost.model_validate(receipt["cost"])
            ):
                raise IntegrityError("remote receipt/cost mismatch")
            if result["status"] == "SUCCEEDED":
                validate_iris_result(result, request, spec)
                if request.get("imported_from"):
                    sources = [
                        t
                        for q in spec.prior_queues
                        if store.manifest(q)["spec"]["environment"]
                        == spec.environment.model_dump(mode="json")
                        for t in queue_tasks(store, q)[:1]
                        if t.get("result_ref") == request["imported_from"]
                        and t["status"] == "SUCCEEDED"
                    ]
                    if (
                        len(sources) < 1
                        or p["result_ref"] != request["imported_from"]
                        or Cost.model_validate(receipt["cost"]).tool_calls != 0
                    ):
                        raise IntegrityError(
                            "baseline reuse lacks identical prior verified evidence"
                        )
                if request.get("local_import"):
                    proof = request["local_import"]
                    case = request["baseline_case"]
                    if (
                        spec.baseline is None
                        or proof != spec.baseline.model_dump(mode="json")
                        or case["status"] != "completed"
                        or case["job"]["sha"] != result["commit"]
                        or "sha256:" + case["sha256"] != spec.environment.template_sha256
                        or result["provenance"]["raw_sha256"] != proof["raw_sha256"]
                        or Cost.model_validate(receipt["cost"]).tool_calls != 0
                    ):
                        raise IntegrityError("local baseline provenance changed")
            visible.add(p["result_ref"])
    auth = queue_authorization(store, queue_id)
    if len(dispatched) > (auth.max_submissions if auth else 0):
        raise IntegrityError("queue exceeded owner submission authorization")
    return {
        "status": "VERIFIED",
        "queue_id": queue_id,
        "tasks": len(rows),
        "submissions": len(dispatched),
        "completed": sum(t["status"] in TERMINAL for t in rows),
        "resources": store.resource_totals(queue_id),
        "external_calls": 0,
    }
