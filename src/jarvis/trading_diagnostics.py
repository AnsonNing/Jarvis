"""Read-only declared snapshots, strict matching and auditable PL decomposition."""

import csv
import io
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.contracts import AdmissionError, Record
from jarvis.identity import raw_digest


class DiagnosticRow(Record):
    entity_id: str | None
    pl: Decimal
    score: float | None = None
    path: tuple[str, ...] = ()


class DiagnosticSnapshot(Record):
    kind: Literal["DiagnosticSnapshot"] = "DiagnosticSnapshot"
    domain: str
    model_id: str = Field(min_length=1)
    evaluator_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    period_id: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    entity_kind: Literal["opportunity", "order", "fill"]
    stable_keyspace: str | None
    id_provenance: str = Field(min_length=1)
    pl_units: str = Field(min_length=1)
    exposure: Literal["development_exposed", "synthetic"]
    origin: Literal["human_provided", "declared_fixture", "adapter_observed"]
    source_checksum: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    rows: tuple[DiagnosticRow, ...] = Field(max_length=100000)

    @model_validator(mode="after")
    def finite_pl(self) -> "DiagnosticSnapshot":
        if any(not r.pl.is_finite() for r in self.rows):
            raise ValueError("nonfinite realized PL")
        return self


class DiagnosticInput(Record):
    baseline: DiagnosticSnapshot
    candidate: DiagnosticSnapshot

    @model_validator(mode="after")
    def compatible(self) -> "DiagnosticInput":
        for field in (
            "domain",
            "model_id",
            "evaluator_id",
            "dataset_id",
            "period_id",
            "entity_kind",
            "pl_units",
            "exposure",
        ):
            if getattr(self.baseline, field) != getattr(self.candidate, field):
                raise ValueError("snapshot comparison identity mismatch: " + field)
        return self


class DiagnosticResult(Record):
    kind: Literal["DiagnosticResult"] = "DiagnosticResult"
    status: Literal["OBSERVED", "INSUFFICIENT_DATA"]
    evidence_level: Literal["inventory", "descriptive_decomposition", "descriptive_distribution"]
    facts: dict[str, Any]
    limitations: tuple[str, ...] = Field(min_length=1)


def inventory(data: DiagnosticInput) -> DiagnosticResult:
    facts = {}
    for name in ("baseline", "candidate"):
        value = getattr(data, name)
        ids = [r.entity_id for r in value.rows if r.entity_id is not None]
        facts[name] = {
            "rows": len(value.rows),
            "missing_ids": len(value.rows) - len(ids),
            "duplicate_ids": len(ids) - len(set(ids)),
            "scores": sum(r.score is not None for r in value.rows),
            "entity_kind": value.entity_kind,
            "stable_keyspace": value.stable_keyspace,
            "total_pl": str(sum((r.pl for r in value.rows), Decimal(0))),
        }
    return DiagnosticResult(
        status="OBSERVED",
        evidence_level="inventory",
        facts=facts,
        limitations=("Available rows are not proof of complete opportunity/order coverage.",),
    )


def attribution(data: DiagnosticInput) -> DiagnosticResult:
    a, b = data.baseline, data.candidate
    overview = inventory(data).facts
    if (
        not a.stable_keyspace
        or a.stable_keyspace != b.stable_keyspace
        or any(overview[x]["duplicate_ids"] for x in ("baseline", "candidate"))
    ):
        return DiagnosticResult(
            status="INSUFFICIENT_DATA",
            evidence_level="descriptive_decomposition",
            facts={"inventory": overview},
            limitations=(
                "Stable unique cross-run identity is required; no approximate matching or invented new/lost opportunities.",
            ),
        )
    aa = {r.entity_id: r for r in a.rows if r.entity_id is not None}
    bb = {r.entity_id: r for r in b.rows if r.entity_id is not None}
    common = aa.keys() & bb.keys()
    added, removed = bb.keys() - aa.keys(), aa.keys() - bb.keys()
    shared_delta = sum((bb[k].pl - aa[k].pl for k in common), Decimal(0))
    added_pl = sum((bb[k].pl for k in added), Decimal(0))
    removed_pl = sum((aa[k].pl for k in removed), Decimal(0))
    residual = sum((r.pl for r in b.rows if r.entity_id is None), Decimal(0)) - sum(
        (r.pl for r in a.rows if r.entity_id is None), Decimal(0)
    )
    delta = sum((r.pl for r in b.rows), Decimal(0)) - sum((r.pl for r in a.rows), Decimal(0))
    assert delta == shared_delta + added_pl - removed_pl + residual
    return DiagnosticResult(
        status="OBSERVED",
        evidence_level="descriptive_decomposition",
        facts={
            "entity_kind": a.entity_kind,
            "common": len(common),
            "candidate_only_identified": len(added),
            "baseline_only_identified": len(removed),
            "unmatched_baseline": sum(r.entity_id is None for r in a.rows),
            "unmatched_candidate": sum(r.entity_id is None for r in b.rows),
            "changed_paths": sum(aa[k].path != bb[k].path for k in common),
            "shared_pl_delta": str(shared_delta),
            "added_pl": str(added_pl),
            "removed_pl": str(removed_pl),
            "unmatched_residual": str(residual),
            "total_pl_delta": str(delta),
            "reconciled": True,
        },
        limitations=(
            "Observed row decomposition, not causal mediation or unobserved fill/cancel counterfactuals.",
            "A fill snapshot cannot establish missing or rejected opportunity coverage.",
        ),
    )


def score_distribution(data: DiagnosticInput) -> DiagnosticResult:
    facts = {}
    for name in ("baseline", "candidate"):
        rows: tuple[DiagnosticRow, ...] = getattr(data, name).rows
        values = [r.score for r in rows if r.score is not None]
        facts[name] = {
            "observed_scores": len(values),
            "missing_scores": len(rows) - len(values),
            "mean": sum(values) / len(values) if values else None,
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
        }
    return DiagnosticResult(
        status="OBSERVED",
        evidence_level="descriptive_distribution",
        facts=facts,
        limitations=(
            "Selected-row score distributions do not establish full-population model calibration or causality.",
        ),
    )


def snapshot_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(20_000_001)
    if len(raw) > 20_000_000:
        raise AdmissionError("snapshot exceeds admitted read size")
    return raw


def import_snapshot_csv(
    path: Path, metadata: dict[str, Any], columns: dict[str, str]
) -> DiagnosticSnapshot:
    if set(columns) - {"entity_id", "pl", "score"} or "pl" not in columns:
        raise AdmissionError("explicit PL and optional identity/score column mapping required")
    raw_bytes = snapshot_bytes(path)
    with io.StringIO(raw_bytes.decode("utf-8-sig"), newline="") as stream:
        reader = csv.DictReader(stream)
        if len(reader.fieldnames or ()) != len(set(reader.fieldnames or ())):
            raise AdmissionError("duplicate CSV headers are ambiguous")
        if not set(columns.values()) <= set(reader.fieldnames or ()):
            raise AdmissionError("missing declared CSV columns")
        rows: list[DiagnosticRow] = []
        for raw in reader:
            if len(rows) >= 100000:
                raise AdmissionError("snapshot row limit")
            rows.append(
                DiagnosticRow(
                    entity_id=raw.get(columns.get("entity_id", "")) or None,
                    pl=Decimal(raw[columns["pl"]]),
                    score=float(raw[columns["score"]])
                    if columns.get("score") and raw[columns["score"]]
                    else None,
                )
            )
    return DiagnosticSnapshot.model_validate(
        {
            **metadata,
            "source_checksum": raw_digest(raw_bytes),
            "rows": [r.model_dump(mode="json") for r in rows],
        }
    )
