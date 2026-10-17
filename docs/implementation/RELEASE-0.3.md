# Jarvis 0.3 — Complete synthetic Dream-RSI acceptance

Date: 2026-10-18. Status: VERIFIED_WITH_LIMITATIONS.

Implementation: `142fa1a70b7c22c44385fe921576771a079ba61c` after core
`4f9ffb71dd8f71312d82b972efff29616a65b043`. Both complete studies ran with
that committed implementation, `dirty=false`, and source identity
`sha256:42bf82c036ef24d6212c36472666e077b1e85d2bfb547ef2f30681227cc2e6a1`.

## Executed workflow

```sh
jarvis --store .jarvis/dream-fixture-acceptance rsi study
jarvis --store .jarvis/dream-real-acceptance rsi study --developer codex
jarvis --store .jarvis/dream-real-acceptance rsi verify <actual-study-id>
jarvis --store .jarvis/dream-real-acceptance rsi report <actual-study-id>
jarvis --store .jarvis/dream-real-acceptance rsi resume <actual-study-id>
```

The fixture-developer control and real-model study each completed three rounds
and one additional previously unseen condition-shift canary. Both finished with
Q0 restored. The two studies share the declared fixture protocol; they are
engineering controls, not additional independent scientific replications.

| Real-model stage | Observed result | Decision |
| --- | --- | --- |
| 1: incomplete coverage | Proposed new orders; missing realized diagnostic edges prevent comparison | NO_PROMOTION; retain Q0 |
| 2: collect missing branches in new worlds | Frozen finalist: heldout target attainment 6/6 vs incumbent 2/6; fresh locked attainment 4/6 vs 0/6 | APPROVED_FOR_RESEARCH, synthetic scope only |
| 3: plateau check | Existing policy already reaches all development targets; no better covered candidate | NO_PROMOTION; retain current policy |
| Independent shifted-family canary | Active policy 0/2 vs fallback 2/2 | Deprecate active version and roll back to Q0 |

The real policy developer made exactly **three** completed requests. It authored
two proposals per request; the kernel included the incumbent unchanged. Actual
input-plus-output tokens were **16,937 + 20,066 + 20,280 = 57,283**. Cached input
is already part of input, not an additional charge. No monetary price or resolved
default model name is inferred. The provider was the installed authenticated
Codex CLI; its default model selection was recorded as unreported by exec JSON.

## What changed and what the negative result means

The initial policy spent its three-step budget on inspection, broad expansion
and diagnosis, leaving no step for targeted correction. After actual development
branches were collected, the real model proposed inspection, diagnosis and
correction. Independent families and newly executed paired tasks initially
supported this ordering under the unchanged budget and tools.

The plateau round proposed alternative orderings and stopping rules. Mandatory
initial inspection and the recorded admissible frontier prevent a policy from
claiming an unexecuted shortcut or fictitious lower cost. No additional gain
qualified for validation or promotion.

The declared adversarial canary exposes a spurious development proxy: correction
looks promising in development but fails the locked shifted condition, while
broad expansion succeeds. Its observations do not establish a reliable general
way to distinguish that shift beforehand. Rollback is the observed outcome;
historical replay improvement cannot guarantee future non-regression. This
receipt preserves the failed active policy and its earlier positive evidence.

## Corpus, isolation and accounting

- The full pool contains **12 trees across six distinct mathematical families**,
  with three development families and three disjoint policy-heldout families.
  Additional fresh/canary families were declared separately before their use.
- Real sibling experiments were executed from immutable parent states. Receipts
  contain actual observations, fixed evaluations and separately settled costs.
  Siblings/future/locked outcomes never enter an earlier decision state.
- Candidate generation received only development observations. Exactly one
  finalist was frozen before heldout reveal. A store-wide family ledger prevents
  recycling a validation family by renaming a pool or changing a variant.
- The real study recorded **149 jobs: 146 synthetic tool/evaluation invocations
  and three actual developer calls**. The fixture control recorded 146 tool jobs
  and zero LLM calls. Neither study had a pending reservation at completion.
- Original collection cost, historical replay path cost, actual proposal usage
  and fresh execution cost remain separate. Tool/compute counts are fixture
  invocations, not GPU hours or real strategy backtests.
- Both complete studies passed history/round/fresh/registry verification. Offline
  verification and completed-run resume were repeated while domain execution,
  numerical evaluation, provider completion and socket connection were patched
  to fail on invocation. They completed with **zero new jobs/calls**.

Raw prompts, answers, proposal rationales, receipts and full private operational
reports remain in ignored local storage. No authentication, proprietary inputs,
real trading rows or model weights were published.

## Automated and operational acceptance

Executed installation, schema export, Ruff lint/format, mypy, dependency check and
`pytest -q`: **102 passed in 41.51s**, 43 Python files formatted, 31 source modules
type checked, no broken requirements. Tests cover unknown branches, no-tool
replay, causal ancestry, immutable receipt adoption, unknown in-flight/usage stop,
family overlap/exposure, one-shot selection, fresh matched budgets, negative/no
promotion, missing inputs vs infrastructure failure, real subprocess CLI study,
policy version ancestry and measured rollback.

GitHub CI on the implementation runs the full tests plus the synthetic study and
settings negotiation without credentials. Subsequent release documentation does
not change the implementation source identity above.

## Domain settings and remaining scope

Finite single-axis/factorial declarations support take-profit/stop-loss basis
points, Trading cutoff deltas, cancellation mode and a closed cancellation-rule
DSL. The example produces 54 designs with explicit missing capabilities. Price
path, fill, exit-simulation, order-event and joint-comparison requirements are
documented in [15_EXPERIMENT_SPACE.md](../15_EXPERIMENT_SPACE.md); unavailable
execution is labeled REQUIRES_CAPABILITY / NOT_RUN.

Current real-domain acceptance covers the previously admitted frozen Hermes
artifact format. The reference repository now documents a new baseline format;
it needs separate adapter admission and fidelity verification. No new baseline,
stop/target simulator, custom cancellation executor or external backtester was
executed in this synthetic study. Existing entry PL must not be clipped to invent
exit outcomes.

Persistent L2 capability learning, generated-code sandbox execution, real-domain
policy transfer and general efficacy remain separate unverified packages. The
isolated L3 loop holds memory empty and base tools fixed. Further promotion needs
additional independent families after the declared cohorts have been consumed.
