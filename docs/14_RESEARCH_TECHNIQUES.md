# 14 — Research Intelligence Techniques and Decision Algorithms

This document maps promising *research-process* techniques into Jarvis. They are **options**, not a mandate to implement all algorithms. The goal is a dependable meta-scientist, not maximal technique count.

## 1. Hierarchical planning and re-planning

Represent a campaign as Goal → Research Questions → Falsifiable Hypotheses → Experiments → Tool Jobs → Evaluation. High-level Director selects questions; Scientist forms hypotheses; Designer creates experiments; Q chooses where to allocate real work. Replan after meaningful evidence, not every token. Maintain a fixed objective and runtime permission boundary.

Recommended MVP: simple rules and versioned explicit plan records; optional tree search later. Measure whether the planning agent changes direction when evidence disproves a hypothesis rather than repeatedly tuning the same model.

## 2. Research Tree Search

Tree nodes are versioned experimental candidates. Children represent alternative research actions. Support:
- breadth: multiple distinct hypotheses or analysis tools;
- depth: refining promising candidate;
- branch provenance: all children start from declared parent workspace;
- pruning: stop weak branches with evidence, do not erase attempts;
- selective retention: keep top candidates plus diverse alternatives and negative history.

Do NOT use classic MCTS/UCB as if financial reward samples are independent, stationary, or immediately available. If UCB/Thompson ideas are used, state exactly what random variable they model (e.g. research improvement probability), how uncertainty is estimated, and what evidence justifies it.

## 3. Value of Information (VOI) and Active Experiment Design

A cheap diagnostic may have greater value than training another large model. Each hypothesis suggests an experiment that should discriminate among explanations.

For potential experiment e with observation Y, an idealized information utility:
I(H;Y | E,e) = entropy(H|E) - expected_Y[entropy(H|E,Y,e)].
A practical scheduler can rank e by expected information/compute:
priority(e) = [estimated decision benefit + exploration bonus] / estimated resource cost.

Such values are *estimates*. If the system lacks calibrated probabilities, use transparent ordinal scores rather than fake precise expected values. Final results come from actual observed experiments. Save pre-experiment predictions and compare them to reality to calibrate the heuristic over time.

This is distinct from Dream-RSI: VOI guides an **online local** choice; Dream-RSI learns the overall Q mapping from historical realized research trees.

## 4. Cost-aware adaptive budgeting

Use staged budgets: cheap lint/data diagnostics → small pilot → larger training/backtest → locked evaluation. Allow early termination of poor branches but preserve partial/interrupted costs. Multi-fidelity tests must be labeled as approximate; low-cost proxy performance never automatically equals valid full backtest.

Hypothesis diversity minimum may prevent premature collapse to one favored model. Predeclare budget caps and stop logic so Q cannot redefine itself as "successful" by using different cost accounting.

## 5. Multi-objective research

Strategy and research criteria may conflict: profitability, drawdown, stability, runtime, fill realism and number of experiments. Keep **one fixed primary scientific objective** per campaign plus hard risk/validity constraints; report Pareto front and tradeoffs as secondary analyses. No changing metric weights after observing which candidate wins.

Across different task families Dream-RSI must compare normalized within-world success or cost-to-target, not average incomparable raw domain scores.

## 6. Hypothesis falsification and causal failure diagnosis

Ask for alternative explanations and evidence that would disprove each. Critic separates:
- software failure vs scientific negative;
- sample selection bias vs modeling limitation;
- target mismatch vs weak representation;
- insufficient data vs missing capability;
- correlation vs causation and unobserved counterfactual.

For HFT, cancellation/entry/exit form sequential decisions; later realized outcomes may be policy-dependent. If historical replay cannot identify unexecuted counterfactual fills, record uncertainty and limitations. Do not make LLM-generated "what if" PnL a measured result.

## 7. Tool and skill internalization

Treat an external method as a candidate capability with source provenance, known-case fidelity tests, scoped task effectiveness and routing predicates. Retrieval index points to versioned skill card, callable entry point, tests and evidence. Fresh-session use is required to demonstrate persistent competence.

Capability RSI can create a **research action class** (e.g. a new diagnostic), thereby extending the frontier. Q must know the action catalog version; historical replay cannot apply unknown future actions to old worlds without coverage.

## 8. Meta-optimization: Dream-RSI vs naive prompt reflection

A free-form self-reflection summary influences language generation but often has unclear credit assignment and can suppress exploration diversity. Dream-RSI instead records concrete action/outcome/cost branches; replays alternative Q policies on the **realized** search space, then validates in new runs. Use summaries as scoped knowledge, never as substitutes for policy evaluation.

Policy iteration can target:
- node selection, depth/breadth;
- parallel batches;
- stop and restart;
- cheap-first ordering;
- discovery of missing evidence;
- reusing validated research skills;
- budget allocation conditioned on stagnation and uncertainty.

Initially use a safe DSL, not unrestricted policy-code self-modification.

## 9. Research-policy learning stages

**Q0:** fixed deterministic policy with clear heuristics.
**Q1:** context-conditioned hand-written DSL proposal, replay tested.
**Q2:** LLM-proposed DSL candidates, development and heldout replay.
**Q3 (optional):** fitted contextual bandit or offline RL from sufficient logged transitions with support diagnostics.
**Q4 (future):** predictive research outcome model if calibrated in independent worlds; must explicitly distinguish predicted vs observed rewards.

Bandit/offline RL require action propensities/coverage and reasonable identifiability. Historical agent action logs are selectively observed, not a random exploration dataset. An algorithm that scores unchosen experiments by invented outcomes is not admissible exact replay.

## 10. Continual learning, drift and regression

Memory and Q may become stale when tools, data schemas, markets or research tasks change. Keep domain/task capability compatibility and validity dates. When new tests contradict old skill utility, register an exception or deprecate that scope. Re-test a sampled subset of historical tasks after major agent/prompt/policy/compiler changes to measure forgetting and negative transfer.

Avoid updating research-LLM weights as a prerequisite. First demonstrate measurable gains from external skills/policies with fixed base models; then weigh the cost and evidence for actual model fine-tuning.

## 11. Experiment scheduling choices

Local runner executes deterministic single jobs in P0. Later use prioritized queue with resource tokens for GPU/CPU/external calls, fair sharing among campaigns, branch isolation, heartbeat/lease and interruption reconciliation. Candidate selection may run concurrently only when sibling outcomes are independent from a frozen common parent; otherwise sequential execution preserves information causality.

Parallelism affects elapsed cost but not the factual outcome of a stateful experiment unless explicitly modeled. Replay may reorder independent recorded nodes but cannot invent results for a different data/tool state.

## 12. Technique adoption rubric

Before adopting a new technique ask:
1. Does it address an observed bottleneck or required capability?
2. Can it be tested on the synthetic suite with a meaningful comparator?
3. What new sources of bias, leakage or runtime burden does it create?
4. Will the improvement persist in a new agent session?
5. Can effects be measured at equal task/compute budgets?
6. Does it preserve exact replay and evaluative separation?
7. Is it domain-agnostic, or a scoped adapter capability?

If no, keep it as an optional research note rather than permanent architecture.
