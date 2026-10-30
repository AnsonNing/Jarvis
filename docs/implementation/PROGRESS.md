# Verified implementation progress

Latest engineering update: 2026-10-26, [P20 evidence-handoff repair](P20-evidence-handoff-repair.md): cited analysis definitions reach implementation, rejected no-edit deliveries retain their reasons, and input/planning corrections are actionable. Seventy unique related synthetic/regression tests passed in two overlapping batches; full-suite acceptance below retains its original date. P19/P20 histories and historical release evidence remain preserved.

Date: 2026-10-21. Release: 0.14.0. Mechanism research adds complete owner intent, a question/hypothesis tree, typed evidence-seeking actions, read-only diagnostics, versioned skills and a separate isolated Builder. Current engineering acceptance uses artificial data and scripted providers. Historical real research remains paused; no automatic resumption is enabled.

Initial 0.14 acceptance at `3678a77`: **307 tests passed in 384.15s**, 29 targeted feature tests
passed, three public skill self-tests passed, Ruff lint/format passed for 122
Python files, and mypy passed for 92 source files. The full new workflow reached
VERIFIED with eight actions across two versions, 21 scripted role calls, actual
isolated Python and candidate commit, zero real provider/Iris calls, and zero
pending resources. Existing Critic, Dream feedback and Research Lab session CLI
regressions also closed with VERIFIED audits. The hosted macOS framework-launcher
follow-up adds a direct-interpreter selection regression; 30 focused tests pass
without granting process-fork permission. See [Release 0.14](RELEASE-0.14.md).

| Area | Status | Evidence and limits |
| --- | --- | --- |
| P23 recoverable stops | VERIFIED_WITH_LIMITATIONS | Diagnosed every stop of the real Hermes sessions: repeated invalid Director decisions, provider timeouts and a context envelope overflow, all terminal. Role exhaustion now recovers in the same run (two rounds with earlier rejections shown) and then stops resumably; `autonomy resume` continues the same session; memory has a last-resort compact view; default context/prompt caps raised and clamped to the reservation. Synthetic tests only; no real research run. See P23-recoverable-stops.md. |
| P24 training settings and risk floors | VERIFIED_WITH_LIMITATIONS | `TRAIN_MODEL` takes declared strategy settings that apply to the base during training and stay on the trained candidate, so data collected under a rule switch can train the policy that uses it. `RiskLimit.floor` bounds a metric absolutely (e.g. mean PR >= 0.001); `max_worsening` is optional. Synthetic tests only. See P24-training-settings-and-floors.md. |
| P22 actionable finite recovery | VERIFIED_WITH_LIMITATIONS | Rejected proposal and exact validation feedback survive ended-call recovery; existing questions and matched controls are reused without duplicate execution or expanded visibility. Provider and semantic failure waves have separate durable bounds; terminal/unknown work stays stopped. C++ driver alias and STL/exception linkage, sealed native argv/input lineage, provisional usage and accurate resume explanations covered. Retained one-run fault-injected synthetic campaign CLOSED in 8 decisions, stage gate PASS, audit VERIFIED, zero-call replay and zero pending jobs; persistent-fault negative controls pass. Full regression 423 passed in 712.65s, Ruff 158 files and mypy 117 modules passed. All 29 original journals unchanged; real research remains stopped. Private monitor predicate passed 36 synthetic cases without launching it. No real provider/Iris/training or operational Dream, commit, push or deployment. See P22-actionable-recovery.md. |
| P21 workspace-history follow-up | VERIFIED_WITH_LIMITATIONS | Preserve already compact `{step,note,tool}` entries during a second context pass; long mixed histories no longer assume nested requests. Targeted regression 54 passed in 21.24s, Ruff and mypy 116 modules passed. Research receipts were settled before editing; continuation requires a new source identity. Prior 411-test full-suite evidence remains scoped to the base P21 source. See P21-workspace-history-correction.md. |
| P21 bounded native runtime and delivery | VERIFIED_WITH_LIMITATIONS | Owner-pinned native compile/run worker tool with sealed source/input/exit evidence, bounded role/Curator contexts preserving compact experiment identities and owner intent, separate role/context vs total-budget stops, finite receipted recovery/refusal backoff, nested-rerun preflight and automatic provider-free exit packages. Single retained synthetic CLI run CLOSED in 8 decisions, stage gate PASS, audit VERIFIED, replay COMPLETE with zero external calls and no pending resources; actual native host-read/write/network/fork sentinels passed. Full regression 411 passed in 690.07s, Ruff 156 files and mypy 116 modules passed. All 29 original journal databases unchanged; real research remains paused. No paid-provider quality claim, real Iris/Hermes research, operational Dream, commit, push or deployment. A dead process/machine still needs a launcher; unknown work requires reconciliation. |
| P20 collaborative research planning | VERIFIED_WITH_LIMITATIONS | Opt-in direction reviews inside the Director's normal response, alternatives and qualitative cross-direction tradeoffs, distinct branch/session/checkpoint scopes, existing owner advice/resume, idempotent stop recovery, causal audit and legacy serialization. Full regression 388 passed; 31 targeted planning/stage tests and one separate shipped CLI example test passed. Five retained scripted toy campaigns audit VERIFIED and replay COMPLETE with zero external replay calls; 38 historical campaign/subtask journals and manifests unchanged. No fixed breadth/tool quotas, real research, paid-provider campaign or operational Dream run. Research-quality benefits remain unmeasured. |
| P15 mechanism research and skills | VERIFIED_WITH_LIMITATIONS | Complete owner contract, typed question/action loop, strict row diagnostics, checksum-pinned packages, private Builder and cross-session adoption; no real provider/backend work or causal/utility claim |
| P14 recovery and continued missions | VERIFIED_WITH_LIMITATIONS | Exact-reference correction, explicit historical recovery, decision-time Critic controls, multi-session controller and failure-finalized runner; known/unknown-call and continuation controls passed. Real outcomes remain private. |
| P13 selected entry sessions | VERIFIED_WITH_LIMITATIONS | Selected-package and calendar-subset baseline admission, side-specific trigger, pinned role effort, explicit PR/MDD feasibility, sequential reviewed session and private notebook; complete artificial session passed. Real outcomes remain private and do not establish independent Dream effectiveness. |
| P0.1 contracts/package | VERIFIED | Strict records, finite canonical JSON, generated schemas, installable CLI |
| P0.2 evidence/journal | VERIFIED | Hash tamper, atomic rollback, spawned-process append tests |
| P0.3 immutable identity | VERIFIED | Sealed task/world, source/environment/data changes refuse resume |
| P0.4 admission | VERIFIED_WITH_LIMITATIONS | Closed action IDs, development split and visible-evidence guards; static tools only |
| P0.5 jobs/budgets | VERIFIED_WITH_LIMITATIONS | Atomic reservations, receipt adoption, uncertain-job stop; sequential local coordinator, no remote leases |
| P0.6–P0.9 fixture/evaluation/tree/Q0 | VERIFIED_WITH_LIMITATIONS | Four fixture worlds, fixed evaluator, formal freeze, causal tree, adaptive Q0; extended fixture matrix remains |
| P1 LLM research loop | VERIFIED_WITH_LIMITATIONS | Real structured Codex provider; autonomous hypothesis/design/analysis/revision; four new-baseline real Hermes rounds CLOSED plus prior legacy acceptance; token/repair/recovery/replay verified |
| P1 generated candidate builder | VERIFIED_WITH_LIMITATIONS | Separate structured Builder, real OS-isolated Python, independent fixed paired-attribution fidelity, retained local candidate commit and versioned successor use; arbitrary new protocols and research utility remain gated |
| P2 memory/capability RSI | VERIFIED_WITH_LIMITATIONS | Scoped experience retains actual observations/counter-evidence and provisional interpretations; compatible new-task reuse and negative control pass. One closed diagnostic has metered fidelity, independent utility and subsequent use; general capability acquisition remains gated; isolated fixed-protocol generated skills are covered by P15 |
| P3 branching corpus | VERIFIED_WITH_LIMITATIONS | Twelve-tree, six-family corpus, real sibling execution, compatibility/splits, causal verification and coverage CLI; L2 transfer A/B remains unimplemented |
| P4 synthetic Dream-RSI | VERIFIED_WITH_LIMITATIONS | Real developer and fixture control each completed three rounds plus fresh canary; exact replay, one finalist, independent/fresh gates, versioning and measured rollback. Final active policy Q0; no general efficacy claim |
| P5 Hermes integration | VERIFIED_WITH_LIMITATIONS | Both new frozen baselines admitted, portable predictions agree exactly, four policies reproduced; four real LLM rounds CLOSED, causal review and offline replay verified. Real postfill quote paths and event acknowledgement backtester remain unavailable |
| Domain settings design | VERIFIED_WITH_LIMITATIONS | Risk/age cancellation DSL, controlled joint comparisons and separate durable quote exit simulation; synthetic quote tests cover execution gaps/latency/fees. Real stop/target execution is NOT_RUN |
| P6 Iris research adapter/queue | VERIFIED_WITH_LIMITATIONS | Explicit accepted-package gate, region-only constants, read-only baseline reuse, durable request IDs, uncertainty stop, shared finite grants and offline receipt audit; inert private-API subprocess contract tests pass |
| P6 direction program / Hermes Dream | VERIFIED_WITH_LIMITATIONS | Concurrent next-epoch direction role, immutable catalogs, causal prior evidence, exact replay, development finalist, one-shot heldout, fresh arms and measured canary rollback pass artificial acceptance; real policy promotion remains gated |
| P7 continuous Iris research | VERIFIED_WITH_LIMITATIONS | Rolling bounded programs under standing owner authorization, shared conservative quota, cross-program candidate deduplication, rejection feedback, original-server-time acknowledgement reads and one persistent admission watcher; thirteen artificial executions span five programs, with zero remote submissions |
| P8 strategy-design rules | VERIFIED_WITH_LIMITATIONS | Typed all/any entry/cancel predicates, session clocks, quantity and position limits; owner-pinned source bindings and compile receipts; five compiled artificial executions across two adaptive epochs preserve positive/negative findings; quiescent predecessor handoff retains histories and shared quota |
| P9 research readiness | VERIFIED_WITH_LIMITATIONS | Explicit logical-case replacement proof, retained unknown requests, completed-findings attention state, owner-bound development-use records and store-free readiness; stopped unstarted watchers remain stopped; four supplied baseline ledgers recomputed and four source compilations passed locally, with no new research/LLM/backend submission |
| P10 independent Critic | VERIFIED_WITH_LIMITATIONS | Two-stage separate provider calls, approval-before-enqueue, result critique, causal next-round feedback, receipt recovery, shared budgets and continuous stop propagation; two artificial epochs and a real three-call LLM/artificial-backend acceptance completed. No new real Hermes research or efficiency-gain claim |
| P11 reviewed Dream feedback | VERIFIED_WITH_LIMITATIONS | Selected policy controls actual reviewed epochs and new continuous programs; full role/compile/backend costs share an atomic budget; independent fresh/canary programs, adopted policy and measured rollback, new history and second Dream screening pass artificial acceptance. Scripted providers use simulated units; no real-domain policy/efficiency verdict |
| P12 research self-improvement | VERIFIED_WITH_LIMITATIONS | Parallel independent specialists and directed discussion, pinned human/source boards, live metadata adapters, scoped memory, method/capability/action-policy evolution and later profile use; 154 artificial executions, measured method rollback and two retained profiles; no real Hermes study or general autonomous skill claim |
| P16 autonomous research + action-policy Dream | VERIFIED_WITH_LIMITATIONS | Typed READ_SOURCE/ANALYZE/BACKTEST/TRAIN_MODEL/IMPLEMENT/COMPARE actions with injected executors, OS workbench with agentic receipted tool loop, candidates with model/data lineage, multi-objective profiles, background actions, owner guidance, stop guard, quota/timeout recovery, deliverable; Dream-RSI over the action policy with exact-replay screen, paired fresh validation on hidden holdout, adoption, canary and rollback. Synthetic world and scripted roles plus four bounded real-LLM sessions; full regression 340 passed; no Hermes/Iris/real training run |
| P17 real-domain executors | VERIFIED_WITH_LIMITATIONS | Iris region backtester (bindings, model constants, calendars, submission ledger, identical-run reuse in the private adapter), owner training entry in interpreter mode with compiler access, region compile-check harness, Claude Code and relay providers. Private Hermes adapter exercised live: baseline replay, Iris submissions, sandboxed retraining with HPP parity, a 14-decision real study; full regression 344 passed |
| P18 research memory + skills + Dream from histories | VERIFIED_WITH_LIMITATIONS | Per-domain knowledge base with trusted facts, curator lessons and reproducible-analysis skills; memory snapshot pinned into new sessions (reuse, bounded recall, mounted skills, memory-aware policy); export/import; Dream-RSI from existing sessions adopting into the domain registry used by later sessions. Synthetic world and scripted roles; full regression 352 passed; no real Hermes memory or Dream round run |
| P19 trusted analysis delivery + research stages | VERIFIED_WITH_LIMITATIONS | Opt-in clean-rerun delivery with same-workspace repair, bounded Critic evidence including comparison inputs, explicit artifact/blob identity semantics, opt-in submitted-source checks and diffs, bounded output-limit recovery, source manifest and explicit harness execution scope, selection/confirmation and exact-candidate freeze, closed-branch direction filtering, risk/missing-metric gates and historical compatibility. Three retained synthetic campaigns; full regression 368 passed and focused source/provider/public-surface regression 38 passed. Final scope/closure refinements passed 23 targeted delivery, memory, provider and public-surface tests. Ruff and mypy pass. Repeatability is not scientific correctness; exposed-data confirmation is not independent validation. |

Validation after research recovery integration: **275 tests passed in
342.66s**, Ruff lint/format passed for 109 Python
files and mypy passed for 82 source modules. Dependency check, schema export,
bounded runner CLI, task validation and complete self-improvement CLI fixture
passed. The final acceptance count and exact commands are
recorded in release acceptance documents; dependency versions are pinned in requirements-lock.txt.
Private numerical reports and raw receipts stay in ignored local storage.

Next work packages: supply real quote/fill/fee data and order acknowledgement events;
admit corresponding execution backends; extend beyond the single closed L2
diagnostic with new independently validated tool operations; generated-code
isolation and full-paper/code-reproduction adapters. The typed local quote backend and data
requirements are already implemented and tested.
Additional Dream rounds require independent new validation families after exposure;
the synthetic fixture result does not authorize real-domain policy promotion.

The P6 implementation and operating boundaries are recorded in
[Iris and Hermes Dream](P6-iris-and-hermes-dream.md). The accepted multi-month
package is consumed read-only after its upstream workflow finishes; no incomplete
or single-month substitute passes this gate. Development research can proceed
after acceptance without claiming an independent real-policy verdict.

[Continuous Iris research](P7-continuous-iris-research.md) removes a fixed lifetime
experiment-count limit while preserving finite program budgets. It continues L1
strategy research. [Reviewed Dream feedback](P11-reviewed-dream-loop.md) can now
explicitly adopt a validated exploration policy through a scoped registry in
new reviewed programs. Research-policy promotion still requires distinct
histories and independent heldout/fresh tasks; canary regression rolls it back.
Scoped L2 utility and reuse are now demonstrated for one closed diagnostic;
general capability acquisition and arbitrary new strategy source remain incomplete.

[Research self-improvement](P12-research-self-improvement.md) adds a bounded
research-method layer and agent teams without editing the trusted kernel.
Fresh profiles are pinned in subsequent tasks; separately validated axes cannot
be combined without a new joint comparison. The method fixture deliberately
regresses on a canary and rolls back; capability and policy fixtures retain
their profiles. Each second proposal cycle stops for new independent validation.
Public Crossref/GitHub queries returned real metadata, while all research
acceptance used declared scripted providers and mathematical backends.

[Strategy-design research](P8-strategy-design.md) adds bounded rules and private
source compilation. It preserves active older runtimes in their original checkout;
a new watcher can take over only after their lease is released and receipts are
settled. Real model acceptance remains mandatory. Compile success is engineering
evidence, with no financial improvement, complete execution fidelity or live
capacity verdict.
