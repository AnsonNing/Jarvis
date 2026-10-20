# Release 0.13 — Research recovery and continued missions

Invalid Curator references could stop a completed research episode, while an outer
checkpoint retained a successful child status after the runner failed. Finite
reviewed programs also needed separate task-level continuation decisions. This
release repairs these boundaries while preserving original evidence, rejected
answers, costs and independent stop recommendations.

## Executed acceptance

The fixed-environment regression passed **275 tests in 342.66 seconds**. Ruff lint
and formatting passed for 109 Python files and mypy passed for 82 source modules.
The installed package is 0.13.0 and dependency checks passed before testing.
The complete `lab session-fixture` CLI closed with VERIFIED quota accounting and
zero pending resources. It made zero real provider/backend calls.

Seven new tests exercise known-invalid Curator correction with preserved receipts,
exact-reference schemas, historical recovery without rewriting or re-execution,
refusal to retry unknown usage, decision-time Critic execution evidence, actual
continuation into a second session, a concrete final stop reason, exception-finalized
notebooks/checkpoints and resumable runner limits. Earlier synthetic Dream, policy
adoption, cost comparison, Critic, canary rollback and Research Lab acceptance
remain in the regression.

## Implementation

- Evidence enums and runtime reference checks admit valid structured answers.
  Known invalid answers get at most two separately receipted corrective calls.
  Unknown operations retain reservations and are never blindly retried.
- Historical experience recovery is a separate successor requiring a closed
  measured program, complete receipts, immutable lineage and no pending work.
  The original failed episode remains unchanged.
- Critic sees proposed source diffs before execution and actual compile/result
  lineage after admission. Offline audits reconstruct decision-time visibility.
- Sessions can explicitly reuse sealed compatible integration evidence while
  retaining negative observations and critique.
- A mission controller selects new directions through fixed templates with shared
  costs, deduplication and unchanged selection constraints. Stopped programs remain
  stopped; subsequent research is a newly declared session.
- A single-owner runner atomically checkpoints task-level state. Child success
  cannot become task completion. Exceptions finalize diagnostics and notebooks;
  invocation limits preserve a resumable mission.

Real strategy evidence remains private. This release does not demonstrate financial
gain, full hidden-caller causality, arbitrary skill acquisition or independent
Dream effectiveness. Exposed dates, missing execution data and remote cost limits
are retained. Contracts are in [P14 research recovery](P14-research-recovery.md).
