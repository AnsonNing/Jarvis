# Verified implementation progress

Date: 2026-10-18. Release: 0.3.0. Complete synthetic Dream-RSI and explicit real LLM research are available.

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
| P3 branching corpus | VERIFIED_WITH_LIMITATIONS | Twelve-tree, six-family corpus, real sibling execution, compatibility/splits, causal verification and coverage CLI; L2 transfer A/B remains unimplemented |
| P4 synthetic Dream-RSI | VERIFIED_WITH_LIMITATIONS | Real developer and fixture control each completed three rounds plus fresh canary; exact replay, one finalist, independent/fresh gates, versioning and measured rollback. Final active policy Q0; no general efficacy claim |
| P5 Hermes integration | VERIFIED_WITH_LIMITATIONS | Previously admitted frozen artifacts, baseline reproduction and controlled LLM comparisons; new upstream baseline format needs separate admission. Price-path exits/custom cancellation/event backtester remain unavailable |
| Domain settings design | VERIFIED_WITH_LIMITATIONS | Bounded single-axis/factorial settings and cancellation DSL; explicit capability negotiation. Unavailable stop/target execution is NOT_RUN |

Validation: `pytest -q`, Ruff lint/format, mypy, task CLI and complete local
synthetic/Hermes workflows. The final acceptance count and exact commands are
recorded in RELEASE-0.3.md; dependency versions are pinned in requirements-lock.txt.
Private numerical reports and raw receipts stay in ignored local storage.

Next work packages: new-baseline adapter admission; price/fill/event exit and cancellation
backends; persistent scoped L2 memory/capability validation; generated-code isolation.
Additional Dream rounds require independent new validation families after exposure;
the synthetic fixture result does not authorize real-domain policy promotion.
