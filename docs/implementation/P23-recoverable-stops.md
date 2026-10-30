# P23 — Research that keeps going: in-run recovery and resumable stops

Real Hermes sessions (2026-10-24..27) never ran to completion. Every stop came from the
runtime, not from a research conclusion:

| Stop | Cause | Before | Now |
|---|---|---|---|
| Director rejected 3 times on one step (duplicate hypothesis IDs, repeated baseline, non-analyzable inputs) | strict validators, LLM repeating the same form | terminal `NEEDS_ATTENTION`; a new session was needed | two automatic recovery rounds in the same run, each with all earlier rejection reasons and an instruction to choose a simpler valid step; then a resumable stop |
| Provider ended without usage (900 s timeouts) 3 times | long high-effort calls | terminal | same rounds, with growing backoff; then resumable |
| `CONTEXT_ENVELOPE` after one decision | owner guidance, preference lessons and skill descriptions are kept verbatim during compaction; with a 28 KB contract they no longer fit 80 KB | terminal | last-resort memory view: sealed full-view ref, the last eight guidance items and owner preferences shortened to 300 characters, skill names |
| Role call / token overrun | one call used more than its reservation | terminal | (P21) recorded, not a stop |

`resume` on a recoverable stop records a manual `ROLE_RECOVERY` and continues the same
session with its pinned source, runtime and budget; other `NEEDS_ATTENTION` stops are still
refused. Default `RuntimeControls` now allow 120 KB contexts and 175 KB prompts (prompts are
sent on stdin), and the compaction target is clamped below both the prompt cap and the token
reservation so preflight cannot refuse a context compaction accepted.

All allowances stay finite: a persistent fault ends in a recoverable stop after
`(1 + AUTO_RECOVERY_ROUNDS) × allowance` attempts; budgets, decision bounds and unknown remote
work (reconciliation) are unchanged.

Tests: `tests/test_role_feedback.py` (automatic rounds, resumable stop, resume spends again),
`tests/test_runtime_controls.py` (memory fallback, context clamp, CLI resume only for
recoverable stops).
