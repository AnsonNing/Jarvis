# Research preparation without starting execution

The owner can cancel monitoring and prepare a subsequent research declaration
without creating a study, consuming provider calls or uploading candidates.
Preparation does not establish a strategy gain or research-policy improvement.

## Logical cases and explicit replacements

Model admission distinguishes physical requests from effective comparison cases.
The v1 replacement proof supports one explicitly owner-authorized, one-hop resend.
It requires matching manifest and separate authorization, time-ordered submission
records, identical source bytes, side/mode/phase/period, matching completed job,
and a PASS scope proof against the saved preceding manifest. Previously completed
case sources, jobs and result directories must remain unchanged. Duplicate IDs,
orphan links, chains, missing proof and extra submissions are rejected.

Only the replacement enters comparison selection. The original unknown request
remains listed as unresolved. Selection neither cancels a remote job nor settles
its reservations. Replacement proof files and sources are pinned in prepared
environments; a baseline proof uses the selected result's actual label.

Completed packages with non-PASS findings return
`MODEL_ACCEPTANCE_NEEDS_ATTENTION`, rather than waiting as though remote work is
still queued. Failure remains failure. Invalid or missing engineering/lineage
evidence cannot be admitted through a development-use decision.

## Owner-selected development baseline

`IrisDevelopmentUse` records a trusted owner's choice of a supplied model for
already-exposed development work. Only PASS or `COMPLETED_WITH_FINDINGS` final
analysis is supported; all other engineering and logical-case gates must pass.
The record pins workflow, manifests, analysis, final findings, training/export
proofs, model metadata, HPP bytes, calendars, candidate sources and evaluator/API
source checksums. Changed evidence invalidates the decision.

This record preserves known limits and explicitly declares
`execution_authorized=false` and `independent_validation=false`. It is not an Iris
submission grant, scientific PASS, model update or independent-test admission.
The trusted caller supplies human provenance; the JSON file does not authenticate
a human or allow an LLM to approve itself. The ordinary strict gate is unchanged
when no owner-bound development-use record is supplied.

```sh
jarvis iris model-status --upstream "$PRIVATE_PACKAGE"
jarvis iris development-use --upstream "$PRIVATE_PACKAGE" \
  --owner-instruction "$OWNER_PREPARATION_INSTRUCTION" \
  --output local/preparation/development-use.json
jarvis iris readiness --upstream "$PRIVATE_PACKAGE" --python "$OWNER_PYTHON" \
  --development-use local/preparation/development-use.json \
  --design-root "$PRIVATE_PROFILES" --output local/preparation/readiness.json
```

These commands run before evidence-store, queue or provider construction. Readiness
checks both directions and their integration/full-period environments, profile
bindings, source/model hashes, baseline raw-ledger proofs and explicit calendars.
It returns `PREPARED_NOT_STARTED` or concrete blockers. It does not execute a
compiler/evaluator, read credentials, query quota, create studies or submit work.
Its baseline accounting field explicitly says it has not recomputed performance.
A separate trusted local baseline-import audit can recompute existing ledgers
without importing the private credential API or requesting a new backtest.

`iris prepare` and `iris continuous-prepare` accept the same explicit
`--development-use` record. The resulting declarations are inert until a separate
authorized start; prepared queues have no submission grant. Ordinary watchers
continue using strict admission. No old active or cancelled watcher is silently
converted to this scope.

## Cancellation and scalar fidelity

A stopped watch state is durable even when no study ID exists. CANCELLED, PAUSED,
closed, failed and unresolved/attention states return before model checks,
predecessor acquisition, quota reads, provider calls or study creation. Resuming a
different scope requires a new explicit owner decision; cancellation is not an
automatic restart condition.

Scalar bindings accept a numeric literal or a product of two numeric literals,
including digit separators. This handles source constants expressed as basis
points times a scale. Rendering and capability inspection use the same finite
numeric decoder. Symbols, function calls, sums and longer products are not
interpreted. Unchanged baseline source remains byte-identical.

## Baseline first, Dream policy comparison second

The first research phase freezes the supplied strategy baseline and initial
research protocol: provider/model identity, agent prompts, tools, memory access,
dataset exposure, evaluator and finite per-program resources. Strategy PL gains
are measured against the frozen strategy baseline, with risk recorded separately.
The existing `Hermes-Q0` is the fixed comparator for sealed candidate catalogs;
an adaptive director's later designs form separate causal epochs. They must not
be mislabeled as a predeclared catalog or a learned Dream policy.

After explicit launch authorization and quota evidence, verify one integration
candidate per direction, then conduct adaptive development research. Preserve
negative results, unavailable directions, costs, uncertain operations and stops.
There is no fixed lifetime experiment cap; each program remains bounded.

Dream policy development follows only after replay-valid history coverage:
at least ten distinct histories across three genuinely independent task families.
Different knobs on the same exposed dataset are research directions, not proof
of independent families. Backend jobs alone are not research histories. Policy
development/heldout families must be isolated; choose one finalist before heldout
reveal, then compare fresh campaigns under matched budgets and run a subsequent
canary with rollback. Missing unseen tasks remain NEEDS_INPUT. No new research,
heldout result or reusable capability may be invented to complete preparation.

The runtime-input precision gap, complete order/cancel acknowledgements, postfill
quote paths, explicit fee/fill semantics and independent new task data remain
separate evidence requirements. Owner-selected development use retains these
limitations and does not close them.
