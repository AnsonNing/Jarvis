"""Single-host append-only evidence, transactional budgets and durable job receipts."""

import contextlib
import fcntl
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.contracts import Budget, BudgetExceeded, Cost, IntegrityError, JarvisError
from jarvis.identity import canonical, digest, raw_digest


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, ref: str) -> Path:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", ref):
            raise IntegrityError("invalid artifact reference")
        return self.root / ref[7:9] / ref[7:]

    def put(self, value: Any) -> str:
        data = canonical(value)
        ref = digest(value)
        path = self.path(ref)
        path.parent.mkdir(exist_ok=True, mode=0o700)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
        try:
            with temp.open("xb") as f:
                os.chmod(temp, 0o600)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(temp, path)
                sync_directory(path.parent)
            except FileExistsError:
                pass
        finally:
            temp.unlink(missing_ok=True)
        self.get(ref)
        return ref

    def get(self, ref: str) -> Any:
        try:
            data = self.path(ref).read_bytes()
            value = json.loads(data)
        except (FileNotFoundError, ValueError) as exc:
            raise IntegrityError(f"missing or invalid artifact {ref}") from exc
        if canonical(value) != data or digest(value) != ref:
            raise IntegrityError(f"artifact hash mismatch: {ref}")
        return value


class BlobStore:
    """Content-addressed raw bytes (tables, model files, source); hashed as captured."""

    limit = 256 * 1024 * 1024

    def __init__(self, root: Path):
        self.root = root

    def path(self, ref: str) -> Path:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", ref):
            raise IntegrityError("invalid blob reference")
        return self.root / ref[7:9] / ref[7:]

    def put(self, data: bytes) -> str:
        if len(data) > self.limit:
            raise IntegrityError("blob exceeds admitted size")
        ref = raw_digest(data)
        path = self.path(ref)
        if path.exists():
            self.get(ref)
            return ref
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
        try:
            with temp.open("xb") as f:
                os.chmod(temp, 0o600)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(temp, path)
                sync_directory(path.parent)
            except FileExistsError:
                pass
        finally:
            temp.unlink(missing_ok=True)
        return ref

    def put_file(self, path: Path) -> str:
        with path.open("rb") as stream:
            data = stream.read(self.limit + 1)
        return self.put(data)

    def get(self, ref: str) -> bytes:
        try:
            data = self.path(ref).read_bytes()
        except FileNotFoundError as exc:
            raise IntegrityError(f"missing blob {ref}") from exc
        if raw_digest(data) != ref:
            raise IntegrityError(f"blob hash mismatch: {ref}")
        return data


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifacts = ArtifactStore(self.root / "artifacts")
        self.blobs = BlobStore(self.root / "blobs")
        self.db_path = self.root / "journal.sqlite3"
        with self.bootstrap_lock(), contextlib.closing(self.connect()) as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY, manifest_ref TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                    sequence INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, sequence)
                );
                CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                  BEGIN SELECT RAISE(ABORT, 'append-only journal'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                  BEGIN SELECT RAISE(ABORT, 'append-only journal'); END;
                CREATE TRIGGER IF NOT EXISTS campaigns_no_update BEFORE UPDATE ON campaigns
                  BEGIN SELECT RAISE(ABORT, 'sealed campaign'); END;
                CREATE TRIGGER IF NOT EXISTS campaigns_no_delete BEFORE DELETE ON campaigns
                  BEGIN SELECT RAISE(ABORT, 'sealed campaign'); END;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                    request_ref TEXT NOT NULL,
                    reservation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    actual TEXT,
                    receipt_ref TEXT,
                    finalized INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS controls (
                    campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id),
                    command TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS budget_links (
                    member TEXT PRIMARY KEY REFERENCES campaigns(id),
                    owner TEXT NOT NULL REFERENCES campaigns(id)
                );
                CREATE TRIGGER IF NOT EXISTS budget_links_no_update BEFORE UPDATE ON budget_links
                  BEGIN SELECT RAISE(ABORT, 'sealed budget lineage'); END;
                CREATE TRIGGER IF NOT EXISTS budget_links_no_delete BEFORE DELETE ON budget_links
                  BEGIN SELECT RAISE(ABORT, 'sealed budget lineage'); END;
                CREATE TABLE IF NOT EXISTS policy_family_use (
                    family_key TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL,
                    owner_id TEXT NOT NULL REFERENCES campaigns(id)
                );
            """)
        os.chmod(self.db_path, 0o600)

    @contextlib.contextmanager
    def bootstrap_lock(self) -> Iterator[None]:
        """Serialize first WAL/schema setup across newly started processes."""
        with (self.root / ".bootstrap.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextlib.contextmanager
    def campaign_lock(
        self, campaign_id: str, *, scope: str | None = None, blocking: bool = False
    ) -> Iterator[None]:
        self.manifest(campaign_id)
        if scope is not None and not re.fullmatch(r"[a-z-]+", scope):
            raise JarvisError("invalid coordinator lock scope")
        locks = self.root / "locks"
        locks.mkdir(exist_ok=True, mode=0o700)
        suffix = "-" + scope if scope else ""
        with (locks / f"{campaign_id}{suffix}.lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise JarvisError("campaign already has an active coordinator") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def create(self, manifest: dict[str, Any]) -> str:
        campaign_id = "camp-" + uuid.uuid4().hex
        manifest = {**manifest, "campaign_id": campaign_id, "created_at": utcnow()}
        ref = self.artifacts.put(manifest)
        with self.transaction() as conn:
            conn.execute("INSERT INTO campaigns VALUES (?, ?)", (campaign_id, ref))
            if manifest.get("budget_owner"):
                conn.execute(
                    "INSERT INTO budget_links VALUES (?,?)", (campaign_id, manifest["budget_owner"])
                )
            self._append(conn, campaign_id, "CREATED", {"manifest_ref": ref})
            self._append(conn, campaign_id, "STATUS", {"status": "READY"})
        return campaign_id

    def manifest(self, campaign_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise JarvisError("unknown campaign ID")
        return self.artifacts.get(row["manifest_ref"])

    def _append(
        self, conn: sqlite3.Connection, campaign_id: str, event_type: str, payload: Any
    ) -> dict[str, Any]:
        last = conn.execute(
            "SELECT event_json FROM events WHERE campaign_id=? ORDER BY sequence DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        previous = json.loads(last[0]) if last else None
        event = {
            "schema_version": 1,
            "kind": "JarvisEvent",
            "campaign_id": campaign_id,
            "sequence": previous["sequence"] + 1 if previous else 1,
            "event_type": event_type,
            "actor": "trusted-kernel",
            "recorded_at_utc": utcnow(),
            "payload_ref": self.artifacts.put(payload),
            "previous_event_hash": previous["event_hash"] if previous else None,
        }
        event["event_hash"] = digest(event)
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?)",
            (campaign_id, event["sequence"], canonical(event).decode()),
        )
        return event

    def append(self, campaign_id: str, event_type: str, payload: Any) -> dict[str, Any]:
        with self.transaction() as conn:
            return self._append(conn, campaign_id, event_type, payload)

    def events(self, campaign_id: str) -> list[dict[str, Any]]:
        self.manifest(campaign_id)
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT sequence,event_json FROM events WHERE campaign_id=? ORDER BY sequence",
                (campaign_id,),
            ).fetchall()
        previous = None
        result = []
        for index, row in enumerate(rows, 1):
            event = json.loads(row["event_json"])
            content = {k: v for k, v in event.items() if k != "event_hash"}
            if (
                row["sequence"] != index
                or event["sequence"] != index
                or event["campaign_id"] != campaign_id
                or event["previous_event_hash"] != previous
                or digest(content) != event["event_hash"]
            ):
                raise IntegrityError("journal sequence or hash mismatch")
            previous = event["event_hash"]
            result.append({**event, "payload": self.artifacts.get(event["payload_ref"])})
        return result

    def status(self, campaign_id: str) -> str:
        return next(
            e["payload"]["status"]
            for e in reversed(self.events(campaign_id))
            if e["event_type"] == "STATUS"
        )

    def set_status(self, campaign_id: str, status: str) -> None:
        if self.status(campaign_id) != status:
            self.append(campaign_id, "STATUS", {"status": status})

    def jobs(self, campaign_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE campaign_id=? ORDER BY rowid", (campaign_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def _usage(self, conn: sqlite3.Connection, campaign_id: str) -> dict[str, float]:
        used = Cost().dimensions()
        for row in conn.execute("SELECT * FROM jobs WHERE campaign_id=?", (campaign_id,)):
            cost = Cost.model_validate_json(row["actual"] or row["reservation"])
            for key, value in cost.dimensions().items():
                used[key] += value
        return used

    def usage(self, campaign_id: str) -> dict[str, float]:
        with self.connect() as conn:
            return self._usage(conn, campaign_id)

    def resource_totals(self, campaign_id: str) -> dict[str, dict[str, float]]:
        totals = {"actual": Cost().dimensions(), "pending": Cost().dimensions()}
        for job in self.jobs(campaign_id):
            section = "actual" if job["actual"] else "pending"
            cost = Cost.model_validate_json(job["actual"] or job["reservation"])
            for key, value in cost.dimensions().items():
                totals[section][key] += value
        return totals

    def resource_members(self, owner: str) -> list[str]:
        with self.connect() as conn:
            members = self._budget_members(conn, owner)
            for member in members:
                parent = conn.execute(
                    "SELECT owner FROM budget_links WHERE member=?", (member,)
                ).fetchone()
                if self.manifest(member).get("budget_owner") != (parent[0] if parent else None):
                    raise IntegrityError("budget lineage differs from sealed manifest")
        return members

    @staticmethod
    def _budget_members(conn: sqlite3.Connection, owner: str) -> list[str]:
        return [
            r[0]
            for r in conn.execute(
                "WITH RECURSIVE scope(id) AS (SELECT ? UNION SELECT member FROM budget_links JOIN scope ON owner=scope.id) SELECT id FROM scope",
                (owner,),
            )
        ]

    def resource_totals_tree(self, owner: str) -> dict[str, dict[str, float]]:
        totals = {"actual": Cost().dimensions(), "pending": Cost().dimensions()}
        for member in self.resource_members(owner):
            for section, values in self.resource_totals(member).items():
                for key, value in values.items():
                    totals[section][key] += value
        return totals

    def reserve(self, campaign_id: str, request: dict[str, Any], cost: Cost) -> str:
        manifest = self.manifest(campaign_id)
        limits = Budget.model_validate(manifest["task"]["budget"]).limits()
        request_ref = self.artifacts.put(request)
        job_id = "job-" + uuid.uuid4().hex
        with self.transaction() as conn:
            # Check all ancestor envelopes in the same write transaction as the
            # child reservation. Sibling queues cannot overspend a program cap.
            parent = manifest.get("budget_owner")
            while parent:
                parent_manifest = self.manifest(parent)
                members = self._budget_members(conn, parent)
                aggregate = Cost().dimensions()
                active_total = 0
                for member in members:
                    for row in conn.execute(
                        "SELECT actual,reservation FROM jobs WHERE campaign_id=?", (member,)
                    ):
                        active_total += row["actual"] is None
                        for key, value in (
                            Cost.model_validate_json(row["actual"] or row["reservation"])
                            .dimensions()
                            .items()
                        ):
                            aggregate[key] += value
                parent_budget = Budget.model_validate(parent_manifest["task"]["budget"])
                if (
                    datetime.now(UTC) - datetime.fromisoformat(parent_manifest["created_at"])
                ).total_seconds() >= parent_budget.max_wall_seconds:
                    raise BudgetExceeded("shared program elapsed-time envelope exceeded")
                if active_total >= parent_budget.max_parallel_jobs:
                    raise BudgetExceeded("shared max_parallel_jobs reached")
                if any(
                    aggregate[k] + v > parent_budget.limits()[k] + 1e-9
                    for k, v in cost.dimensions().items()
                ):
                    raise BudgetExceeded("shared program budget exceeded")
                parent = parent_manifest.get("budget_owner")
            used = self._usage(conn, campaign_id)
            # The owner itself must also account for its descendants.
            descendants = self._budget_members(conn, campaign_id)
            if (manifest.get("budget_owner") or len(descendants) > 1) and (
                datetime.now(UTC) - datetime.fromisoformat(manifest["created_at"])
            ).total_seconds() >= limits["wall_seconds"]:
                raise BudgetExceeded("shared program elapsed-time envelope exceeded")
            for child in descendants[1:]:
                for key, value in self._usage(conn, child).items():
                    used[key] += value
            active = conn.execute(
                "SELECT count(*) FROM jobs WHERE campaign_id=? AND actual IS NULL",
                (campaign_id,),
            ).fetchone()[0]
            for child in descendants[1:]:
                active += conn.execute(
                    "SELECT count(*) FROM jobs WHERE campaign_id=? AND actual IS NULL", (child,)
                ).fetchone()[0]
            if active >= manifest["task"]["budget"]["max_parallel_jobs"]:
                raise BudgetExceeded("max_parallel_jobs reached")
            for key, value in cost.dimensions().items():
                if used[key] + value > limits[key] + 1e-9:
                    raise BudgetExceeded(f"budget exceeded: {key}")
            conn.execute(
                "INSERT INTO jobs(id,campaign_id,request_ref,reservation,status) VALUES(?,?,?,?,?)",
                (job_id, campaign_id, request_ref, cost.model_dump_json(), "QUEUED"),
            )
            self._append(
                conn,
                campaign_id,
                "JOB_RESERVED",
                {
                    "job_id": job_id,
                    "request_ref": request_ref,
                    "cost": cost.model_dump(),
                },
            )
        return job_id

    def start_job(self, job_id: str) -> None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] != "QUEUED":
                raise JarvisError("job cannot be started twice")
            conn.execute("UPDATE jobs SET status='RUNNING' WHERE id=?", (job_id,))
            self._append(conn, row["campaign_id"], "JOB_STARTED", {"job_id": job_id})

    def receipt_path(self, job_id: str) -> Path:
        if not re.fullmatch(r"job-[a-f0-9]{32}", job_id):
            raise IntegrityError("invalid job ID")
        path = self.root / "receipts"
        path.mkdir(exist_ok=True, mode=0o700)
        return path / f"{job_id}.json"

    def write_receipt(self, job_id: str, receipt: dict[str, Any]) -> None:
        ref = self.artifacts.put(receipt)
        path = self.receipt_path(job_id)
        try:
            os.link(self.artifacts.path(ref), path)
            sync_directory(path.parent)
        except FileExistsError:
            if self.read_receipt(job_id) != receipt:
                raise IntegrityError("conflicting immutable job receipt") from None

    def read_receipt(self, job_id: str) -> dict[str, Any] | None:
        path = self.receipt_path(job_id)
        if not path.exists():
            return None
        value = json.loads(path.read_bytes())
        self.artifacts.get(digest(value))
        return value

    def finish_job(
        self,
        job_id: str,
        evaluation_ref: str,
        *,
        critique_ref: str | None = None,
        finding_ref: str | None = None,
    ) -> None:
        receipt = self.read_receipt(job_id)
        if receipt is None:
            raise IntegrityError("missing durable receipt")
        cost = Cost.model_validate(receipt["cost"])
        evaluation = self.artifacts.get(evaluation_ref)
        with self.transaction() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise IntegrityError("unknown receipt job")
            if job["finalized"]:
                return
            request = self.artifacts.get(job["request_ref"])
            manifest = self.manifest(job["campaign_id"])
            if (
                receipt["job_id"] != job_id
                or receipt["request_ref"] != job["request_ref"]
                or receipt["world_id"] != manifest["world_id"]
                or evaluation["action_id"] != request["action"]["action_id"]
            ):
                raise IntegrityError("receipt does not belong to this job/world")
            ref = self.artifacts.put(receipt)
            conn.execute(
                "UPDATE jobs SET status=?,actual=?,receipt_ref=?,finalized=1 WHERE id=?",
                (receipt["observation"]["status"], cost.model_dump_json(), ref, job_id),
            )
            self._append(
                conn,
                job["campaign_id"],
                "NODE_COMPLETED",
                {
                    "node_id": job_id,
                    "parent_id": request["state"]["parent_node_id"],
                    "request_ref": job["request_ref"],
                    "receipt_ref": ref,
                    "evaluation_ref": evaluation_ref,
                    "critique_ref": critique_ref,
                    "finding_ref": finding_ref,
                },
            )

    def finish_agent_job(self, job_id: str, *, turn_ref: str | None, rejection: str | None) -> None:
        """Settle an observed LLM completion without adding a numerical domain node."""
        receipt = self.read_receipt(job_id)
        if receipt is None or receipt.get("cost") is None:
            raise IntegrityError("agent usage/completion unknown; reservation must remain pending")
        cost = Cost.model_validate(receipt["cost"])
        with self.transaction() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise IntegrityError("unknown agent job")
            if job["finalized"]:
                return
            request = self.artifacts.get(job["request_ref"])
            if (
                not request.get("agent_call")
                or receipt["job_id"] != job_id
                or receipt["request_ref"] != job["request_ref"]
                or receipt["world_id"] != self.manifest(job["campaign_id"])["world_id"]
            ):
                raise IntegrityError("agent receipt/job identity mismatch")
            ref = self.artifacts.put(receipt)
            conn.execute(
                "UPDATE jobs SET status=?,actual=?,receipt_ref=?,finalized=1 WHERE id=?",
                (
                    "REJECTED" if rejection else "SUCCEEDED",
                    cost.model_dump_json(),
                    ref,
                    job_id,
                ),
            )
            self._append(
                conn,
                job["campaign_id"],
                "AGENT_COMPLETED",
                {
                    "job_id": job_id,
                    "request_ref": job["request_ref"],
                    "receipt_ref": ref,
                    "turn_ref": turn_ref,
                    "rejection": rejection,
                },
            )

    def settle_at_reservation(self, job_id: str, reason: str) -> None:
        """Charge an ended call whose usage is unknown at its full reservation (an upper bound
        set before dispatch). Only for calls whose receipt proves the process ended."""
        receipt = self.read_receipt(job_id)
        if receipt is None:
            raise IntegrityError("no receipt: completion is unknown; reconcile manually")
        with self.transaction() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise IntegrityError("unknown job")
            if job["finalized"]:
                return
            ref = self.artifacts.put(receipt)
            conn.execute(
                "UPDATE jobs SET status='SETTLED_AT_RESERVATION',actual=reservation,receipt_ref=?,finalized=1 WHERE id=?",
                (ref, job_id),
            )
            self._append(
                conn,
                job["campaign_id"],
                "JOB_SETTLED_AT_RESERVATION",
                {
                    "job_id": job_id,
                    "reason": reason,
                    "receipt_ref": ref,
                    "charged": Cost.model_validate_json(job["reservation"]).model_dump(),
                },
            )

    def finish_auxiliary_job(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """Atomically settle a typed non-development operation and its evidence event."""
        receipt = self.read_receipt(job_id)
        if receipt is None or receipt.get("cost") is None:
            raise IntegrityError("auxiliary completion/usage unknown; retain reservation")
        cost = Cost.model_validate(receipt["cost"])
        with self.transaction() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None:
                raise IntegrityError("unknown auxiliary job")
            if job["finalized"]:
                return
            if (
                receipt["job_id"] != job_id
                or receipt["request_ref"] != job["request_ref"]
                or receipt["world_id"] != self.manifest(job["campaign_id"])["world_id"]
            ):
                raise IntegrityError("auxiliary receipt identity mismatch")
            ref = self.artifacts.put(receipt)
            conn.execute(
                "UPDATE jobs SET status='SUCCEEDED',actual=?,receipt_ref=?,finalized=1 WHERE id=?",
                (cost.model_dump_json(), ref, job_id),
            )
            self._append(
                conn,
                job["campaign_id"],
                event_type,
                {
                    **payload,
                    "job_id": job_id,
                    "request_ref": job["request_ref"],
                    "receipt_ref": ref,
                },
            )

    def claim_policy_families(self, owner: str, families: list[str], purpose: str) -> bool:
        """One-shot validation families; renaming a pool cannot reset exposure."""
        if purpose not in {"development", "heldout", "fresh", "canary"}:
            raise JarvisError("unknown policy family access purpose")
        with self.transaction() as conn:
            rows = {
                row["family_key"]: dict(row)
                for row in conn.execute("SELECT * FROM policy_family_use")
            }
            if purpose != "development" and any(
                key in rows and (rows[key]["owner_id"] != owner or rows[key]["purpose"] != purpose)
                for key in families
            ):
                return False
            new = []
            for key in sorted(set(families)):
                if key not in rows:
                    conn.execute(
                        "INSERT INTO policy_family_use VALUES(?,?,?)", (key, purpose, owner)
                    )
                    new.append(key)
            if new:
                self._append(
                    conn, owner, "POLICY_FAMILIES_CLAIMED", {"families": new, "purpose": purpose}
                )
        return True

    def control(self, campaign_id: str, command: str) -> None:
        self.manifest(campaign_id)
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO controls VALUES(?,?) ON CONFLICT(campaign_id) DO UPDATE SET command=?",
                (campaign_id, command, command),
            )
            self._append(conn, campaign_id, "CONTROL", {"command": command})

    def get_control(self, campaign_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT command FROM controls WHERE campaign_id=?", (campaign_id,)
            ).fetchone()
        return row[0] if row else None
