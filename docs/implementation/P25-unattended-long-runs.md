# P25 — Unattended long runs: remove limits that stopped real sessions

## Why

Every stop of the real long-running sessions so far was examined from the store (role rejections,
failed actions, partial stops). Besides the faults fixed in P23/P24, these remained:

| Stop | Cause | Change |
|---|---|---|
| Successor could not use its predecessor's collection | A session paused at an owner checkpoint is never consolidated into memory, so a session declared after it does not see its tables | `autonomy start` records the trusted facts of every ended or paused session of the domain that is not yet in memory before pinning it |
| Session paused waiting for the owner | The Director may STOP at a checkpoint to ask a question | `owner_checkpoints: false` in a declaration rejects checkpoint STOPs with an instruction to decide and continue |
| Backtest result discarded | One evidence table above the blob limit raised and failed the whole result | Oversized tables are skipped and listed in `tables_not_stored`; the result stands |
| Analysis failed | The worker's provider ended at its output limit three times | Six attempts, each asking for a smaller step |
| Training killed after 2 h | `WorkbenchLimits` capped wall and CPU time at 7200 s; a seven-month joint fit runs for hours, serially (the sandbox blocks the IPC joblib needs for processes) and with multithreaded CPU time | Caps raised to 72 h wall and 720 h CPU; a failed training now says TIMEOUT and the limits instead of the last stderr line |
| Declarations refused | Caps on periods (12), stages (8), grants, experiments per backtest (16), decisions and calls | Raised: periods 48, stages 32, experiments 32, setting changes 64, training runs 2000, implementations 1000, analysis steps 120, decisions 10000, calls 50000 |

Kept on purpose: unknown paid results still stop for reconciliation; budgets, the runtime and
executor identity pins, the evaluator and data-exposure boundaries are unchanged.

## Evidence

- `tests/test_research_planning.py::test_unattended_session_rejects_owner_checkpoints`
- `tests/test_research_planning.py::test_a_new_session_remembers_a_predecessor_paused_at_a_checkpoint`
- `tests/test_autonomy.py::test_oversized_backtest_tables_are_noted_not_fatal`
- `tests/test_autonomy_adapters.py::test_command_trainer_names_a_timeout`
- Full suite, ruff and mypy: see the commit.

## Limitations

Synthetic tests only. Facts are recorded once per session: if a session recorded at a successor's
start later resumes, its later results need `autonomy memory consolidate` again. Lessons and skills
still need an agent consolidation.
