# 07 — Scientific Evaluation, Promotion and Research Benchmarks

## 1. Separate evaluation layers

**Layer A / Tool correctness:** schema, determinism, algorithm fidelity, data sanity and no unauthorized access.

**Layer B / Domain candidate:** compare candidate vs fixed baseline under declared development/selection/locked-test protocol. For trading, this eventually includes execution realism, costs, risk and time dependence.

**Layer C / Capability transfer:** does a registered method improve fresh tasks relative to no capability under equal research budget?

**Layer D / Meta-research policy:** does Q1 improve adaptation cost / valid discovery quality on **previously unseen research campaigns** relative to Q0, with same researchers, tools and budgets?

Do not use strategy test data as if they were the separate heldout task families needed for Layer D.

## 2. Dataset/time/test isolation

At domain level reserve independent:
- development (training, hypothesis generation and diagnostics),
- candidate selection (model, feature and threshold choice),
- locked final test (one-time verdict for frozen candidate),
- optional forward/shadow period after finalization.

At **research-policy** level reserve separate development research worlds, policy-selection/heldout worlds, and fresh online campaigns. Correlated strategies, variants or time windows need family grouping and split together. The specific financial split semantics arrive with the actual backtester; no generic agent may silently choose a convenient August/test month.

After a test reveal, revisiting assumptions can be a *new research campaign with new untouched test*, not continuation of the previous formal test. Provenance of test access must be auditable.

## 3. Predeclared objectives and multiple comparisons

Freeze one primary outcome and direction, acceptable budget, validation cohorts, risk bounds, comparator and tie-breaks before searching. Secondary metrics can be diagnostic but cannot be substituted post hoc for primary metric. Record every evaluated variant, including early failed variants, so multiplicity and selection effects are visible.

For financial evaluations: time-ordered/cross-validated OOF based on real event timing, purge/embargo where labels overlap; compare net PnL after realistic fees/slippage, drawdown, tail losses, trade/fill/cancel rates, stability across days/products/regimes, sensitivity to delays and costs. Use block/day-level bootstrap or appropriate dependence-aware inference; thousands of correlated ticks/trades are not independent trials.

A backtester may not be capable of reconstructing unobserved counterfactual fills or cancel decisions. Mark such outcomes NON_IDENTIFIABLE/UNKNOWN, never observed. The domain adapter explicitly declares replay fidelity and causal assumptions.

## 4. Fixed evaluator and acceptance gate

The numeric evaluator computes metrics from sealed input/result artifacts under fixed code hash and metric configuration, not from an LLM summary. It verifies input hashes, sample/time coverage, candidate baseline comparability, completeness, leakage checks and units. It may return PASS, FAIL, INCONCLUSIVE, INVALID_EVALUATION or INSUFFICIENT_COVERAGE. These are independent of job SUCCESS state.

**Promotion requirement for domain candidates:**
1. Correctness and no data leakage flags.
2. Meaningful development hypothesis and candidate frozen before locked test.
3. All declared risk/validity constraints met.
4. Locked test PASS (or inconclusive with no promotion).
5. Final report identifies data/time/fidelity scope and unresolved risks.

No model automatically becomes a live trading strategy.

## 5. Research quality vs research cost

Report:
- Quality at fixed budget B: best **valid** candidate improvement over baseline, normalized within task.
- Adaptation cost: actual resource cost until predeclared goal first reached (or censored if not reached).
- Valid success rate across independent task families with uncertainty interval.
- Operational robustness: recovered interruptions, rejected unsafe decisions, duplicate work avoided.
- Diversity: tested hypothesis families and valid branch coverage; diversity is a secondary diagnostic, not an excuse to maximize token burn.

Cost vector: LLM calls/tokens/charges, GPU seconds, CPU time, tool/model evaluations, backtests, wall clock, human minutes/interventions, interrupted computation, failed and repeated calls. Monetization weights fixed before comparison. Distinguish validation collection cost vs replay path cost vs dreaming overhead.

## 6. Experimental comparisons (A/B/C)

| Arm | Research assets | Q policy | Purpose |
|---|---|---|---|
| A | Fresh/no accumulated skills | Q0 | unaided baseline |
| B | validated memory/capabilities | Q0 | isolate transfer effect |
| C | same as B | Q_candidate | isolate Dream-RSI effect |

Same initial task/baseline, agent provider/version, tool access, declared budget, evaluator and heldout families. Randomize paired run order where possible; report uncertainty over independent research-world families, not only seeds. Log total **policy development plus task execution** costs.

Include at least one intentionally negative-control task where memory should not be applicable, to detect overgeneralization.

## 7. Dream-RSI gates

- Incumbent policy participates in every development comparison.
- Development selection uses only development replay; incomplete replay paths are counted as lack of coverage, not genuine bad candidate reward.
- Choose/freeze **one** finalist prior to policy-heldout read; no retry on that revealed cohort.
- Require adequate coverage, valid behavior, and predeclared meaningful effectiveness or efficiency improvement; otherwise do not promote.
- Fresh-campaign validation is a separate step. Record both favorable and unfavorable runs; auto-rollback on statistically/operationally credible regression.
- Paper's historical replay non-regression holds only for selection criterion over that realized history; unseen domains can regress.

## 8. Mandatory reports

Domain Report: goal, baseline, data/split lineage, experiment tree, attempted methods, failure classes, final candidate, all important metrics/risk/cost, validity and human-readable limitations.

Capability Report: source/fidelity, implementation diff/hash, scope, benchmark against without-tool baseline, new-session availability and transfer test.

Dream Report: Q0 vs Q*, pool composition by family, total collection cost, replay coverage and unknown actions, budget/metric definition, finalist-selection receipt, heldout verdict, fresh online comparison, regression/rollback.

All claims include evidence IDs linking to immutable artifacts. An LLM explanation is advisory; report metrics come from evaluator output.

## 9. Minimum honest result vocabulary

- COMPLETED_WORKFLOW: agent work ran; may have found nothing.
- VALIDATED_IMPROVEMENT: specific measured criterion met under a declared scope.
- NO_DEMONSTRATED_IMPROVEMENT: comparison failed or showed no meaningful gain.
- INCONCLUSIVE: insufficient independent evidence/uncertainty too high.
- DATA_OR_TOOL_GAP: capability/data needed before testing.
- INVALID_EVALUATION: leakage, wrong World, broken metrics or unavailable test.
- INFRASTRUCTURE_INTERRUPTED: technical execution incomplete; no negative scientific inference.
- REPLAY_ONLY: exact historical result, no fresh experiment.
- POLICY_TRANSFER_UNVERIFIED: Dream-RSI policy did not yet pass fresh campaigns.

Do not collapse them into generic "success".
