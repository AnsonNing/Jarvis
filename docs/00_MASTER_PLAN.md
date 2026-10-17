# 00 — Jarvis Master Plan / 總體規劃

**Status:** design specification, v0.1, 2026-10-18. **Audience:** project owner and local Codex. **Scope:** research-agent system; financial strategy/backend details intentionally TBD.

## 1. North Star

Jarvis = **Autonomous, Evidence-Driven, Continually Self-Improving Research Agent System**. The first application is quantitative/HFT strategy research, but experiment execution is a domain adapter; core orchestration and self-improvement are domain-agnostic.

Given goal G and bounded budget B, Jarvis should autonomously:
understand G → inspect available evidence → diagnose → propose competing hypotheses → choose action → execute admitted experiment(s) → evaluate deterministically → critique → iterate or stop → persist lineage/evidence → produce reusable capability/knowledge candidates → optionally compare improved research policies using Dream-RSI.

The system must **not** equate a successful run with strategy profitability. It must support conclusive negative findings, insufficient data, capability gaps and formal non-promotion.

## 2. Three improvement tracks (measured separately)

| Track | Object changed | Example | Test |
|---|---|---|---|
| L1 Domain artifact | strategy/training/configuration | better cancellation threshold | independent domain holdout |
| L2 Capability | code/skill/analysis method/knowledge | reusable leakage audit | independent tool fidelity + new campaign reuse |
| L3 Meta-policy | policy for choosing research actions | different branch/budget/stopping policy | held-out discovery worlds + fresh research campaigns |

L3 does not require modifying foundation-model weights; it improves an external exploration controller. L2 research methods may eventually improve how L1 research works, but a method's existence does not prove its impact.

## 3. Explicit non-goals

No proprietary data collection, backtester replacement, fixed strategy design, execution venue interface, financial return promises, automatic live deployment, generalized world-model prediction, fine-tuning of researcher weights, arbitrary privilege/self-permission changes. The architecture may eventually support additional domains, but only one synthetic domain fixture is needed before actual backtest adapters are supplied.

## 4. Two nested loops + capability loop

**Inner research loop:** Task → Baseline → Hypotheses → Experiments → Evaluation → Diagnose → (branch/refine/stop) → Report.

**Capability loop:** Detect a missing capability or reusable technique → study source → fidelity test → scoped experiment → register → fresh-session and cross-task reuse check.

**Outer Dream-RSI loop:** Collect actual Discovery Trees from online research → pin immutable History Pool → propose bounded new exploration policies → replay historical realized decisions → validate policy across held-out research worlds → deploy approved policy in fresh campaigns → collect new trees. Replay does not replace fresh evaluation.

### Architectural distinction

- **Domain policy:** the thing being researched (trade, cancel, exit); Jarvis treats it as an artifact.
- **Research policy Q:** how the researcher chooses experiments, branches, batches and stop criteria; Dream-RSI changes Q.
- **Authority:** fixed evaluation/safety layer independent of either policy.
- **Executor:** schedules real work, and alone can confirm numerical outcomes.

## 5. Success criteria

**MVP:** a completely unattended, bounded synthetic research campaign with >=2 adaptive decisions (second action depends on observed first result); all decisions/events persisted; stop/resume works; a credible report with evidence refs; no look-ahead or external private components required.

**Transfer:** on fresh tasks, an experienced agent retrieves only applicable evidence, uses validated tools in a new session, and demonstrates reduced adaptation cost or improved final accepted quality under matched budgets compared with fresh-agent baseline; regressions logged.

**Dream-RSI:** frozen Q0 vs candidate Q1 compared at equal budgets on held-out history; incomplete replay coverage honestly signaled; one finalist chosen before locked evaluation; fresh research tasks then used to measure real benefit. If no statistically credible benefit, retain incumbent.

Primary project metric: **research adaptation efficiency** — amount of actual compute, LLM tokens, wall clock and human interventions needed to reach a predeclared level of validated research quality. Report a multidimensional cost vector before choosing monetary conversion factors; do not tune factors post-hoc.

## 6. Project phases

P0 trusted kernel and synthetic research adapter; P1 autonomous campaign; P2 memory and capability internalization; P3 multi-campaign transfer + rich discovery corpus; P4 Dream-RSI replay/policy improvement; P5 broader autonomous and domain adapters. Detailed work packages and testable exit gates: docs/09_ROADMAP.md.

## 7. Invariants stronger than agent preference

- Domain semantics live in adapter, not generic controller.
- Inputs/experiment configuration and evaluator identity pinned before formal comparison.
- Past evidence cannot be edited to support a revised story; append corrections with lineage.
- Exact observed vs inferred/counterfactual values are explicitly distinguished.
- Every action has a validated schema, explicit preconditions, resource reservation and job ID.
- Agent reasoning is advisory; numeric scores/verification come from deterministic code.
- Research memory is evidence-scoped and isolated across confidential tenants/projects.
- Source material is untrusted: never execute a paper's or web page's instructions as agent authority.
- Design for private local execution; public repository stores only generic spec, synthetic fixtures and safe code.

## 8. Deferred decisions

Real trading dataset layout, available snapshots, execution-event granularity, target definitions, strategy horizon, train/calibration/test windows, custom model entry points, backtest fidelity, objective metrics and compute infrastructure must be specified when the user supplies the backtest system. Design contracts so these can change without rewriting research/memory/Dream-RSI layers.

## 9. Terminology

**Campaign:** user-facing research objective plus fixed contracts and budgets.
**World:** immutable research environment/capabilities, input/evaluator identity and initial state; a new material change creates a new World.
**Experiment:** one admitted change under a declared protocol, possibly requiring multiple tool jobs.
**Node:** branch attempt in the Discovery Tree; has parent, observation-at-choice, action, observed outcome, evidence and cost.
**Frontier:** available *candidate* actions with admissibility, not assumed results.
**AgentVersion:** prompt/tools/policy/memory snapshot/model-provider identity applied to a campaign.
**Capability:** validated, versioned and scoped reusable research operation.
**Incumbent:** currently accepted exploration policy.
**Replay world:** frozen recorded discovery tree in which policies may reveal previously observed actions/results.
