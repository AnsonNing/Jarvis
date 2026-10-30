# P19 — Verified numerical analysis and staged research conclusions

This package strengthens autonomous research without rewriting earlier sessions.
The two optional contract records are versioned and omitted from serialization
when absent, preserving historical contract identities and legacy outcome rules.
See [ADR-015](ADR-015-trusted-research-delivery.md).

## Verified analysis delivery

`analysis_delivery: {}` enables `VerifiedAnalysisDelivery`. A numerical analysis
must finish with a regeneration script and delivered CSV/JSON evidence tables.
The harness checks paths and files, then reruns the script in a clean isolated
workspace and compares every declared output byte for byte. A missing or
nonrepeatable output is returned to the same worker and workspace as a correction,
with its failed attempt and resource receipt retained. Each finish attempt has a
separate idempotent rerun key. Optional weaker delivery requirements are labeled
UNVERIFIED unless the clean rerun actually passes.

New rerun receipts explicitly identify `trusted_harness` as the origin and record
the clean workspace, fresh-process start, exit code, script and workbench identity.
Worker-authored self-check claims remain separate. These fields certify regeneration
of delivered analysis outputs, not a repeated external backtest. Analysis contexts
also carry the declared source-byte manifest checked before workspace copying.
Earlier receipts are retained as recorded; missing historical scope is not inferred.

Passing this check establishes repeatability, not mathematical correctness,
causality, complete population coverage or research utility.

## Trusted review evidence

Before a Critic call, the harness records `TrustedReviewEvidence` with an immutable
reference. It contains recorded input/output lineage, checksummed table previews,
bounded script previews, the actual clean-rerun result, candidate/model/settings
identity, backtest provenance, execution receipts and explicit missing fields.
Preview limits and omitted items are reported; a preview is not the full dataset.

The Critic receives this material directly and later Director contexts retain the
latest evidence view. Retry uses the same recorded bundle. Offline audit verifies
its source/table visibility at construction time and verifies referenced bytes.
Missing engine instrumentation remains a data/tool gap, not a reason to repeatedly
repackage an already delivered calculation. No new filesystem/network permissions
are granted to any role.

Identity semantics explicitly distinguish the metadata artifact reference from the
blob checksum of the delivered file. Analysis input manifests carry both identities;
a difference between them is expected, not a corruption finding.

With an explicit `analysis_delivery.capture_submitted_source: true` grant, the Iris
adapter also captures source evidence: the recorded upload bytes, their request hash,
protected-region check and exact baseline-relative diff. Cached reconstructions are
labeled and require an original request checksum before receiving VERIFIED status.
Full bytes remain in private immutable blobs; reviewers receive bounded diff previews.
This proves byte identity only, not hidden-engine caller behavior or causality. Default
false is omitted from serialization, preserving earlier opt-in contract identities.

An ended provider stream reporting `max_output_tokens` retains its full reservation
when actual usage is unknown. Its bounded worker retry now receives an actionable
instruction to split scripts into small saved steps and keep the answer concise.
The declared effort is unchanged; no completed experiment is repeated as a repair.

## Research stages

Declarations can set:

```yaml
analysis_delivery: {}
research_stages:
  screening: [smoke]
  selection: development
  confirmation: [confirmation]
```

These names must partition the contract's declared periods exactly. The trusted
outcome selects on `selection`, rather than using the first listed period.
Screening comparisons are explicitly labeled and cannot complete the task.
Single-candidate paired comparisons are supported (one baseline plus one result).

The first admitted confirmation action freezes the selection winner's candidate,
full settings, selection result and acceptance profile. Confirmation admits only
that exact configuration and a matching baseline. The freeze waits for running
actions; further training, implementation and tuning require a new session.
Every confirmation period must have a passing matching observation registered
after the freeze. Previously registered observations alone cannot satisfy the
post-freeze stage. An identical backend result may be reused with its original
receipt and reuse provenance; it is never labeled newly executed or independently
validated. Missing confirmation yields AWAITING_CONFIRMATION; a breached risk
guard yields CONFIRMATION_FAILED. Neither may produce a selected task finalist or
`research_complete`. Raw positive, negative and failed observations remain stored.

For staged contracts, explicitly closed branches no longer contribute their old
recommendations to a deliverable's pending directions or newly generated memory
facts. The time-ordered reviews remain available, including the earlier directions.
Legacy contracts preserve their existing reporting behavior.

These stages operate on exposed or synthetic data. Confirmation is still
development evidence; independent validation remains NOT_RUN. A genuine locked
evaluation requires its own trusted, sealed protocol. The fixed evaluator,
acceptance thresholds and existing evidence never change during a session.

## Validation

The deterministic synthetic acceptance suite is `tests/test_trusted_research.py`.
It exercises legacy serialization/reporting, stage declarations, strong smoke
results without selection, exact frozen configurations, pre-freeze evidence,
multiple confirmation periods and matched baselines, risk failure, missing and
non-finite metrics, real isolated script execution, in-place delivery repair,
nonrepeatable-output repair, Critic evidence content, full audit and zero-call
replay. Providers and domain outcomes are declared SIMULATED_FIXTURE controls.

Commands:

```sh
python -m pytest -q tests/test_trusted_research.py
python -m pytest -q
ruff check src tests
ruff format --check src tests
mypy
```

Executed on 2026-10-25:

- Full regression including source capture and output-limit recovery:
  **368 passed in 658.51 s**.
- Source-evidence extension: **366 passed in 659.76 s** in an isolated candidate.
- Focused delivery/stage suite: **11 passed in 5.29 s**.
- Focused source/worker/provider/public-surface regression: **38 passed in 24.86 s**.
- Subsequent source-manifest, harness-rerun scope and closed-branch reporting
  refinements: **23 targeted tests passed in 130.59 s**, including research memory,
  provider settlement, public-surface checks and all delivery/stage cases.
- Ruff lint/format passed (145 Python files); mypy passed (110 source modules).
- Schema export and dependency consistency checks passed.
- Three separately retained synthetic campaigns closed: positive, failed
  confirmation, and nonrepeatable-output repair. Each audits VERIFIED and replays
  COMPLETE with zero external replay calls and no pending resource reservations.
- Four earlier real-domain session journals retain identical event counts and
  final event hashes; offline audits still return VERIFIED. They were not resumed.

Executed counts are also recorded in PROGRESS; raw logs and immutable acceptance
receipts stay in private local storage.
No synthetic outcome is a financial result or a Dream policy effectiveness claim.
Real-domain runs are separate private campaigns and are never included here.
