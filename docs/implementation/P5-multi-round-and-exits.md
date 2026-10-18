# New Hermes study and exit contract acceptance

Date: 2026-10-18. Engineering status: VERIFIED_WITH_LIMITATIONS.

The two/three-round coordinator pins immutable new-baseline artifacts and source
identity, registers each child before execution, and seals each report, usage and
zero-call replay. A later campaign pays for a `prior_round_review` tool after
inspection and baseline. That tool carries only verified earlier development
comparisons and provenance-tagged advisory analysis from the same model. The
offline study verifier rebuilds context lineage and costs. Repeating a parameter
comparison from the prior review is rejected. Completed rounds survive a
coordinator interruption without new provider/tool dispatch; uncertain operations
stop for reconciliation.

New CLI commands: `hermes study`, `study-resume`, `study-verify`, `study-report`,
`exit-replay`, `exit-resume`, `exit-verify`. The real study requires explicit
`--agent codex`. CLI IDs are printed before execution for recovery. New records
export with `jarvis schema`. The package is version 0.4.0; old persisted records
remain version compatible.

The quote exit runner admits explicit single complete fills with bid/ask paths,
quantity/units and latency/slippage/fees. It labels results SIMULATED and denies
ambiguous/sparse paths instead of inventing stop/target outcomes from saved PL.
See [data contract](../adapters/exit-and-cancel-data.md). Its durable job test proves
receipt adoption, no uncertain retry, one charge and receipt-only verification.

Executed before real study dispatch: `pytest -q`: **121 passed in 41.43s**; Ruff
lint/format and mypy passed (50 Python files, 36 source modules). Schema CLI
exported successfully. Tests use inert artifact and quote fixtures, not account
authentication or strategy weights. They include two-round causal review,
interruption/resume, duplicate prior comparisons, offline verification, signed
regression risk, independent snapshot clock checks, long/short bid/ask execution,
target gap losses, horizon precedence and missing-capability outcomes. Both actual
new baseline readers were re-admitted after these checks. Risk units are described
from the supplied model contract; regressors are not constrained to probabilities.

Actual real-LLM multi-round acceptance is recorded separately after execution.
Current study scope is exposed calibration development, not independent strategy
validation or Dream-RSI policy promotion. The quote contract has artificial tests;
real exits still require complete postfill data. Full cancellation acknowledgement
and fill-race replay require another backend. No training or external submission.

The actual run exposed an integer/float normalization defect in transcript audit:
a valid JSON integer waiting time was parsed as its declared float, then compared
to raw JSON hashes. Audit now compares both through the admitted versioned schema,
retaining raw receipts unchanged. Actual previously completed histories reverified
without provider dispatch. New-baseline Markdown report rendering also handles
the explicit-artifact format without requiring a legacy README match field.

`study-upgrade` records source/commit provenance at a settled round boundary. It
requires CLOSED children, known settled receipts, sealed reports and unchanged
baseline inputs. Original child worlds are retained. The study lineage verifier
checks the runtime chain and the source admitted when each subsequent child was
created. Active/uncertain children cannot cross this boundary; completed rounds
are audited rather than executed again. An already completed study can be audited
from a newer runtime without starting a provider or tool.

After the audit/recovery correction, executed full `pytest -q`: **122 passed in
41.50s**, plus Ruff lint/format and mypy. The synthetic provider intentionally
returns integer waiting times to exercise raw/typed normalization. A source-change
test requires explicit upgrade, preserves the first round, runs only the remaining
round and verifies the combined history offline. Active or pending child jobs
reject runtime upgrades. Both actual histories that exposed the defect were
reverified without new provider calls before continuing.
