# Verified implementation progress

Date: 2026-10-18. Release: 0.5.0. Synthetic Dream-RSI, Hermes baseline admission, adaptive real LLM research and a private Iris research queue are available.

| Area | Status | Evidence and limits |
| --- | --- | --- |
| P0.1 contracts/package | VERIFIED | Strict records, finite canonical JSON, generated schemas, installable CLI |
| P0.2 evidence/journal | VERIFIED | Hash tamper, atomic rollback, spawned-process append tests |
| P0.3 immutable identity | VERIFIED | Sealed task/world, source/environment/data changes refuse resume |
| P0.4 admission | VERIFIED_WITH_LIMITATIONS | Closed action IDs, development split and visible-evidence guards; static tools only |
| P0.5 jobs/budgets | VERIFIED_WITH_LIMITATIONS | Atomic reservations, receipt adoption, uncertain-job stop; sequential local coordinator, no remote leases |
| P0.6–P0.9 fixture/evaluation/tree/Q0 | VERIFIED_WITH_LIMITATIONS | Four fixture worlds, fixed evaluator, formal freeze, causal tree, adaptive Q0; extended fixture matrix remains |
| P1 LLM research loop | VERIFIED_WITH_LIMITATIONS | Real structured Codex provider; autonomous hypothesis/design/analysis/revision; four new-baseline real Hermes rounds CLOSED plus prior legacy acceptance; token/repair/recovery/replay verified |
| P1 generated candidate builder | NOT_STARTED | Arbitrary generated-code execution awaits a separately verified isolation boundary |
| P2 memory/capability RSI | PARTIAL | Scoped prior-round review reuses sealed real development comparisons with causal verification; general retrieval, utility/transfer validation and capability learning remain unimplemented |
| P3 branching corpus | VERIFIED_WITH_LIMITATIONS | Twelve-tree, six-family corpus, real sibling execution, compatibility/splits, causal verification and coverage CLI; L2 transfer A/B remains unimplemented |
| P4 synthetic Dream-RSI | VERIFIED_WITH_LIMITATIONS | Real developer and fixture control each completed three rounds plus fresh canary; exact replay, one finalist, independent/fresh gates, versioning and measured rollback. Final active policy Q0; no general efficacy claim |
| P5 Hermes integration | VERIFIED_WITH_LIMITATIONS | Both new frozen baselines admitted, portable predictions agree exactly, four policies reproduced; four real LLM rounds CLOSED, causal review and offline replay verified. Real postfill quote paths and event acknowledgement backtester remain unavailable |
| Domain settings design | VERIFIED_WITH_LIMITATIONS | Risk/age cancellation DSL, controlled joint comparisons and separate durable quote exit simulation; synthetic quote tests cover execution gaps/latency/fees. Real stop/target execution is NOT_RUN |
| P6 Iris research adapter/queue | VERIFIED_WITH_LIMITATIONS | Explicit accepted-package gate, region-only constants, read-only baseline reuse, durable request IDs, uncertainty stop, shared finite grants and offline receipt audit; inert private-API subprocess contract tests pass |
| P6 direction program / Hermes Dream | VERIFIED_WITH_LIMITATIONS | Concurrent next-epoch direction role, immutable catalogs, causal prior evidence, exact replay, development finalist, one-shot heldout, fresh arms and measured canary rollback pass artificial acceptance; real policy promotion remains gated |

Validation: `pytest -q`, Ruff lint/format, mypy, task CLI and complete local
synthetic/Hermes workflows. The final acceptance count and exact commands are
recorded in release acceptance documents; dependency versions are pinned in requirements-lock.txt.
Private numerical reports and raw receipts stay in ignored local storage.

Next work packages: supply real quote/fill/fee data and order acknowledgement events;
admit corresponding execution backends; persistent scoped L2 utility/capability
validation; generated-code isolation. The typed local quote backend and data
requirements are already implemented and tested.
Additional Dream rounds require independent new validation families after exposure;
the synthetic fixture result does not authorize real-domain policy promotion.

The P6 implementation and operating boundaries are recorded in
[Iris and Hermes Dream](P6-iris-and-hermes-dream.md). The accepted multi-month
package is consumed read-only after its upstream workflow finishes; no incomplete
or single-month substitute passes this gate. Development research can proceed
after acceptance without claiming an independent real-policy verdict.
