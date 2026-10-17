# 05 — Dream-RSI / Meta-Research Policy Improvement

## 1. Objective, source and non-claims

Reference: [Dream-RSI: Recursive Self-Improvement through Evolving Worlds](https://arxiv.org/abs/2609.14858), [project](https://dream-rsi.com/). The method leaves the underlying coding/research agent unchanged and improves a higher-level **exploration policy** by replaying realized discovery trees. Historical results are exact **within their realized action paths**, not for unexplored counterfactuals, and the paper's historical replay non-regression guarantee does not imply future online non-regression.

Jarvis adapts that meta-policy loop to **research orchestration**, not to trade/exit/cancel decision policies. Define Q(a | research_state) = research action-selection policy. The underlying domain candidate (model, strategy or capability) is a separate versioned artifact.

~~~text
Online campaign with Q_t
         │
         ▼
actual evaluated branches + cost + information sets
         │
         ▼
append-only Discovery Tree T_t
         │
         ▼
verified History Pool H_t = (T_1, ... , T_t)
         │
         ▼
Dream: Q_t + proposals Q'_1 ... Q'_M
         │   replay without executing tools
         ▼
development score + exact coverage + budget + complexity
         │
         ▼
freeze one candidate → independent policy-heldout replay
         │
         ▼
if accepted, Q_(t+1) (for NEW online tasks only)
         │
         └───────────────→ expanded H_(t+1)
~~~

This is **L3 research process improvement**. Capability learning (L2) is another loop and must be experimentally separated to attribute gains.

## 2. What a Discovery Tree must capture

At node n, store full admissible decision-time observation O_n, available frontier F_n, selected action a_n, observable result r_n, actual costs c_n, baseline candidate state, branch parent and siblings actually visible before choice, associated evaluator and timestamps. An action "suggested" but not executed has no result. A branch may be considered eligible even if absent; replay choosing it then returns UNKNOWN_ACTION and stops the affected path.

One primary parent per branch and explicit branch-start workspace/version prevent the replay from combining incompatible mutated artifacts. Multiple parallel branches from same parent must have independent resource accounting. Store tool/job IDs and candidate commits for all siblings, including interrupted/failed ones.

### Decision-information consistency

When replaying a new Q, reveal nodes **only if** their ancestor state/action path exists and all causal prerequisites are satisfied. Reveal a node's result only after replay "pays" its stored actual cost. A new order of independent recorded sibling branches is allowed if the World and isolation make each result independent of completion order. If an agent in original run changed its experiment design after seeing a different sibling, alternate ordering may not have a valid corresponding branch; mark unsupported instead of assuming invariance.

Store visible evidence set and frontier separately for every choice. Future outcomes, final best candidate, hidden formal tests and post-hoc annotations must never enter Q's reconstructed state.

## 3. Three kinds of costs

1. **Original data collection:** actual online LLM/tool/training/backtest costs already spent to produce trees.
2. **Replay path cost:** sum of historically recorded action cost the candidate Q chooses to reveal. This is counterfactual accounting of historical *observed* calls, not new compute.
3. **Dreaming overhead:** tokens/time consumed by LLM policy development, replay CPU, validation and storage. Replay requires zero new domain evaluations but is not globally free.

Preserve dimensions: LLM tokens and money, GPU time, model evaluations, backtests, actual tool calls, wall clock, human interventions, queue time. Parallel replay cost accounting: sum actual compute is additive, wall clock comparison requires explicit scheduling model; if unverifiable, report sequential-equivalent or UNKNOWN, not fictitious speedup.

## 4. Q0 baseline policy

Implement a deterministic, versioned Q0 before any dreaming. Minimal policy:

- First diagnose available baseline; if baseline already meets declared sufficient criterion, prefer terminal verification to expensive search.
- Prioritize admitted cheap informative diagnostics to reduce ambiguity; then choose experiment with highest predeclared heuristic score using only visible development evidence.
- Retain a small exploratory branch share; do not assume a predicted gain is a measured gain.
- If recent attempts failed, explore a distinct hypothesis instead of performing unbounded parameter refinements.
- Prefer validation and stop when a predeclared criterion or budget boundary is met.
- Stable tie-break by ActionID; all heuristic features logged.

Q0 is a reference, not an assumed optimal policy. Test that recorded Q0 action choices can be reconstructed on a fixture tree; record known off-policy state mismatches rather than rewriting history.

## 5. Restricted Research Policy DSL v1

Example to be compiled by a trusted whitelist parser (not eval/exec):

~~~yaml
schema_version: 1
kind: ResearchExplorationPolicy
policy_id: q-candidate-001
scope: domain_agnostic_research
rules:
  - when: baseline_sufficient
    prefer: [VERIFY, STOP]
  - when: prior_attempt_invalid
    prefer: [DIAGNOSE, REQUEST_CAPABILITY]
  - when: stagnation_and_budget_high
    prefer: [EXPLORE_NEW_HYPOTHESIS, CHEAP_DIAGNOSTIC]
  - when: uncertain_evidence
    prefer: [CHEAP_DIAGNOSTIC, RUN_COMPARISON]
  - when: always
    prefer: [REFINE, EXPLORE_NEW_HYPOTHESIS, STOP]
allocation:
  min_branch_budget_fraction: 0.05
  max_single_branch_fraction: 0.45
  max_parallel: 2
selection_order: declared_preference_then_id
stopping:
  - budget_exhausted
  - valid_terminal_evaluation
  - no_admitted_actions
~~~

Predicates are generated by trusted ResearchState features; Q cannot introduce arbitrary Python expressions, modify the evaluator, request live trading or see heldout results. Tunable numeric DSL domains and rule counts are bounded/fixed before evaluation. Changing DSL compiler requires new identity and revalidation.

## 6. Replay algorithm (normative)

~~~text
replay(world, Q, budget):
  assert World+Tree+Policy hashes verify
  state := frozen root decision snapshot with correct evidence visibility
  spent := zero cost vector
  trace := []
  while Q can choose admitted action and budget remains:
      frontier := actions recorded as available at THIS reproducible state
      chosen := compile(Q).choose(state, frontier)
      if STOP: return COMPLETE(trace, score, spent, coverage)
      if chosen not realized in history at this state:
          return INSUFFICIENT_COVERAGE(trace, UNKNOWN_ACTION, spent)
      recorded := exact child action result with valid ancestry
      if recorded is corrupt, missing or outside available information:
          return INVALID_REPLAY
      charge recorded.actual_cost; enforce budget
      reveal result + next admissible state WITHOUT tool/API/LLM call
      append reveal + evidence to trace
  return COMPLETE or BUDGET_EXHAUSTED, never guessed score
~~~

A valid replay path follows the *recorded realized transitions*; any new agent-generated experiment or new model parameterization without exact matching history requires real online execution. An internal model that predicts unknown outcomes would be a separate, explicitly approximate approach, and must never be passed off as Dream-RSI exact replay.

## 7. Offline objective and selection

Predeclare task-aware score with **real evaluation outcome + actual replay path cost + coverage**. For example, for each compatible world w:

~~~text
quality_w(Q) = best valid DEVELOPMENT candidate quality revealed by Q
cost_w(Q)    = cumulative recorded tool+LLM budget units revealed by Q
success_w(Q) = reached fixed target quality by budget B
~~~

The recommended primary metric is fraction of held-compatible worlds reaching target quality under matched resource cap B. Secondary: cost-to-target, best accepted score at B, false-positive/invalid verdicts, repeated dead-end rate, and exploratory diversity. For different objective types, use per-World target attainment rather than averaging raw incomparable values (PnL vs optimizer loss, etc.). Missing values due to incomplete paths are **not** assumed failure nor success; report coverage and, where possible, conservative upper/lower bounds separately.

Policy candidate promotion must not optimize a metric jointly defined or modified by that candidate. Complexity and diversity constraints prevent trivial overfitting to a small history pool.

## 8. Offline Dreaming procedure

1. Verify and freeze history pool, compatibility and group-level development / policy-heldout splits.
2. Prepare a **development-only** request: state/action histograms, incurred costs, failure modes, Q incumbent and replay gaps. Do not include policy-heldout raw outcomes.
3. Policy Developer proposes 2–5 restricted DSL variants. Include the incumbent unchanged.
4. Compile all candidates; reject invalid or unsafe policies before replay.
5. Replay all candidates on all **compatible** development worlds at matched budgets; report per-world status, score, coverage, resource-vector and complexity.
6. Freeze one development finalist using prespecified criteria. If no candidate beats incumbent with adequate coverage, keep incumbent.
7. Evaluate finalist and incumbent on previously unopened policy-heldout worlds. No policy rewriting after reveal. If insufficient coverage or weak evidence, mark INCONCLUSIVE and leave Q unchanged.
8. Record experiment-wide original search collection cost and dreaming cost separately.
9. Independently validate by starting **fresh** research campaigns never used in policy development or selection. Same base agent/assets/adapters/budget, candidate Q vs Q0/Q incumbent.
10. Promote only to APPROVED_FOR_RESEARCH if reliable benefit; deployment for real research remains bounded with rollback/canary. No live-trading permission.

## 9. Pool compatibility and leakage

Worlds can be mixed for meta-learning only if action semantics, replay-recording semantics, evaluation budget and success normalization are compatible. Explicitly stratify by domain, task family, strategy lineage, time period, data source, tool/evaluator major version and researcher-agent version. A single family repeated across many seeds or months is not independent evidence of transfer.

No world or correlated family may appear across both policy-development and policy-heldout split. Historical locked strategy-test results may be used as prior knowledge only under declared permissions; they are not automatically reusable for tuning that same strategy's future formal comparisons. Keep **strategy candidate holdout** and **research-policy holdout** separate.

## 10. Coverage-aware history collection

When replay reports UNKNOWN_ACTION:
- Record gap: (state signature, admissible action, missing outcome type, potential cost).
- Schedule a **new online world/campaign**, if research budget and permissions allow, to collect evidence for high-value gaps. Do not mutate an old tree to conjure a sibling.
- Branch multiple real experiments from an immutable parent when tool semantics allow. For stateful training/backtesting, clone parent workspaces and use independent seeds/state.
- Preserve even dead ends and negative studies; otherwise replay favors selectively retained successes.

Do not maximize coverage at any cost. A coverage collection policy balances information benefit against actual tool expense, and remains subordinate to user research goals.

## 11. Policy and capability versioning

PolicyID binds DSL source, compiler hash, state feature extractor version, frontier catalog version, budget semantics, source prompt/model version and scope. A previously validated Q must be re-evaluated after any of these change materially. Q can be EXPERIMENTAL, REPLAY_VALIDATED, POLICY_HELDOUT_VALIDATED, FRESH_CAMPAIGN_VALIDATED, APPROVED_FOR_RESEARCH or DEPRECATED. Never use the term production trading approval here.

## 12. Mandatory Dream-RSI tests

- Deterministic replay parity of Q0 with one recorded fixture.
- Zero executor, training, evaluator, external-network and LLM calls during replay.
- Hidden future result not visible at choice time; violating policy rejected.
- Unexplored branch is UNKNOWN_ACTION; never success=0 or invented PnL.
- Corrupt artifact hash or node parent invalidates world.
- Budget cutoff produces identical replay path across runs.
- Late-result sibling incorrectly exposed triggers INVALID_REPLAY.
- Parallel sum cost vs elapsed distinction preserved.
- Incumbent always included; no promotion from development overfit.
- Shared family across meta-policy splits rejected.
- Policy-heldout outcomes unavailable to candidate-generating agent.
- If all candidate replays incomplete, incumbent unchanged.
- Fresh-campaign comparison can regress even with historical gain; rollback works.

## 13. Interpreting the paper's historical non-regression statement

Because the incumbent is among policy candidates, one can select a new policy with no worse **recorded-replay score** on the evaluated historical pool by construction. That does NOT guarantee:
- Better performance on an unseen research task.
- Coverage for unseen action choices.
- No degradation when the LLM/provider, tool version, market data or environment changes.
- Lower end-to-end engineering expense once policy-development tokens are counted.

Jarvis explicitly separates these claims. Its official RSI result is **fresh-campaign research quality and adaptation cost under a matched budget**, not replay score alone.
