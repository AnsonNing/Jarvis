"""Read-only logical-case selection for explicitly owner-authorized replacements."""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.contracts import IntegrityError, Record
from jarvis.identity import file_digest


class IrisRetryAuthorization(Record):
    source: Literal["Direct human instruction in this chat"]
    instruction: str = Field(min_length=1)
    recorded_at_utc: datetime
    recorded_at_user_time: datetime | None = None
    original_request: str = Field(pattern=r"^[a-f0-9]{8}$")
    retry_label: str = Field(min_length=1)
    max_additional_submissions: Literal[1]
    identical_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["submitted_once"]
    automatic_retry: Literal[False]
    new_request: str = Field(pattern=r"^[a-f0-9]{8}$")

    @model_validator(mode="after")
    def ordered_identity(self) -> "IrisRetryAuthorization":
        if self.recorded_at_utc.tzinfo is None or self.original_request == self.new_request:
            raise ValueError("replacement requires timezone and distinct request identities")
        return self


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise IntegrityError("missing or unreadable evidence: " + path.name) from exc
    if not isinstance(value, dict):
        raise IntegrityError("evidence must be an object: " + path.name)
    return value


def select_cases(folder: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        return _select_cases(folder, manifest)
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
        raise IntegrityError("invalid or incomplete replacement evidence") from exc


def _select_cases(folder: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """One-hop replacements only; original unknown requests remain explicitly unresolved.

    This checks supplied provenance; it does not authenticate a human, cancel a
    remote job, settle any reservation, or create permission for another upload.
    """
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases or any(not isinstance(c, dict) for c in cases):
        raise IntegrityError("manifest needs nonempty case objects")
    labels = [c.get("label") for c in cases]
    ids = [c.get("request_id") for c in cases]
    if any(not isinstance(v, str) or not v for v in (*labels, *ids)):
        raise IntegrityError("case label/request identity missing")
    if len(set(labels)) != len(labels) or len(set(ids)) != len(ids):
        raise IntegrityError("duplicate case label or request identity")
    originals = [c for c in cases if c.get("superseded_by")]
    replacements = [c for c in cases if c.get("retry_of")]
    authorizations = manifest.get("authorized_retries", [])
    if not isinstance(authorizations, list) or len(originals) != len(replacements):
        raise IntegrityError("orphan replacement or invalid authorization list")
    if not originals:
        if authorizations:
            raise IntegrityError("unused replacement authorization")
        return {"cases": cases, "unresolved_superseded": [], "proof_inputs": {}}
    # The upstream v1 proof admits exactly one manually authorized extra request.
    if len(originals) != 1 or len(authorizations) != 1:
        raise IntegrityError("replacement proof v1 requires exactly one explicit authorization")
    try:
        auth = IrisRetryAuthorization.model_validate(authorizations[0])
        separate = IrisRetryAuthorization.model_validate(
            read_object(folder / "RESUBMISSION_AUTHORIZATION.json")
        )
    except ValueError as exc:
        raise IntegrityError("invalid owner replacement authorization") from exc
    if auth != separate:
        raise IntegrityError("manifest and separate owner authorization differ")
    original, replacement = originals[0], replacements[0]
    if (
        original["request_id"] != auth.original_request
        or replacement["request_id"] != auth.new_request
        or replacement["label"] != auth.retry_label
        or original["superseded_by"] != replacement["label"]
        or replacement["retry_of"] != original["request_id"]
        or replacement.get("logical_case_label") != original["label"]
        or original.get("retry_of")
        or replacement.get("superseded_by")
        or original.get("status") != "superseded"
        or replacement.get("status") != "completed"
        or replacement.get("result_exit_code") != 0
    ):
        raise IntegrityError("replacement identities, lineage or completion invalid")
    for field in ("side", "generation", "mode", "comparison_month", "phase", "period", "sha256"):
        if original.get(field) is None or original[field] != replacement.get(field):
            raise IntegrityError("replacement scope differs: " + field)
    if original["sha256"] != auth.identical_source_sha256:
        raise IntegrityError("replacement source differs from owner authorization")
    pins = {}
    for case in (original, replacement):
        source = Path(case.get("file", ""))
        if not source.is_file() or file_digest(source) != "sha256:" + auth.identical_source_sha256:
            raise IntegrityError("replacement source bytes changed")
        pins[str(source.resolve())] = file_digest(source)
    try:
        before, after = float(original["submitted_at"]), float(replacement["submitted_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrityError("replacement submission times missing") from exc
    if not all(math.isfinite(v) for v in (before, after)) or not (
        before <= auth.recorded_at_utc.timestamp() <= after
    ):
        raise IntegrityError("replacement authorization is not time ordered")
    job = replacement.get("job", {})
    if job.get("branch") != manifest.get("branch") or job.get("period") != original["period"].split(
        "-"
    ):
        raise IntegrityError("replacement job scope mismatch")
    proof = read_object(folder / "retry_scope_acceptance.json")
    previous = read_object(folder / "interim" / "manifest.json")
    previous_cases = previous.get("cases", [])
    completed = [c for c in previous_cases if c.get("status") == "completed"]
    previous_ids = [c["request_id"] for c in completed]
    if (
        proof.get("status") != "PASS"
        or proof.get("new_requests") != [auth.new_request]
        or proof.get("candidate_sha256") != auth.identical_source_sha256
        or proof.get("model_hpp_unchanged") is not True
        or proof.get("automatic_resubmissions") != 0
        or proof.get("previous_completed_count") != len(completed)
        or len(set(previous_ids)) != len(previous_ids)
        or sorted(proof.get("previous_completed_unchanged", [])) != sorted(previous_ids)
        or set(ids) - {auth.new_request} != {c.get("request_id") for c in previous_cases}
    ):
        raise IntegrityError("replacement scope proof inconsistent with previous manifest")
    for old in completed:
        current = next(c for c in cases if c["request_id"] == old["request_id"])
        for field in ("label", "file", "sha256", "result_dir", "job", "submitted_at", "status"):
            if current.get(field) != old.get(field):
                raise IntegrityError("previously completed case changed: " + field)
    for name in (
        "RESUBMISSION_AUTHORIZATION.json",
        "retry_scope_acceptance.json",
        "interim/manifest.json",
    ):
        pins[str((folder / name).resolve())] = file_digest(folder / name)
    return {
        "cases": [c for c in cases if not c.get("superseded_by")],
        "unresolved_superseded": [auth.original_request],
        "proof_inputs": pins,
    }


def selected_case(folder: Path, label: str) -> dict[str, Any]:
    selection = select_cases(folder, read_object(folder / "manifest.json"))
    matches = [c for c in selection["cases"] if c.get("logical_case_label", c["label"]) == label]
    if len(matches) != 1:
        raise IntegrityError("expected one effective baseline case")
    return matches[0]
