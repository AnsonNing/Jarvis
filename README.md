# Jarvis

**Jarvis — Autonomous, Evidence-Driven, Self-Improving Research Agent System**

Jarvis is a planned domain-extensible research platform. Its first target domain is quantitative/high-frequency trading research; future domains must be introduced through explicit adapters, not hard-coded into the research engine.

Jarvis is **not** a live trading bot, not a trading engine, and not currently a completed implementation. The repository initially contains an implementation-ready **system specification for local Codex**. The user will provide data schemas, strategy details, training executors and backtesting interfaces later.

## Mission / 願景

Given a research question, Jarvis should autonomously decompose the problem, diagnose the baseline, generate hypotheses, plan and execute bounded experiments, analyze evidence, revise research direction, and produce reproducible results. Across campaigns it accumulates reusable knowledge, methods, code capabilities, and failure lessons. Given enough well-recorded discovery histories, Dream-RSI improves **how Jarvis researches**, not necessarily the weights of any trading model.

Three separate improvement loops:

1. **Strategy improvement** — improve a domain artifact (e.g., a model, threshold, execution decision).
2. **Capability improvement** — validate and reuse new tools, methods, and research skills.
3. **Meta-research / Dream-RSI** — learn more efficient exploration, branch allocation, and stopping policies from discovery trees.

## System boundary

**Inside Jarvis:** research orchestration, agent decision protocols, structured evidence, capability registry, policy-driven experiment selection, history/replay, policy evaluation, resource accounting, and reporting.

**Outside Jarvis for now:** proprietary market data, strategy logic, training pipelines, backtest engines, broker/exchange connections, real trading deployment. They connect later through versioned adapters and may remain in local/private infrastructure.

No live orders or access to production credentials is permitted. Generated research code executes only in isolated development workspaces.

## Read first

| Document | Purpose |
|---|---|
| [AGENTS.md](AGENTS.md) | Start here when implementing with local Codex |
| [00 — Master plan](docs/00_MASTER_PLAN.md) | Goals, boundaries, success criteria and non-goals |
| [01 — Architecture](docs/01_ARCHITECTURE.md) | Layers, components, decisions and repository layout |
| [02 — Research lifecycle](docs/02_RESEARCH_LIFECYCLE.md) | Durable state machine, branching, lifecycle and failures |
| [03 — Agents](docs/03_AGENT_ROLES.md) | Roles, collaboration, tool permissions and prompting |
| [04 — Contracts](docs/04_CONTRACTS.md) | Required schema definitions and I/O protocols |
| [05 — Dream-RSI](docs/05_DREAM_RSI.md) | Discovery world, exact replay, policy improvement and promotion |
| [06 — Memory/capabilities](docs/06_MEMORY_CAPABILITIES.md) | Persistent memory, retrieval, skill internalization and transfer |
| [07 — Evaluation](docs/07_EVALUATION.md) | Strategy-level and research-policy-level evaluation |
| [08 — Runtime/security](docs/08_RUNTIME_SECURITY.md) | Job queues, resumability, safety, budgets and isolation |
| [09 — Roadmap](docs/09_ROADMAP.md) | Phased work packages, dependencies and acceptance |
| [10 — Tests](docs/10_TEST_PLAN.md) | Deterministic contracts, regression and end-to-end fixtures |
| [11 — Codex implementation](docs/11_CODEX_EXECUTION.md) | Exact sequence of local implementation steps |
| [12 — References/decisions](docs/12_REFERENCES_DECISIONS.md) | Source mapping, architectural decisions and limits |
| [13 — Adapter boundary](docs/13_ADAPTER_BOUNDARY.md) | Future training/backtest integration contract |
| [14 — Research techniques](docs/14_RESEARCH_TECHNIQUES.md) | VOI, tree search, cost-aware experimentation, meta-learning alternatives |
| [中文導讀](docs/zh/README.md) | Jarvis 完整規劃的中文架構導讀 |
| [Research task example](examples/research_task.example.yaml) | Domain-neutral synthetic example |

## Development policy

- Plan-first, test-first, small work packages. Do not claim unimplemented modules are operational.
- Every decision/action/result has stable IDs and explicit evidence visibility.
- Replay of an unexecuted branch must return UNKNOWN; it must never fabricate a result.
- Evaluator, locked-test cohorts, and budgets are frozen and separated from research agents.
- Memory must carry evidence, applicability, lineage and negative results; a vector search hit is not a scientific result.
- Improve policies using independent **research tasks**; a higher historical replay score alone is not proof of transfer.
- Never upload proprietary strategy code, confidential datasets, model weights, credentials or machine-specific paths to this public repository.

## Existing references

- [Dream-RSI project](https://dream-rsi.com/) · [paper](https://arxiv.org/abs/2609.14858)
- [Existing RSI research-agent project](https://github.com/AnsonNing/RSI) (may require private access)
- [Existing EEHEMT policy/replay project](https://github.com/AnsonNing/EEHEMT) (may require private access)

Documents are design targets, not evidence that Jarvis already supports a feature.
