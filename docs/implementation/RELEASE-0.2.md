# Jarvis 0.2 — Real LLM researcher acceptance

Date: 2026-10-18. Status: VERIFIED_WITH_LIMITATIONS.

The first implementation commit is
`1a1213aee079346498c07372c0c29813d7a0da94`. Both complete real-model Hermes campaigns
ran on that committed source with `dirty=false`. Subsequent bootstrap/completion
hardening has separate regression tests and does not change numerical evaluation.

## Actual model acceptance

The local Codex CLI authenticated through the existing local account. No API key
was copied into source, examples, public CI or reports. Actual prompts, answers,
usage, executable identity, model option, hypotheses, controlled designs, numerical
receipts and final analyses remain in the private local evidence store.

| Real workflow | Status | Actual LLM completions | Domain tool invocations | Input + output tokens |
| --- | --- | --- | --- | --- |
| Hermes LONG | CLOSED | 6 | 5 | 120,322 |
| Hermes SHORT | CLOSED | 6 | 5 | 120,647 |

Both workflows independently inspected inputs, reproduced the frozen baseline,
proposed a cancellation-control hypothesis, executed its comparison, observed a
negative candidate result, then retained cancellation and tested a Trading cutoff
intervention. The second proposal explicitly referenced the first measured result
and explained the changed direction. An additional aggregate diagnostic informed
the final analysis. Both models chose STOP themselves, preserved negative and
inconclusive outcomes, and identified missing evidence and independent-validation
requirements. No model training, external event backtest or deployment occurred.

All experiments have agent-authored hypothesis/mechanism/falsification, controls,
parameters, expected observation and criterion. Numerical results are independent
adapter/evaluator artifacts. The LLM's interpretation is advisory. Default model
resolution is labeled CLI default because the observed exec stream does not report
its resolved model name; no model name or monetary price is guessed.

Both histories passed exact decision-time evidence and cost verification. Replay
made zero new provider/tool calls and reproduced all recorded decisions/designs
and charged tokens. Pending reservations were zero at completion. Protected data,
models, metadata and upstream source fingerprints were unchanged before/after.
Preliminary transport/schema checks are separate engineering receipts,
not included in the campaign totals above.

## Regression and operational acceptance

Executed: `pytest -q` → **77 passed**; Ruff lint and formatting passed; mypy passed
in 21 source modules; package dependency check passed. New checks cover real
subprocess JSON transport, schema/evidence/parameter admission, paid repair,
missing/partial usage, finite quotas, proposal adoption without a duplicate call,
negative controlled comparisons, and zero-call recorded-agent replay. Public CI
uses synthetic inputs and scripted providers without credentials.

Simultaneous first use of a new store exposed a WAL initialization race before
one campaign was created or any provider call submitted. The affected start was
then issued once after initialization. The failure was reproduced with four
spawned processes and repaired with a filesystem bootstrap lock; that regression
now passes. No expensive operation was blindly resubmitted.

A later one-step real-model probe supplied a tool ID where an action template ID
was required. Admission rejected it before any domain tool execution and retained
the observed 13,158 tokens. Each request's output schema now binds its exact
request/state IDs, allowed action IDs, visible evidence IDs and agent hypothesis
origin. A fresh real-model check with this binding produced an admitted research
turn and one tool invocation, paused at its declared one-step limit, and recorded
13,208 input-plus-output tokens. Its history verified; recorded-prefix replay
made zero external calls and reconciled the same usage. These engineering probes
are separate from the two complete Hermes campaigns.

The synthetic formal evaluator now also covers a candidate reachable by the LLM
but never selected by Q0. Regression checks exercise that branch through formal
closure in both the ordinary and negative fixture worlds.

## Scope

These are real, fresh LLM research decisions over existing exposed development
data. Numeric trading outcomes remain **dataset replay**, not realistic event
execution or untouched strategy validation. Hermes formal verdict is NOT_RUN;
candidate comparisons remain INCONCLUSIVE. No published private trading rows or
financial performance are included in this receipt.

Generated source execution, model training, external backtester integration,
cross-task memory/capability validation and Dream-RSI policy promotion remain
future milestones. The default remains Q0; real provider calls require explicit
`--agent codex`. Actual consumption and pending reservations are reported separately.
