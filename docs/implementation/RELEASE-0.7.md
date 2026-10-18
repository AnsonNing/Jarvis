# Release 0.7 acceptance

Date: 2026-10-18. Scope: owner-bound entry/cancellation rules, trading schedules,
quantity and position limits, compile admission and preservation of an active
predecessor runtime. Private source, models, scaffolding, numerical market
results, credentials and internal paths stay in ignored local storage.

## Executed engineering acceptance

`pytest -q`: **164 passed in 81.59s**. Ruff lint and formatting passed for
76 Python files; mypy passed for 56 source modules. An isolated 0.7.0 editable
installation and schema export completed. CI additionally runs the compiled
strategy-design fixture with no account authentication or remote service.

`research design-fixture` completed two adaptive epochs, one artificial baseline
and four candidate executions. It exercised entry rules, cancellation rules,
session end and position limits, preserved an objective-negative candidate and
verified both queues and their program offline. The declared director is an
engineering control, not a real LLM. Artificial source was actually compiled and
executed against a fixed toy evaluator. Remote Iris submissions: **zero**.

The real Codex direction role subsequently completed two adaptive batches on the
same compiled artificial backend: three novel candidates and one actual baseline
execution, two completed provider calls, **31,601 observed tokens**, and zero
pending resources. The next epoch reused its baseline. After observing a negative
entry-rule result, the LLM proposed the complementary score interval while
explicitly withholding a conclusion on a cancellation result not yet visible to
that decision. Queue, role and program evidence verified offline. This proves
the expanded structured interface can support actual LLM hypothesis, negative
analysis and revision; the objective remains artificial and proves no market gain.

Tests cover the closed DSL, unknown/unavailable signals, source-unit bounds,
strict comparisons, canonical predicate ordering, nonfinite inputs, protected
region lines, baseline entry gates, expiry precedence, clock conversion/hour
boundaries, session ordering, compiler/profile drift, failed compilation without
submission, receipt reuse and offline verification without compiler calls.
Predecessor tests cover an active lease, unsettled/unknown work, user pause/cancel,
compatible history retention, shared quota, idempotent handoff, successor identity
saved before dispatch and the predecessor lease retained during dispatch.

The actual owner-provided strategy regions also passed ten local compile-only
checks across both sides. Two additional owner-source programs executed with
artificial inputs under C++17 address/undefined-behavior sanitizers. They verified
the retained entry gate, extra clock gate, expiry-before-sampling, single cancel
per call and fixed feature width. These tests did not train models or submit
market backtests and do not satisfy upstream model acceptance by themselves.

## Persisted-history compatibility

Existing 0.5 real-LLM artificial program evidence, the 0.5 Hermes Dream canary
rollback study and 0.6 continuous research/quota evidence reverified offline with
zero new provider/backend calls. Batch and prospective-catalog comparisons now
normalize both recorded artifacts through their declared types. Newly added
nullable settings do not rewrite original receipts or invalidate historical
identities. A regression fixture stores a batch with only legacy settings and
verifies it through the expanded contracts.

## Operating boundary

An active predecessor keeps its original checkout and environment. The new
watcher runs from an isolated checkout, waits for its predecessor lease to be
released, refuses unsettled/unknown work and saves its own study before dispatch.
The pause/link operation is atomic. Matching historical source/model/side/period
evidence and the same account quota ledger survive the transition. No active job
is upgraded, migrated or resent. See [P8](P8-strategy-design.md) for commands and
the owner profile contract.

Real research remains gated on the complete accepted model package and confirmed
quota evidence. Entry-only PL is primary; risk remains explicit without a hidden
penalty. Owner-profile syntax/guard tests do not prove live fill/acknowledgement,
full costs, capacity, independent profitability, L2 capability learning or L3
research-policy improvement. Current feature semantics and model inputs remain
fixed; unsupported order-age or future/outcome signals are not synthesized.

## C++ driver portability correction

The first Linux CI run exposed a compiled-fixture defect: resolving a `clang++`
symlink to `clang` changes the compiler driver's C++ standard-library linkage.
The fixture now retains the selected driver's absolute path. Its executable
bytes remain checksum-pinned through that path. Existing live runtime checkouts
were preserved; the correction was developed and tested in a separate checkout.

A regression test creates a driver symlink, builds a program using the C++
standard library and executes the resulting binary. `pytest -q`: **165 passed in
85.78s**. `ruff check src tests`, `ruff format --check src tests` (76 files),
`mypy src/jarvis` (56 source modules) and `git diff --check` passed. These checks
use the pinned dependencies and do not submit Iris work or repeat provider calls.
Linux CI is a separate check after publication; the local result alone does not
establish its outcome.
