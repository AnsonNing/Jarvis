"""Shared, conservative Iris quota accounting from timestamped acknowledgements."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.contracts import AdmissionError, IntegrityError, Record
from jarvis.identity import digest
from jarvis.storage import Store


class IrisQuotaSnapshot(Record):
    kind: Literal["IrisQuotaSnapshot"] = "IrisQuotaSnapshot"
    used: int = Field(ge=0, strict=True)
    limit: int = Field(ge=1, strict=True)
    ack_created_at: datetime
    request_id: str = Field(pattern=r"^[a-f0-9]{8}$")
    source: Literal["IRIS_QUEUE_ACK", "SIMULATED_FIXTURE"] = "IRIS_QUEUE_ACK"
    authoritative_current: Literal[False] = False

    @model_validator(mode="after")
    def valid_snapshot(self) -> "IrisQuotaSnapshot":
        if self.ack_created_at.tzinfo is None or self.used > self.limit:
            raise ValueError("invalid quota snapshot")
        return self


class IrisQuotaPolicy(Record):
    scope: str = Field(min_length=1)
    reserve: int = Field(default=1, ge=0, le=20, strict=True)
    max_pending: int = Field(default=1, ge=1, le=2, strict=True)
    max_snapshot_age_seconds: int = Field(default=86400, ge=60, le=86400, strict=True)


class QuotaLedger:
    def __init__(self, store: Store, owner: str, policy: IrisQuotaPolicy):
        self.store, self.owner, self.policy = store, owner, policy
        with store.transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS iris_quota_snapshots (scope TEXT, ref TEXT, created REAL, used INTEGER, covered INTEGER, PRIMARY KEY(scope,ref))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS iris_quota_attempts (sequence INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, owner TEXT, task_id TEXT, request_id TEXT UNIQUE, candidate_key TEXT, status TEXT, UNIQUE(scope,candidate_key))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS iris_quota_blocks (scope TEXT PRIMARY KEY, recorded REAL)"
            )

    def observe(self, snapshot: IrisQuotaSnapshot) -> None:
        if snapshot.ack_created_at.timestamp() > datetime.now(UTC).timestamp() + 300:
            raise AdmissionError("quota acknowledgement is implausibly in the future")
        ref = self.store.artifacts.put(snapshot)
        with self.store.transaction() as conn:
            prior = conn.execute(
                "SELECT * FROM iris_quota_snapshots WHERE scope=? ORDER BY created DESC,used DESC,covered DESC LIMIT 1",
                (self.policy.scope,),
            ).fetchone()
            if prior and prior["created"] > snapshot.ack_created_at.timestamp():
                return
            own = conn.execute(
                "SELECT sequence FROM iris_quota_attempts WHERE scope=? AND request_id=?",
                (self.policy.scope, snapshot.request_id),
            ).fetchone()
            covered = max(prior["covered"] if prior else 0, own["sequence"] if own else 0)
            existing = conn.execute(
                "SELECT 1 FROM iris_quota_snapshots WHERE scope=? AND ref=?",
                (self.policy.scope, ref),
            ).fetchone()
            if existing:
                return
            conn.execute(
                "INSERT INTO iris_quota_snapshots VALUES (?,?,?,?,?)",
                (
                    self.policy.scope,
                    ref,
                    snapshot.ack_created_at.timestamp(),
                    snapshot.used,
                    covered,
                ),
            )
            self.store._append(
                conn,
                self.owner,
                "IRIS_QUOTA_OBSERVED",
                {"snapshot_ref": ref, "covered_through": covered},
            )

    def status(self) -> dict[str, Any]:
        with self.store.transaction() as conn:
            return self._status(conn)

    def _status(self, conn: Any) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM iris_quota_snapshots WHERE scope=? ORDER BY created DESC,used DESC,covered DESC LIMIT 1",
            (self.policy.scope,),
        ).fetchone()
        active = conn.execute(
            "SELECT count(*) FROM iris_quota_attempts WHERE scope=? AND status='ACTIVE'",
            (self.policy.scope,),
        ).fetchone()[0]
        if row is None:
            return {"status": "WAITING_FOR_QUOTA", "active": active, "remaining_conservative": 0}
        snapshot = IrisQuotaSnapshot.model_validate(self.store.artifacts.get(row["ref"]))
        uncovered = conn.execute(
            "SELECT count(*) FROM iris_quota_attempts WHERE scope=? AND sequence>?",
            (self.policy.scope, row["covered"]),
        ).fetchone()[0]
        remaining = max(0, snapshot.limit - snapshot.used - uncovered - self.policy.reserve)
        block = conn.execute(
            "SELECT recorded FROM iris_quota_blocks WHERE scope=?", (self.policy.scope,)
        ).fetchone()
        age = datetime.now(UTC).timestamp() - row["created"]
        status = "AVAILABLE"
        if age > self.policy.max_snapshot_age_seconds or (
            block and row["created"] <= block["recorded"]
        ):
            status = "QUOTA_REFRESH_REQUIRED"
        elif active >= self.policy.max_pending:
            status = "WAITING_IRIS_SLOT"
        elif remaining == 0:
            status = "WAITING_FOR_QUOTA"
        return {
            "status": status,
            "snapshot": snapshot.model_dump(mode="json"),
            "snapshot_ref": row["ref"],
            "remaining_conservative": remaining,
            "active": active,
            "uncovered_attempts": uncovered,
            "snapshot_age_seconds": max(0, age),
            "scope": self.policy.scope,
        }

    def reserve(self, task_id: str, request_id: str, candidate_key: str) -> dict[str, Any]:
        with self.store.transaction() as conn:
            prior = conn.execute(
                "SELECT * FROM iris_quota_attempts WHERE scope=? AND candidate_key=?",
                (self.policy.scope, candidate_key),
            ).fetchone()
            if prior:
                if prior["task_id"] == task_id and prior["request_id"] == request_id:
                    return {"status": "RESERVED", "sequence": prior["sequence"]}
                raise AdmissionError(
                    "identical source and period already attempted; no unchanged resubmission"
                )
            status = self._status(conn)
            if status["status"] != "AVAILABLE":
                return status
            inserted = conn.execute(
                "INSERT INTO iris_quota_attempts(scope,owner,task_id,request_id,candidate_key,status) VALUES (?,?,?,?,?,'ACTIVE')",
                (self.policy.scope, self.owner, task_id, request_id, candidate_key),
            )
            self.store._append(
                conn,
                self.owner,
                "IRIS_QUOTA_RESERVED",
                {
                    "task_id": task_id,
                    "request_id": request_id,
                    "candidate_key": candidate_key,
                    "snapshot_ref": status["snapshot_ref"],
                    "quota_sequence": inserted.lastrowid,
                },
            )
        return {"status": "RESERVED"}

    def completed(
        self, request_id: str, *, quota_refused: bool = False, result_ref: str | None = None
    ) -> None:
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT status FROM iris_quota_attempts WHERE scope=? AND request_id=?",
                (self.policy.scope, request_id),
            ).fetchone()
            if row is None or row["status"] == "SETTLED":
                return
            conn.execute(
                "UPDATE iris_quota_attempts SET status='SETTLED' WHERE scope=? AND request_id=?",
                (self.policy.scope, request_id),
            )
            self.store._append(
                conn,
                self.owner,
                "IRIS_QUOTA_SETTLED",
                {
                    "request_id": request_id,
                    "quota_refused": quota_refused,
                    "result_ref": result_ref,
                },
            )
            if quota_refused:
                conn.execute(
                    "INSERT INTO iris_quota_blocks VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET recorded=excluded.recorded",
                    (self.policy.scope, datetime.now(UTC).timestamp()),
                )

    def attempted(self, candidate_key: str) -> bool:
        with self.store.transaction() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM iris_quota_attempts WHERE scope=? AND candidate_key=?",
                    (self.policy.scope, candidate_key),
                ).fetchone()
                is not None
            )

    def request_for_task(self, task_id: str) -> str | None:
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT request_id FROM iris_quota_attempts WHERE scope=? AND task_id=?",
                (self.policy.scope, task_id),
            ).fetchone()
            return str(row["request_id"]) if row else None


def verify_quota(store: Store, owner: str, policy: IrisQuotaPolicy) -> dict[str, Any]:
    """Rebuild shared allowance and slots from immutable journals, never Iris."""
    parents = {}
    with store.connect() as conn:
        identifiers = [r[0] for r in conn.execute("SELECT id FROM campaigns")]
    for identifier in identifiers:
        manifest = store.manifest(identifier)
        if (
            manifest.get("kind") == "IrisContinuousStudy"
            and manifest["spec"]["quota_policy"]["scope"] == policy.scope
        ):
            parents[identifier] = IrisQuotaPolicy.model_validate(manifest["spec"]["quota_policy"])
    events = [
        (e, pid)
        for pid in parents
        for e in store.events(pid)
        if e["event_type"] in {"IRIS_QUOTA_OBSERVED", "IRIS_QUOTA_RESERVED", "IRIS_QUOTA_SETTLED"}
    ]
    events.sort(key=lambda item: (item[0]["recorded_at_utc"], item[1], item[0]["sequence"]))
    snapshots: dict[str, tuple[IrisQuotaSnapshot, int]] = {}
    attempts: dict[str, dict[str, Any]] = {}
    blocked: float | None = None
    for event, pid in events:
        p = event["payload"]
        if event["event_type"] == "IRIS_QUOTA_OBSERVED":
            snap = IrisQuotaSnapshot.model_validate(store.artifacts.get(p["snapshot_ref"]))
            previous = max((v[1] for v in snapshots.values()), default=0)
            own = attempts.get(snap.request_id)
            expected = max(previous, own["sequence"] if own else 0)
            if p["covered_through"] != expected:
                raise IntegrityError("quota observation claimed unacknowledged attempts")
            snapshots[p["snapshot_ref"]] = (snap, expected)
        elif event["event_type"] == "IRIS_QUOTA_RESERVED":
            if p["request_id"] in attempts or p["snapshot_ref"] not in snapshots:
                raise IntegrityError("duplicate or unobserved quota reservation")
            snap, covered = snapshots[p["snapshot_ref"]]
            active = sum(t["status"] == "ACTIVE" for t in attempts.values())
            unaccounted = sum(t["sequence"] > covered for t in attempts.values())
            age = (
                datetime.fromisoformat(event["recorded_at_utc"]).timestamp()
                - snap.ack_created_at.timestamp()
            )
            local_policy = parents[pid]
            if (
                active >= local_policy.max_pending
                or snap.limit - snap.used - unaccounted - local_policy.reserve < 1
                or age > local_policy.max_snapshot_age_seconds
                or (blocked and snap.ack_created_at.timestamp() <= blocked)
            ):
                raise IntegrityError("dispatch exceeded observed quota or active-slot policy")
            if any(t["candidate_key"] == p["candidate_key"] for t in attempts.values()):
                raise IntegrityError("unchanged candidate was resubmitted")
            attempts[p["request_id"]] = {
                "request_id": p["request_id"],
                "task_id": p["task_id"],
                "candidate_key": p["candidate_key"],
                "sequence": p["quota_sequence"],
                "status": "ACTIVE",
            }
        else:
            if p["request_id"] not in attempts:
                raise IntegrityError("quota settlement has no reserved request")
            if p["result_ref"]:
                result = store.artifacts.get(p["result_ref"])
                if result["status"] not in {
                    "SUCCEEDED",
                    "FAILED",
                    "REJECTED",
                    "INVALID_RESULT",
                } or bool(p["quota_refused"]) != (result.get("reason") == "quota_exhausted"):
                    raise IntegrityError("quota slot released without a terminal receipt")
            attempts[p["request_id"]]["status"] = "SETTLED"
            if p["quota_refused"]:
                blocked = datetime.fromisoformat(event["recorded_at_utc"]).timestamp()
    with store.connect() as conn:
        actual = {
            r["request_id"]: {
                k: r[k] for k in ["request_id", "task_id", "candidate_key", "sequence", "status"]
            }
            for r in conn.execute(
                "SELECT * FROM iris_quota_attempts WHERE scope=?", (policy.scope,)
            )
        }
        refs = {
            r[0]
            for r in conn.execute(
                "SELECT ref FROM iris_quota_snapshots WHERE scope=?", (policy.scope,)
            )
        }
    if actual != attempts or refs != set(snapshots):
        raise IntegrityError("quota accounting tables differ from immutable evidence")
    return {
        "status": "VERIFIED",
        "attempts": len(attempts),
        "active": sum(t["status"] == "ACTIVE" for t in attempts.values()),
        "snapshots": len(snapshots),
        "external_calls": 0,
    }


def operating_ledger(store: Store, queue_spec: Any) -> QuotaLedger | None:
    owner = queue_spec.operating_owner
    if owner is None:
        return None
    manifest = store.manifest(owner)
    if manifest["kind"] != "IrisContinuousStudy" or not manifest["spec"]["owner_instruction"]:
        raise IntegrityError("standing research authorization is absent")
    environments = [
        q["environment"] for t in manifest["spec"]["tracks"] for q in [t["smoke"], t["development"]]
    ]
    if digest(queue_spec.environment) not in {digest(e) for e in environments}:
        raise IntegrityError("queue escaped standing research environment scope")
    return QuotaLedger(
        store, owner, IrisQuotaPolicy.model_validate(manifest["spec"]["quota_policy"])
    )


def candidate_key(spec: Any, cpp_ref: str) -> str:
    return digest(
        {
            "branch": spec.environment.branch,
            "dates": [str(d) for d in spec.environment.period.trading_dates],
            "cpp_ref": cpp_ref,
        }
    )
