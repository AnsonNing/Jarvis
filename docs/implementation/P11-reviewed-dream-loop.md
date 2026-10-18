# Policy feedback into reviewed continuous research

This release connects a Dream-selected research policy to the real
ResearchProgram + independent Critic path. A program pins its policy at creation.
The policy selects among admitted experiments, determines an epoch's execution
budget, and applies its no-gain stopping rule across epochs. The Director and
Critic prompts, providers, evaluator and permission boundaries stay fixed.

## Two separate evidence stages

Recorded catalog replay remains a zero-call screen for family ordering and stop
policies. Its reported tool-path cost is not presented as a prediction of the
Director/Critic cost for an unrecorded ordering. Adaptive designs enter a new
epoch; unseen outcomes remain INSUFFICIENT_COVERAGE. A new Director or Critic
context must actually execute to establish its result and usage.

Screening freezes one finalist before policy-heldout disclosure. Independent
fresh policy arms then run the complete reviewed program, including baseline,
Director calls, proposal critique, candidate admission/compilation, selected
backend execution, result critique and further adaptive epochs. Both arms have
the same environment, directions, providers/configurations, round/catalog caps
and complete resource envelope. Entry-only PL remains primary; a cost-only gain
requires dominance across measured cost dimensions rather than adding seconds,
tokens and tool counts into an invented scalar. Risk remains in result evidence.

## Complete accounting and enforcement

An immutable budget-owner relation links queue jobs to programs and programs to
their study. Atomic reservations check the submitting owner and every ancestor
within the same database transaction. Sibling queues therefore share one cap.
Actual cost replaces reservation only with a durable receipt; unknown work keeps
its reservation. Role calls, compile admission, baseline imports, backend submit
and collection work, quota reads and policy-development calls remain accounted.

Reports distinguish summed local work seconds from local elapsed time. The
program cap also checks elapsed time before new descendant work. Remote compute,
server queue time, prices and money are UNAVAILABLE unless supplied; no token
count or local timer is converted into a monetary or remote-resource claim.
Collection, Dream overhead and prospective comparison costs remain separate in
the event lineage, and the complete study total counts each job only once.

## Adoption, continuation and rollback

A scope/protocol-bound registry admits a policy only with the frozen finalist,
independent heldout result and complete fresh-arm receipts. Synthetic evidence
cannot authorize an observed backend. The continuous coordinator reads that
registry under a lease only when creating a NEW program and pins the registry
revision in its declaration. Active and historical programs retain their policy.

Following a measured canary regression, the registry returns to the incumbent.
The next continuous program must actually execute with that policy. New histories
from continuation are appended to the next Dream input. A further candidate may
be screened, but exposed validation cohorts cannot be reused for another
promotion; missing coverage or new independent tasks stops that cycle honestly.

Legacy declarations omitting policy, registry and shared-budget fields retain
their original execution path and raw manifest hashes. The old Hermes catalog
Dream commands remain available. Cancelled watchers are not restarted or migrated.

## Commands and artificial acceptance

```sh
jarvis --store .jarvis/reviewed-dream-fixture research dream-loop-fixture
# New explicit declarations remain inert until advanced:
jarvis --store .jarvis/private research dream-loop-start local/reviewed-dream.json
jarvis --store .jarvis/private research dream-loop-advance STUDY_ID
# Bounded foreground runner; resume the SAME study after RUNNER_LIMIT:
jarvis --store .jarvis/private research dream-loop-run STUDY_ID \
  --max-steps 2000 --wall-seconds 14400 --poll-seconds 45
jarvis --store .jarvis/private research dream-loop-report STUDY_ID
```

The artificial control collects twelve reviewed histories from six mathematical
families, develops a policy, opens the isolated heldout catalog, compares full
fresh programs, adopts the winner in continuous research, compares full canary
programs, measures regression, rolls back, continues with Q0 and feeds three new
epoch histories into a second Dream cycle. That cycle stops on inadequate
coverage rather than inventing results. A separate non-regressing canary control
retains the policy. Original/failed local development attempts remain recorded.

The declared providers use simulated token/time units to test accounting and
causal control. They are explicitly engineering fixtures, not observed LLM
usage or autonomous market discovery. Actual backend invocations are artificial
evaluations; no remote Iris jobs, real Hermes studies, model training or live
trading are part of acceptance. Real-domain efficacy and L2 capability learning
require their own data, authority and validation.

`dream-loop-start` only seals the declaration. The runner is finite, leased and
resumable, honors PAUSED/CANCELLED, and stops for reconciliation instead of
resubmitting unknown work. It does not create a background watcher or recurring
automation. No cancelled real workflow is resumed by a fixture command.
