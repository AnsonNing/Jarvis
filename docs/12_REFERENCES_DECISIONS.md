# 12 — References, Provenance and Architecture Decision Records

## 1. Source references

- **Dream-RSI, Zheng et al. (2026)**: [project](https://dream-rsi.com/) · [arXiv:2609.14858](https://arxiv.org/abs/2609.14858). Key ideas: programmable meta-exploration, discovery trees as realized replay worlds, online/offline loop. Exact only for recorded decisions, offline policy comparison cannot establish fresh-domain performance.
- **Existing RSI robotics**: [AnsonNing/RSI](https://github.com/AnsonNing/RSI), particularly docs/v2/PLAN.md and docs/v2/CAPABILITY_INTERNALIZATION.md (may be private). Concepts considered: separated Executor vs Research Agent, skill assets, new-session reuse, failure-point branching, independent formal evaluation and lightweight workflow.
- **Existing EEHEMT research agent**: [AnsonNing/EEHEMT](https://github.com/AnsonNing/EEHEMT), especially docs/dream_rsi_stage_b.md and IMPLEMENTATION_REPORT.md (may be private). Concepts considered: World identity, candidate frontier, sibling campaign, recorded exact replay, policy DSL, history-family separation, capability scope, clear failure taxonomy.

These are **inspirations**, not copied source code. The Jarvis design is a new specification; production-ready modules have not been copied or validated. Before reusing private modules in this public repository check ownership, licensing and whether they disclose confidential research.

## 2. Adopted architecture decisions (initial ADR index)

### ADR-001 — Research system, not execution system
**Decision:** design Jarvis core domain-agnostic; make trading the first adapter domain. **Why:** user will provide HFT backend later; no false model/backtest assumptions. **Revisit if:** entire project intentionally becomes one tightly coupled strategy implementation.

### ADR-002 — Agent proposals + trusted deterministic execution
**Decision:** LLM roles cannot run unadmitted tools or change fixed evaluators/locked tests. **Why:** scientific validity, budget control and reproducibility. **Revisit if:** new isolated tools provide equivalent guarantees.

### ADR-003 — Logical roles, minimal physical agents
**Decision:** keep Director/Scientist/Designer/Critic/Curator distinct conceptually; run them sequentially through one or more interchangeable providers. **Why:** avoid performance and token overhead of needless agent swarms. **Revisit if:** controlled multi-provider experiment shows improvement.

### ADR-004 — Single-host durable state first
**Decision:** SQLite/event journal + local sandboxed worker in MVP. **Why:** straightforward crash recovery and low infrastructure cost. **Revisit if:** real workloads need multiple hosts, stronger concurrency or geographically separated compute.

### ADR-005 — Discovery-tree-ready data from P0
**Decision:** capture available frontier, decision-visible evidence and actual costs from first campaign. **Why:** retrospectively reconstructing causally correct replay from text logs is unreliable. **Revisit if:** a better proven event protocol replaces it.

### ADR-006 — Dream-RSI acts on research policy Q
**Decision:** only Q's branching, allocation and stop logic self-improves at P4. **Why:** distinguish meta-process gain from domain artifact gain. **Revisit if:** independently tested broader self-modification protocol exists.

### ADR-007 — Memory = verified knowledge and ability
**Decision:** structured provenance/validation scope + callable capability registry; embeddings optional. **Why:** semantic similarity cannot establish experimental truth. **Revisit if:** retrieval benchmark proves alternative design.

### ADR-008 — Separate formal and meta-policy test boundaries
**Decision:** use nested evaluation: strategy candidate holdout distinct from research-policy worlds/heldouts. **Why:** prevent feedback leakage and falsely credited improvement. **Revisit if:** research questions change enough to require new, explicitly frozen comparator.

### ADR-009 — Conservative RSI promotion
**Decision:** incumbent preserved when evidence/coverage insufficient; single heldout finalist then fresh-campaign tests. **Why:** historical replay non-regression alone does not imply future non-regression. **Revisit if:** stronger evaluation design is empirically justified.

### ADR-010 — Public repo safe by default
**Decision:** no private market data, strategies, tokens, model files, personal compute paths. **Why:** Jarvis GitHub repo is public. **Revisit if:** owner deliberately creates isolated private subrepos with reviewed data governance.

## 3. What is fact vs specification?

Fact: Jarvis repository was essentially empty at planning time. The user intends to implement with local Codex and will provide actual backtest/data later. The cited RSI and EEHEMT projects contain relevant existing design patterns. Dream-RSI paper describes exact recorded-history replay and a meta-exploration loop.

Specification: directory layout, schema field names, exact release gates, budgets, proposed CLI, roles and policy DSL are **Jarvis design decisions** that require implementation and tests. They are not claims that the current Jarvis repo implements them.

## 4. Risks to explicitly track

- Sparse/favorably selected discovery histories → Dream-RSI replay coverage bias.
- Leaky research memory → false formal evaluation or policy-heldout contamination.
- Too many LLM agents → coordination overhead without scientific benefit.
- Too much governance → engineering effort eclipses research.
- Too little governance → unrepeatable outcomes / evaluator gaming.
- Proprietary domain integration → accidental public source/data disclosure.
- Repeated backtest search → multiple-comparison / selection overfit.
- Counterfactual execution outcomes unavailable → invented financial conclusions.
- Provider/LLM changes → research-agent behavior drift, Replay assumptions invalidated.
- Unreconciled remote job → duplicate expensive training/backtesting.

## 5. Revision rules

ADRs are versioned, authored and dated; amend or supersede rather than silently changing prior scientific commitments. A new benchmark/test objective or holdout decision must be declared **before** testing candidates, and scientific changes create a new Campaign identity.
