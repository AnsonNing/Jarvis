# P21 — Trusted native execution, bounded contexts, recovery and delivery

Status: VERIFIED_WITH_LIMITATIONS (synthetic engineering acceptance, 2026-10-26).

The previous runtime exposed Python and validation tools but no admitted native
worker call. Context trimming could still return an oversized view, a past token
overrun blocked every later role call, and runner stops lacked an automatic
evidence package. [ADR-017](ADR-017-bounded-native-runtime.md) records the small
extension to the current single-host runner.

## Implemented behavior

`run_native` compiles and runs saved C++ with the owner-pinned compiler/flags and
existing OS isolation. The harness seals source/input blobs, compiler and binary
hashes, exit/timeout/stdout/stderr, and whether original inputs changed. It mounts
the receipt under read-only `inputs/`; the analysis script may summarize those
observed bytes in its clean Python rerun. Critic bundles and offline audits check
the same receipt. Native smoke evidence remains separate from scientific and
backtest validation.

New CLI sessions use an immutable runtime envelope: 64,000 context bytes,
110,000 conservative prompt bytes, up to two provider retries and short bounded
backoff. Owner declarations may set these values. The existing token reservation
and total budget remain authoritative for admission. Director, worker, consultant,
Critic and automatic Curator calls receive the same controls. Known token
overruns are charged and recorded, rather than prohibiting all future calls.
Role allowance/context failures and campaign exhaustion have distinct reasons.

Compaction retains exact fixed constraints/planning and standing owner guidance.
Older experiments retain their identities, labels, configurations, periods,
metrics and result references; larger detail is omitted with a sealed full-view
reference. This prevents forgetting a previously tested configuration merely
because its large observation disappeared from the prompt. Irreducible protected
content is rejected before a provider call. No hidden/future evidence becomes
admissible through compaction.

Only known finalized transient calls are retried against the same frozen context.
The retry receipts, attempts and costs survive another run. Unknown completion is
never resubmitted. Declared refusals have a finite session retry/backoff allowance;
owner pause/cancel and checkpoints remain effective. Strict analysis preflight
rejects an envelope without room for its nested finish/clean-rerun job.

Every normal runner exit now seals a provider-free delivery under the private
store's `deliveries/OWNER/REPORT_HASH/`: `deliverable.json`, `deliverable.md` and
`manifest.json`, with a replaceable `latest.json` pointer. The package distinguishes
workflow completion, research verdict, actual/pending resources, exact exit cause,
recovery history, immutable identity, journal heads and resume point. Failed audits
produce unverified partial reports. Changed sealed deliveries fail integrity
checks. A runner bound, outage or negative finding cannot become an improvement.

## Reproduction

```sh
jarvis --store local/runtime-acceptance autonomy start examples/autonomy/runtime.declaration.yaml
# Use the returned owner once; no manual native probe or successor-session handoff:
jarvis --store local/runtime-acceptance autonomy run OWNER --wall-seconds 60
jarvis --store local/runtime-acceptance autonomy report OWNER
jarvis --store local/runtime-acceptance autonomy replay OWNER
python -m pytest -q tests/test_runtime_controls.py tests/test_native_execution.py
python -m pytest -q
ruff check src tests
ruff format --check src tests
mypy src
```

The fixture uses scripted roles and a fixed arithmetic/toy evaluator. It injects a
charged transient role failure, autonomously executes native arithmetic, regenerates
the output table, retains a negative direction, compares an alternative and freezes
it before matched confirmation. All results are `SIMULATED_FIXTURE`; token units
are simulated, native compilation/isolation and file execution are actually run.

## Executed evidence

- Final retained single-run acceptance: `CLOSED`, 8 decisions, stage gate `PASS`,
  audit `VERIFIED`, offline replay `COMPLETE`, zero replay external calls and zero
  pending resources. One autonomous transient retry; no human execution/handoff.
- Retained report artifact:
  `sha256:0beaed38fcb6d8c01d06c6a88b7569e08d9cc0c2d6a97f51560b289fc2c842fb`.
- Source identity:
  `sha256:65eefdc5066b5d67ad7dcfba0303185cce56344bc799bc8c67ebb7d2977039c2`.
- Working checkout based on `8b3f170a02d700d6c84f416c9ac2c0d65de0dff6` with
  preserved pre-existing changes. No new commit/push/deployment.
- Tests cover known overrun accounting, exact-context durable transient retries,
  unknown completion, a view larger than 196 KB, irreducible prompt admission,
  paused/cancelled/budget/runner/exception exits, finite refusals, legacy identity,
  changed delivery files, missing evidence and unattended native/staged closure.
- Actual native isolation sentinels verify denied host reads, outside writes,
  network binding and process forks; undeclared/changed compilers, custom native
  flags and host paths are refused.
- An early compaction regression removed an old experiment from the context and
  made the scripted provider repeat it. The compact ledger now preserves identity,
  outcome and action index. The affected shipped CLI regression passes after repair.

- Final complete regression: **411 passed in 690.07s** (exit code 0).
- Ruff lint passed, formatting check passed for **156 files**, mypy passed for
  **116 source modules**, and `git diff --check` passed.
- Before the full regression, the targeted runtime/delivery set passed **59 tests**
  and native isolation passed **3 tests**. After the compact-ledger correction,
  **17 focused tests** including the previously failing shipped CLI flow passed.
  These sets overlap; they are not added to the 411-test total.
- All **29 pre-existing `.jarvis/` journal databases** were compared table by
  table against the pre-edit snapshot: row counts and content checksums are
  unchanged, including events, manifest references, jobs, reservations and controls.
  The existing real-study owner control remains `PAUSED`.
- Raw outputs, environment/dependency/source identity, the source backup, negative
  first acceptance, final synthetic store and preservation receipt are retained
  under ignored private `local/runtime-repair-20261026/`. No old research record
  was migrated, reclassified, resumed or overwritten.

## Limits and next action

No paid provider or real Iris/Hermes research, model training, Dream, candidate
adoption or live operation was run. Synthetic acceptance establishes control flow
and execution fidelity, not a trading improvement or deeper research ability.
Prompt-byte admission is not a guarantee of provider token/billing maxima; unknown
usage remains explicitly settled/uncertain under the existing ledger semantics.

A dead machine/process needs a process launcher to restart; this package adds no
daemon. After restart the existing same-session receipt recovery applies. Persistent
outages, changed runtime/evaluator, unreconciled remote work or insufficient budget
must remain partial and actionable. The paused real study is intentionally retained
unchanged. Further real research requires a later owner instruction and a new source
identity declaration; the present work does not authorize resuming it.
