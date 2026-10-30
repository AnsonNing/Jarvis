# ADR-017 — Native execution and finite recovery inside the existing runner

Date: 2026-10-26. Decision: accepted for synthetic engineering validation.

## Problem

A configured compiler was not a callable worker capability. Large role contexts
could exceed their intended envelope, and an observed reservation overrun stopped
unrelated subsequent calls even when total campaign budget remained. Runner exits
also required a separate operator step to produce a delivery.

## Decision

Extend the existing workbench, role caller and CLI runner. Do not introduce a
service, queue, successor-session controller, new database or general shell tool.

- Owner-declared `AnalysisEnvironment.native` pins the compiler hash and fixed
  flags. `run_native` accepts a saved C++ source only. Compilation and binary
  execution use the existing OS workbench; the binary loses compiler permissions
  and cannot fork. Source/input bytes, compiler/binary hashes, stdout, stderr,
  timeout and exit status are sealed in a trusted receipt. Python can consume the
  read-only receipt in its clean regeneration without spawning a compiler.
- Optional `AutonomySessionSpec.runtime` controls context bytes, prompt admission
  and finite provider retries. CLI declarations enable it for new sessions by
  default. Old persisted specs/environment/configs omit absent optional fields
  and keep their identities. Library callers opt in explicitly.
- Context compaction preserves the owner contract, hypotheses, planning board,
  standing preferences and compact experiment identities/numerical results.
  Larger detail stays in a sealed full-view artifact. Citation IDs remain an
  explicit decision-time subset. Irreducible context/role allowances stop with
  `NEEDS_ATTENTION`, separately from whole-campaign `BUDGET_EXHAUSTED`.
- Reserve before dispatch, charge observed usage, retain overrun events. A past
  known overrun no longer poisons future calls; the unchanged total budget still
  admits or denies each new reservation. Prompt bytes plus headroom are an
  admission heuristic, not a measured tokenizer or guaranteed billing cap.
- Retry only finalized, charged transient provider failures against the same
  frozen context. Existing receipt-based settlement handles proved-ended calls
  without usage. Missing/uncertain receipts remain blocked for reconciliation.
  Provider refusal retries/backoff have one durable session bound, honor owner
  pause/cancel, and never create another session or enlarge a budget.
- One CLI coordinator holds the run lock through provider-free final delivery.
  Closed, partial, paused, cancelled, unavailable and interrupted exits export a
  content-addressed report, markdown and checksum manifest. Audit failures yield
  an explicit unverified partial report. Changed sealed delivery files are
  rejected. Existing immutable histories are not migrated or rewritten.

## Limits

This is local execution fidelity and workflow evidence. A native assertion is
agent-proposed code, not a fixed scientific evaluator or proof of engine behavior.
Regenerating a Python summary from a native receipt does not repeat native work.
The declared compiler hash does not pin the entire operating-system SDK.

A hard process/machine kill cannot make a dead CLI restart itself. Existing
receipt-first resume remains available; a system process supervisor is outside
this change. Persistent outages and unknown remote jobs produce a partial
delivery instead of speculative retries. No real research, trading, provider
benchmark, Dream run, deployment or public push is authorized by this ADR.
