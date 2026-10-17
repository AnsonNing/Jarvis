# Jarvis

**Jarvis — Autonomous, Evidence-Driven, Self-Improving Research Agent System**

Jarvis is an executable, domain-extensible research platform. Version 0.1 provides a durable local research kernel, a deterministic adaptive synthetic campaign, and an opt-in adapter for existing local Hermes data and model artifacts. Domain details stay in adapters.

The active researcher is **deterministic Q0 with structured logical roles**. A provider-neutral protocol and mock provider exist; live LLM research, generated-code execution, capability transfer and Dream-RSI policy improvement are still future work. Hermes operations reproduce and diagnose existing artifacts; they do not establish a new strategy improvement or perform live trading.

## Run locally

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
pytest -q
jarvis task validate examples/research_task.example.yaml
jarvis campaign start examples/research_task.example.yaml --max-steps 2
# Use the actual campaign ID printed above:
jarvis campaign resume <campaign-id>
jarvis campaign report <campaign-id>
jarvis history verify <campaign-id>
jarvis rsi replay <campaign-id>
```

The campaign records negative experiments, changes direction based on observed
results, freezes its candidate, runs a separate synthetic locked evaluation and
produces evidence-linked reports. Receipts survive an abrupt coordinator exit;
uncertain started jobs require reconciliation. The default private store is
`.jarvis/`; use `jarvis --store <private-directory> ...` to choose another.

## Use existing Hermes data

Set `HERMES_ROOT` and `HERMES_PYTHON` privately to the provided repository and its
Python environment. Raw data, models, local paths and numerical reports stay local.

```sh
jarvis hermes inspect --root "$HERMES_ROOT" --side long
jarvis hermes start --root "$HERMES_ROOT" --side short
# If saved order decisions do not match the current model metadata, materialize
# frozen-model predictions into a NEW private folder, then use it explicitly:
jarvis hermes materialize --root "$HERMES_ROOT" --side long \
  --python "$HERMES_PYTHON" --output .jarvis/materialized-long
jarvis hermes start --root "$HERMES_ROOT" --side long \
  --ledger-root .jarvis/materialized-long
```

The default reader never opens model pickles. The explicit materialization worker
uses trusted local source/models, disables model fitting, redirects loader writes,
verifies checksums and reproduces the declared baseline. It preserves upstream
files. Existing exposed evaluation data is tagged development/replay; formal
strategy validation reports NOT_RUN. See the
[integration receipt](docs/implementation/HERMES-local-integration.md).

Implementation status and verified limits: [PROGRESS](docs/implementation/PROGRESS.md)
and [release acceptance](docs/implementation/RELEASE-0.1.md). The documents below
remain the broader P0–P5 design, not a claim that every planned capability exists.

## Mission / 願景

Given a research question, Jarvis should autonomously decompose the problem, diagnose the baseline, generate hypotheses, plan and execute bounded experiments, analyze evidence, revise research direction, and produce reproducible results. Across campaigns it accumulates reusable knowledge, methods, code capabilities, and failure lessons. Given enough well-recorded discovery histories, Dream-RSI improves **how Jarvis researches**, not necessarily the weights of any trading model.

Three separate improvement loops:

1. **Strategy improvement** — improve a domain artifact (e.g., a model, threshold, execution decision).
2. **Capability improvement** — validate and reuse new tools, methods, and research skills.
3. **Meta-research / Dream-RSI** — learn more efficient exploration, branch allocation, and stopping policies from discovery trees.

## System boundary

**Inside Jarvis:** research orchestration, agent decision protocols, structured evidence, capability registry, policy-driven experiment selection, history/replay, policy evaluation, resource accounting, and reporting.

**Outside Jarvis:** proprietary market data and strategy source, training pipelines, event-backtest engines, broker/exchange connections and trading deployment. The current adapter reads declared local artifacts and optionally invokes trusted frozen-model inference. Data, weights and private source remain in their original local infrastructure.

No live orders or access to production credentials is permitted. Generated research code execution is disabled until a real isolation boundary is implemented.

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
