"""Capture owner-provided submitted source bytes for review; no engine claims."""

import difflib
import json
import re
from pathlib import Path
from typing import Any

from jarvis.autonomy_adapters import IrisRegionBacktester, region_body
from jarvis.contracts import IntegrityError
from jarvis.identity import raw_digest
from jarvis.iris import split_regions
from jarvis.research_executors import ResolvedCandidate


def source_evidence(
    executor: IrisRegionBacktester,
    candidate: ResolvedCandidate,
    settings: dict[str, Any],
    period: str,
    workdir: Path,
    provenance: dict[str, Any],
) -> dict[str, str]:
    """Verify recorded upload bytes, or an explicitly identified cached reconstruction."""
    env = executor.environments[period]
    baseline = Path(env.template).read_bytes()
    if raw_digest(baseline) != env.template_sha256:
        raise IntegrityError("baseline source changed before source-evidence capture")
    rendered = executor.render_text(candidate, settings).encode()
    record = workdir / "submission.json"
    if record.exists():
        submission = json.loads(record.read_text())
        captured = Path(submission["candidate_path"]).read_bytes()
        expected = submission["candidate_sha256"]
        request_id = submission["request_id"]
        origin = "recorded_upload_bytes"
    else:
        captured = rendered
        expected = provenance.get("candidate_sha256")
        request_id = provenance.get("request_id") or provenance.get("reused_request")
        origin = "cached_request_reconstruction"
    actual = raw_digest(captured)
    verified = bool(expected)
    if verified and (actual != expected or captured != rendered):
        raise IntegrityError("captured candidate source differs from its recorded request")
    before, after = baseline.decode(), captured.decode()
    protected_before = "".join(line for writable, line in split_regions(before) if not writable)
    protected_after = "".join(line for writable, line in split_regions(after) if not writable)
    if protected_before != protected_after:
        raise IntegrityError("captured candidate modified protected source")
    names = re.findall(r"// @start:([A-Za-z0-9_]+)", before)
    changed = [name for name in names if region_body(before, name) != region_body(after, name)]
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="owner_baseline",
            tofile="captured_candidate",
            n=2,
        )
    )
    evidence = {
        "kind": "ExecutionSourceEvidence",
        "schema_version": 1,
        "status": "VERIFIED" if verified else "UNAVAILABLE_REQUEST_HASH",
        "capture_origin": origin,
        "request_id": request_id,
        "baseline_source_sha256": raw_digest(baseline),
        "candidate_source_sha256": actual,
        "recorded_candidate_sha256": expected,
        "submitted_hash_matches": verified,
        "protected_text_unchanged": True,
        "changed_regions": changed,
        "settings": settings,
        "model_ref": candidate.candidate.model_ref,
        "patch_refs": list(candidate.candidate.patch_refs),
        "source_diff": diff,
        "scope": "Submitted/cached candidate source bytes only; hidden engine callers and execution causality are not certified.",
        "baseline_source": before,
        "candidate_source": after,
    }
    path = workdir / "execution_source.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return {
        "name": "execution_source",
        "path": str(path),
        "description": "Captured source hashes, protected-region check and exact diff; cached origins explicit",
    }
