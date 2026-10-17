# 13 — Future Domain / Backtest Adapter Boundary

## 1. Scope

The user will later supply strategy definitions, market-data schemas, existing model-training code, backtest APIs and deployment environment. **No such interfaces are available yet.** Jarvis should be useful against synthetic research fixtures meanwhile.

This specification intentionally defines *how* to connect future tools, not what the user's strategy or target must be.

## 2. Adapter contract and capabilities

An adapter advertises:
- adapter_id, semantic version, source commit/hash, deterministic behavior/seed support;
- domain and task types accepted;
- supported input artifacts and column/unit schemas;
- available actions (INSPECT, TRAIN, CALIBRATE, BACKTEST, COMPARE, DIAGNOSE, etc.);
- estimated resource class and actual cost receipt fields;
- supported temporal split and pre/post-event observation availability;
- whether it can replay counterfactual decisions, and fidelity/assumptions;
- execution timeouts and idempotency properties;
- result schema, split identity, outcome availability and error taxonomy;
- permissions and data-access policy.

Missing support is an explicit CAPABILITY_UNAVAILABLE, not silent fallback. Adapter should never redefine primary metric, training/selection/test partitions or formal evaluator after World is frozen.

## 3. Logical tool API

~~~python
describe_strategy() -> StrategyDescriptor
inspect_data() -> DataDescriptor
inspect_baseline() -> BaselineDescriptor
propose_experiment(spec: ExperimentSpec) -> AdmissionResult
train_model(request: ToolRequest) -> ToolResult
generate_oof(request: ToolRequest) -> ToolResult
select_candidate(request: ToolRequest) -> ToolResult
run_backtest(request: ToolRequest) -> ToolResult
evaluate_candidate(request: ToolRequest) -> EvaluationResult
analyze_trades(request: ToolRequest) -> ToolResult
~~~

These are logical names, not asserted existing functions. The local owner/Codex will map them to actual notebook scripts/process entry points. Tool descriptors define arguments, split scope and outputs; required functions depend on actual supported workflow.

## 4. Temporal semantics required later

The adapter must declare:
- information timestamp available to each decision, event time and receive time;
- order/entry/cancel/exit linkage keys and cardinality;
- training target formation and any feedback path between downstream decisions;
- OOF generation, time embargo/purging and exact training window per fold;
- threshold fitting scope, calibration groups and budget;
- fill/cancel/order latency model, fees/slippage/capital assumptions and execution fidelity;
- inability to observe counterfactual fills and policy-dependent selection effects.

Strategy research can encompass entry, cancellation, take-profit/stop-loss and risk controls without prebuilding a separate agent for each. These are experiment topics and domain capabilities.

## 5. Required future integration questionnaire (not blocking P0–P4)

1. What are the executable entry points for data inspection, model training, candidate selection and backtest?
2. What Python version/runtime/OS/GPU context and isolation policy does each tool need?
3. What are training and decision timestamps and where do labels/OOF targets come from?
4. Which data families/splits are safe for development, selection, locked test and forward evaluation?
5. Which objective/risk constraints must be frozen before autonomous research?
6. Which actions can replay historical events faithfully; what execution counterfactuals remain unknown?
7. Which artifact formats represent models, predictions, orders, cancel snapshots, PnL and diagnostics?
8. What compute/billing limits, secrets and data-handling boundaries apply?
9. Can tools be resumed by stable job ID and do they emit actual cost/attempt receipts?
10. What user-authorized publication boundary applies to analysis and outputs?

Each answer should produce a versioned DomainAdapter specification, not free-form prompt text hidden inside agent context.

## 6. Integration admission test

Before a real campaign:
- adapter dry run validates paths/permissions/schema and snapshot hashes;
- baseline reproduction from known artifacts reaches declared numerical tolerance;
- one known job can be resumed/idempotently reconciled;
- tool reads only permitted time/split;
- evaluator independently computes a known test metric;
- experimental code and full artifacts remain private/local;
- smoke backtest is labeled ENGINEERING_SMOKE, not strategy efficacy.

## 7. Explicit phase boundary

**P0–P4:** synthetic domain proves the Agent System, Memory and Dream-RSI without external trading backend.

**P5:** adapter maps to real user tools; scientific strategy conclusions require genuinely executed backtests and frozen data/evaluation contracts. If user provides only partial interface details, Jarvis should diagnose missing capabilities and continue useful generic research, not invent market results.
