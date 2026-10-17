# Dream-RSI CLI and failure classification checkpoint

Date: 2026-10-18. Status: VERIFIED_WITH_LIMITATIONS.

Added working collect/dream/study/resume/report/verify/status/coverage/replay-tree
commands alongside the original replay command. JSON schemas and a bounded
settings example are exported. Public CI exercises the complete synthetic study
and settings negotiation without authentication.

Executed package installation, schema export, Ruff lint/format, mypy, dependency
check and the full test suite. Observed **102 passed in 41.51s**; 43 Python files
formatted; type checking passed in 31 source modules; dependency check passed.
The CLI subprocess test executes three rounds, renders its report, verifies the
rounds/registry/canary and resumes without repeating completed work.

Additional checks distinguish absent data and tool failure from a scientific
negative, prevent unknown LLM usage from triggering a second request, and preserve
actual/pending costs separately. The sealed canonical pool and one-shot family
ledger prevent silently substituted or recycled validation cohorts.

No real policy-developer call was submitted at this checkpoint. The next receipt
will record complete committed-source fixture and real-developer studies, actual
proposals, independent target attainment, paid usage and any regression/rollback.
The study may legitimately finish without promoting a new policy.
