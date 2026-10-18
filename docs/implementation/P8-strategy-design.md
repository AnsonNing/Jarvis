# Closed strategy-design research

This extends Iris research from scalar calibration to owner-bound strategy rules,
session schedules and position limits. The integration remains private and
dependency-injected. No proprietary function, model, feature formula, market
record or internal path belongs in this public repository.

## Agent intervention contract

`IrisSettings` adds `entry_rule`, `cancel_rule`, `session_start_ms`,
`session_end_ms`, `flat_time_ms`, `hang_volume_ratio`, `max_pending_notional`
and `max_position_notional`. Existing settings and six exploration families remain
compatible. Position/quantity interventions belong to the entry family; schedules
belong to timing. A single-factor trial changes one setting; explicitly joint
trials change two or three. The fixed primary metric is entry-only PL, with every
risk vector and negative result retained separately.

A rule contains `all` or `any` and one to four predicates. Each predicate uses an
owner-declared signal ID, `gt/ge/lt/le`, and a finite numeric threshold. There is
no code, import, function call, arbitrary expression or evaluator instruction in
an agent proposal. Unknown signals, wrong-stage signals, duplicated predicates,
unbound settings and thresholds outside declared units/bounds are rejected.
Predicate order is canonicalized before rendering, so reordering an equivalent
conjunction/disjunction does not bypass candidate deduplication. Missing/nonfinite
signals do not satisfy a predicate. Boundary comparisons retain their stated
strictness.

## Private owner integration

`IrisDesignProfile` supplies immutable scalar bindings, rule source anchors,
replacement templates, signal expressions, causal stage declarations, preserved
invariants and a compiler profile. Templates contain exactly one `{{rule}}`
placeholder and cannot alter region markers. All replacements must match exactly
one anchor inside its declared existing writable region. The final protected
lines/markers must remain byte-identical. No agent receives the private C++
expressions, model source, compilation scaffolding or executable paths. Its
context contains semantic descriptions, units, bounds and invariant descriptions.

`attach_design` pins the profile, compiler binary and owner scaffolding into the
environment. Capability expansion creates a new environment; it does not mutate
an active study. Existing model lineage, baseline source and evaluator remain
fixed. An owner must bind only signals actually available at the declared entry
or cancellation decision. Order-age, outcome, future-return and unknown feature
meanings are not silently synthesized. The trusted owner profile determines which
baseline entry, expiry, null, sampling and bypass guards remain around a rule.
The closed DSL alone cannot prove arbitrary owner source semantics.

Session settings use milliseconds since midnight. The adapter translates the
source's declared HHMMSSmmm constants, including digit separators, without doing
arithmetic on the packed clock. Start must precede end, and end must not exceed
flat time. Quantity ratio remains in ratio units. Notional limits retain their
owner-declared strategy units; they do not establish currency, capacity, queue
position or live exposure fidelity.

## Compile admission and evidence

Every candidate in a design-enabled queue, including its baseline, passes a
local C++17 compile-only check before queue admission. Compilation uses fixed
arguments and the pinned compiler/scaffolding. It executes no produced strategy
binary. A local job reserves resources first; its receipt, observed result and
actual local cost are retained. Failed compilations cannot be submitted. Unknown
completion retains its reservation for reconciliation. Repeated admission adopts
the same known receipt. Offline verification checks that a successful compile
receipt preceded admission, with matching source/profile/world/cost; it never
invokes a compiler, provider or backend.

The separate compiled artificial fixture deliberately builds and executes a
trusted toy program with a fixed artificial evaluator. It exercises entry and
cancel conditions, scheduling and position/quantity controls, retains a negative
candidate and revises direction from observed results. These are artificial
engineering outcomes, not trading observations.

```sh
jarvis --store .jarvis/design-fixture research design-fixture
# Optional paid LLM direction role, still an artificial backend:
jarvis --store .jarvis/design-llm research design-fixture --agent codex
jarvis iris continuous-prepare --upstream "$ACCEPTED_PACKAGE" \
  --python "$HERMES_PYTHON" --owner-instruction "$OWNER_INSTRUCTION" \
  --design-root "$PRIVATE_PROFILES" --output local/design-study.json
```

The private profile folder supplies `long.json` and `short.json`. Real preparation
still requires full upstream model acceptance. A local compiler pass does not
bypass that gate or establish model/strategy efficacy.

## Preserving an active predecessor

Implement and run a successor from an isolated checkout/environment when an older
watcher is active. Do not update that watcher's source files or editable package.

```sh
jarvis --store .jarvis/iris-live iris continuous-watch \
  --upstream "$ACCEPTED_PACKAGE" --python "$HERMES_PYTHON" \
  --owner-instruction "$OWNER_INSTRUCTION" --design-root "$PRIVATE_PROFILES" \
  --state local/iris/design-state.json \
  --predecessor-state local/iris/live-state.json \
  --wall-seconds 14400 --poll-seconds 45
```

The successor waits while the predecessor owns its file lease. It cannot dispatch
provider, quota or backend calls during that wait. After the lease is released,
every predecessor/child job must have known, settled receipts, and its histories
must verify. Unknown, failed, cancelled or paused states require attention rather
than automatic migration. The successor retains the predecessor lease throughout
its invocation, saves its new study identity before dispatch, pauses the old
study and records a versioned predecessor link. Compatible source/model/side/period
histories remain available as causal prior evidence. The same store/account quota
ledger and candidate identities continue across both versions. No active job is
migrated or resubmitted. An unstarted prior candidate remains an unobserved branch.

This extends L1 domain-artifact research. It does not establish L2 capability
learning, L3 real-policy promotion, independent market validation or live trading.
