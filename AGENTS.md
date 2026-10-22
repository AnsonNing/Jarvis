# Jarvis — Instructions for Local Codex

## Assignment

Implement the Jarvis multi-agent autonomous research platform from the specifications in docs/. The **agent system** is the deliverable. The market data, training system and backtester will be provided later. Do not fabricate those systems or bake in assumptions about any private strategy.

Read in this order: README.md, docs/00_MASTER_PLAN.md, docs/01_ARCHITECTURE.md, docs/02_RESEARCH_LIFECYCLE.md, docs/04_CONTRACTS.md, docs/05_DREAM_RSI.md, docs/06_MEMORY_CAPABILITIES.md, docs/07_EVALUATION.md, docs/08_RUNTIME_SECURITY.md, docs/09_ROADMAP.md, docs/10_TEST_PLAN.md, docs/11_CODEX_EXECUTION.md. Read others as relevant.

## Hard invariants

1. This repository is public. Never commit secrets, private data, internal server paths, proprietary strategy logic, full copies of private RSI/EEHEMT modules, or actual trading records.
2. All strategy/tool integrations are dependency-injected **adapters** with explicit capability negotiation; before integration use the deterministic synthetic research fixture.
3. Every run has an immutable declared task/world identity, policy identity, evaluator identity, dataset/split identity when applicable, resource budget, and manifest of versioned artifacts.
4. The Research Agent may propose code and experiments; only a trusted execution harness may run the admitted commands. The agent must not alter the fixed evaluator, formal test conditions, or committed evidence.
5. Preserve time-ordered decision records. An agent may see only evidence available at that decision time.
6. Every job transition and expensive operation is durable, idempotent when retried, resumable, and accountable for attempts and resource cost.
7. Discovery-tree replay executes **zero** external tools/LLM calls; unrecorded action branches return UNKNOWN_ACTION / INSUFFICIENT_COVERAGE, never estimated outcomes labeled as observed.
8. Never conflate strategy-policy gains, capability gains, and research-exploration-policy gains. Promotion uses separate held-out evaluations and subsequent fresh campaigns.
9. Do not connect to brokers, exchanges, live order interfaces or production credentials. No direct promotion to live trading.
10. Negative, inconclusive, unavailable and infrastructure-interrupted outcomes are first-class records, not silently discarded.
11. Human-provided specifications, code and examples must be provenance-tagged; do not attribute them to autonomous discovery.
12. A paper summary, new code file, or an improved development score alone does not establish a validated reusable capability.

## Implementation workflow

- Follow work packages in docs/09_ROADMAP.md and acceptance checks in docs/10_TEST_PLAN.md. Start with P0 contracts/storage and a synthetic fixture, then P1 research cycle. Defer full Dream-RSI training until P3/P4 prerequisites are satisfied, but collect replay-quality events from the first run.
- Make one cohesive change per branch/commit; add or update automated tests with each feature.
- Prefer a modular Python library with CLI commands. Avoid unnecessary services, UI, database infrastructure and metaframework dependencies.
- Use type hints, explicit Pydantic/dataclass boundaries, schema versioning, typed status enums and deterministic JSON canonicalization. Validate inputs and deny unexpected actions.
- Preserve backward-compatibility for persisted schemas or provide a versioned migration.
- Record reproducibility: commit, dependencies, source checksums, tool versions, seeds, objective and evaluator IDs, and cost.
- Document each completed work package in docs/implementation/ with command, evidence, test output, limitations and next action; update a progress file only with **verified** outcomes.
- Never report tests, GPU runs or external-tool integrations as completed unless actually executed.
- If blocked by the missing real backtester, proceed with the synthetic fixture and produce an adapter requirement, not a fake financial result.
- Avoid blanket rewrites of existing code or a policy allowing the agent to dynamically edit its own permission boundaries.

## Non-goals until explicitly requested

Actual trading strategies, BTC/other market data parsing, feature formulas, model architecture selection, exchange connectivity, real strategy backtests, live order execution, unreviewed self-modifying runtime, model-weight fine-tuning of the research LLM, dashboard/UI, distributed microservices.

## Codex starting task

Follow docs/11_CODEX_EXECUTION.md. The first milestone is a working CLI-driven synthetic research campaign that can resume after interruption, record a valid discovery tree, store both positive and negative evidence, and output a report **without** an external training/backtest system. The fixed Q0 exploration policy and Replay-compatible event shape must exist immediately. Report real test evidence and any deviations from the design.

## Operating research sessions for an owner

When a conversation asks you to start, steer or report on Jarvis research (not to change Jarvis
code), follow [docs/OPERATOR.md](docs/OPERATOR.md): begin with `jarvis autonomy brief`, answer from
`status`/`report`/`deliverable`, record the owner's ideas with `advise` or `memory note`, and run
sessions in the background.
