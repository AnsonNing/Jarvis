# P1 real structured research provider

Date: 2026-10-18. Status: VERIFIED_WITH_LIMITATIONS.

Implemented the real Codex CLI provider, closed ResearchTurn schema, bounded
experimental parameters, durable agent jobs, actual token settlement, paid
rejection with at most one repair, provider identity pinning, evidence admission,
and recorded-agent replay without new calls. No new SDK dependency is required.

The installed CLI was checked through version/help/login-status. A real structured
availability request completed, followed by one real synthetic research decision
and tool execution. The latter returned valid hypothesis/design/analysis JSON,
recorded 13,634 input-plus-output tokens, paused at the requested step boundary,
and passed history verification. Its private prompt/response/usage remain local.
This is transport/one-step engineering evidence, not a completed Hermes study.

Automated validation executed:

```sh
ruff check src tests
ruff format --check src tests
mypy src/jarvis
python -m pytest -q
jarvis schema schemas
```

Observed result: 72 tests passed; type checking passed in 21 source modules.
Tests use a subprocess transport fixture and a scripted provider for reproducible
CI. They cover stale/unknown evidence, invalid parameters, paid rejection/repair,
unknown completion/usage, actual vs pending cost, call/token caps, receipt adoption,
negative/unchanged Hermes comparisons, and replay that refuses unrecorded designs.

The LLM controls hypothesis, one-variable experiment parameters, analysis and
continue/branch/stop decisions. Hermes keeps mandatory inspection/baseline guards,
then offers independent diagnostics and frozen-model comparisons. The deterministic
evaluator owns numerical outcomes; the LLM's result interpretation is advisory.

The private provider prompt contains bounded development aggregates and prior
proposals, with full raw research artifacts retained locally. No private paths,
raw orders, datasets, model files or authentication are published. Public CI
uses fixtures only. Codex is explicitly opted in with `--agent codex`.

Remaining acceptance: complete real-model Hermes campaigns, capture their actual
adaptation and resource receipts, and verify the final published commit in CI.
Those results will be recorded separately after execution. Arbitrary code
generation, model training, external event backtests, pristine strategy tests,
capability transfer and Dream-RSI policy promotion remain out of this milestone.
