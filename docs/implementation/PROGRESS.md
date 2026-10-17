# Verified implementation progress

Date: 2026-10-18. Release: 0.2.0. Real LLM research is now available through explicit opt-in.

| Area | Status | Evidence and limits |
| --- | --- | --- |
| P0.1 contracts/package | VERIFIED | Strict records, finite canonical JSON, generated schemas, installable CLI |
| P0.2 evidence/journal | VERIFIED | Hash tamper, atomic rollback, spawned-process append tests |
| P0.3 immutable identity | VERIFIED | Sealed task/world, source/environment/data changes refuse resume |
| P0.4 admission | VERIFIED_WITH_LIMITATIONS | Closed action IDs, development split and visible-evidence guards; static tools only |
| P0.5 jobs/budgets | VERIFIED_WITH_LIMITATIONS | Atomic reservations, receipt adoption, uncertain-job stop; sequential local coordinator, no remote leases |
| P0.6–P0.9 fixture/evaluation/tree/Q0 | VERIFIED_WITH_LIMITATIONS | Four fixture worlds, fixed evaluator, formal freeze, causal tree, adaptive Q0; extended fixture matrix remains |
| P1 LLM research loop | VERIFIED_WITH_LIMITATIONS | Real structured Codex provider; autonomous hypothesis/design/analysis/revision; two real Hermes campaigns CLOSED; token/repair/recovery/replay verified |
| P1 generated candidate builder | NOT_STARTED | Arbitrary generated-code execution awaits a separately verified isolation boundary |
| P2 memory/capability RSI | NOT_STARTED | Findings are sealed in campaign histories; no cross-campaign retrieval or utility/transfer validation |
| P3 cross-task corpus | NOT_STARTED | No independent multi-family corpus or A/B transfer study |
| P4 Dream-RSI | NOT_STARTED | Exact recorded-path replay primitive verified; no dreaming/policy promotion or efficacy claim |
| P5 Hermes integration | VERIFIED_WITH_LIMITATIONS | Real local inputs, baseline reproduction, isolated inference, LLM-designed frozen-model comparisons and grouped diagnostics; no training/event-backtester integration |

Validation: `pytest -q`, Ruff lint/format, mypy, task CLI and complete local
synthetic/Hermes workflows. The final acceptance count and exact commands are
recorded in RELEASE-0.2.md; dependency versions are pinned in requirements-lock.txt.
Private numerical reports and raw receipts stay in ignored local storage.

Next work packages: sandboxed candidate generation; training/backtester adapters; persistent
scoped memory and capability validation. Dream-RSI promotion waits for independent
families and sufficiently covered actual research histories.
