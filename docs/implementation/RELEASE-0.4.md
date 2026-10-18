# Release 0.4 acceptance

Date: 2026-10-18. Scope: explicit new frozen Hermes baseline admission, causal
multi-round real development research, typed cancellation controls and a separate
quote exit simulation contract. Domain data/models/results remain private.

## Actual baseline fidelity

Both supplied new-schema sides were materialized using the trusted upstream
runtime with fitting disabled and loader writes redirected to a new private
scratch/output. Every retained entry/event was scored with frozen and portable
models; maximum prediction difference was zero. Original feature-only thresholds
were reproduced. The reader independently reconciled the four saved calibration
policies, episode lineage, independent snapshot clock decoding, legal prefill
times, embargo and one entry-PL contribution per episode. Protected fingerprints
were unchanged after materialization and after all real research.

The first materialization encountered a Timestamp JSON serialization error and
exited without publishing output; a repaired explicit attempt passed. No training
or external backtest was submitted. Full detail: [admission](P5-new-baseline-admission.md).

## Actual autonomous development workflow

Two real LLM rounds per side completed: four CLOSED child campaigns, 22 observed
provider calls, 468,964 observed tokens, 18 local tool calls, zero pending cost.
Each reproduced the frozen baseline. Both second rounds paid for prior review of
sealed first-round findings. Six distinct parameter comparisons were measured,
including a joint intervention motivated by preceding single-factor evidence.
The researcher stopped when an additional-factor hypothesis was unsupported and
recorded concentration and worst-day tradeoffs rather than treating higher total
PL as an unconditional improvement. All numerical strategy results stay private.

Original inference/research code: `a8193e8e808c3dbf4b4171becd5e2cbded9b92e6`.
The actual run revealed a raw integer versus typed-float audit mismatch. Recovery
code: `18e5c6ea777cf49a0488846fb41fec4018308f43`. Both revisions were committed,
clean for implementation provenance, tested and pushed before their subsequent
work. The first three child campaigns retained the first revision's immutable
worlds. Explicit runtime-upgrade events admitted the repair at fully settled round
boundaries; only the remaining child used the repair runtime. No completed
provider/tool job was resubmitted and raw receipts were not rewritten.

Study/report/history verification and exact replay passed while provider/domain
execution, subprocess creation and network connection were blocked. Replay
reported COMPLETE with zero external calls. Job counts were unchanged before and
after this audit. A prior-version actual Hermes LLM history also reverified after
the parser/audit changes. See [workflow and recovery](P5-multi-round-and-exits.md).

Calibration was already exposed; the original frozen subsequent-period protocol
was preserved. Formal real strategy validation, policy promotion, external
backtester submissions and training are NOT_RUN. These runs establish an adaptive
research workflow and its accounting, not general strategy or Dream-RSI efficacy.

## Exit and cancellation interfaces

The new adapter admits risk deltas, minimum prefill order age, bounded all/any
score/age rules and explicitly controlled joint comparisons. Unsupported price or
spread fields fail capability negotiation. Prefill cancellation still assumes
immediate success conditional on historically filled baseline orders.

A separate versioned CLI/job runner simulates exits from explicit single complete
fills and bid/ask paths, with latency, adverse slippage, fees, horizon precedence
and maximum quote gaps including the first post-horizon execution quote. Missing
coverage records REQUIRES_CAPABILITY without a financial value. Receipt adoption
is exactly once; unknown started jobs require reconciliation. Results are labelled
SIMULATED, not observed fills. No actual supplied postfill price path is available.
Full missing data and event-backend requirements: [contract](../adapters/exit-and-cancel-data.md).

## Synthetic Dream-RSI reacceptance

Executed `rsi study --developer fixture` on a new private store using the repair
revision. Three rounds completed: coverage gap, branch collection with independent
replay and fresh paired fixture tests, then plateau. The subsequent unseen
condition-shift canary regressed and triggered rollback to Q0. `rsi verify` returned
VERIFIED for all three rounds and canary. This run used the deterministic fixture
developer with zero LLM calls; prior real-developer acceptance remains scoped in
the 0.3 receipt. Synthetic success/rollback is not evidence of real trading gain.

## Validation

Executed final `pytest -q`: **123 passed in 39.42s**. Ruff lint/format passed for
50 Python files; mypy passed for 36 source modules. Schema export passed. The
final additional quote test rejects a long gap hidden after the horizon. Public CI uses inert artifact
and quote fixtures with no authentication or weights. The normal reader is tested
with intentionally invalid pickle bytes to prove it does not execute model files.

The actual `hermes exit-replay` CLI also completed on artificial quotes with a
VERIFIED durable receipt and zero pending cost. Schema export was repeated after
the final interface changes. All four real histories/replays reverified from the
final source while live execution was blocked, with unchanged job counts and
protected inputs. No additional LLM call was used for these checks.
