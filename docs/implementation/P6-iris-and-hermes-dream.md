# Iris research programs and Hermes Dream-RSI

Version 0.5. Date: 2026-10-18. The strategy model, calendar, execution backend and
account remain supplied by the owner. No private strategy source, feature formula,
credentials, model weights, trade rows or numerical financial reports belong in
this public repository.

## Run a strategy research program

The owner supplies the frozen strategy, objective, directions and finite budget.
The direction role sees measured entry-only results, declared setting units,
existing candidates and risk vectors. It proposes up to three single-factor or
explicitly joint experiments with hypotheses, falsification criteria and visible
evidence references. Unsupported abilities become recorded capability requests.
No LLM-generated command, arbitrary source edit or evaluator change is executed.

The admitted settings are take-profit/stop-loss basis points, trading cutoff
delta, model cancellation risk delta, heuristic cancellation basis points,
sampling interval and the declared heuristic/model bypass flags. The private
binding manifest maps these to constants in approved writable regions. Unbound,
ambiguous, out-of-range and protected-region changes are rejected. Probability
cutoffs and regression scores retain their model-specific units.

Before real execution, the completed multi-month workflow must pass history,
training quality, HPP export, Python/C++ parity, compiled source, model smoke and
all fixed comparisons. The workflow must say `complete`; a partial result, old
single-month artifact or successful health check does not satisfy admission.
Preparation pins the accepted inputs and leaves the source repository untouched.

```sh
jarvis iris model-status --upstream "$ACCEPTED_PACKAGE"
jarvis iris prepare --upstream "$ACCEPTED_PACKAGE" --side long --month 08 \
  --python "$HERMES_PYTHON" --output .jarvis/private/long-environment.json
jarvis research prepare --environment .jarvis/private/long-environment.json \
  --baseline .jarvis/private/long-environment-baseline.json \
  --direction 'Explore exits, cancellation and entry decisions; retain every risk result' \
  --batches 3 --output .jarvis/private/long-program.json
jarvis research start .jarvis/private/long-program.json
jarvis research advance <program-id>
jarvis research report <program-id>
```

The already completed baseline is reconstructed locally from its native ledger,
source hash, request/commit, explicit complete calendar and fixed entry evaluator.
This uses zero uploads and does not load Iris credentials. Reusing a baseline in
a later identical epoch also uses zero remote submissions. The local reconstruction
is still a charged local action, with recorded compute and wall time.

New remote submissions require an `IrisAuthorization` bound to the exact
environment digest, count, expiry and owner approval note. A program can first
analyze its imported baseline and prepare candidates without this grant. The
operator then records its single immutable grant; the direction role has no tools
and cannot grant or renew permission. Linked epochs share that same count.

```sh
jarvis iris authorize <queue-id> .jarvis/private/owner-authorization.json
jarvis research run <program-id> --max-steps 100 --wall-seconds 1800 --poll-seconds 45
jarvis iris pause <queue-id>
jarvis iris queue-verify <queue-id>
```

The grant schema is exported with `jarvis schema`; `environment_id` is the
canonical digest of the private environment. An owner chooses the period and
number before submission under their upstream operating protocol. The runner
stops at its step/time envelope, a submission limit, pause, unknown operation or
missing approval. Repeating it resumes known work. Existing remote jobs are
collected during a pause. It never submits again after an uncertain upload.

Every upload has a durable request ID allocated before upload. Recovery collects
that ID. A receipt interruption adopts the saved receipt once. Queue acknowledgement
is a timestamped backend response, not a promise of current shared service quota.
The local submission allowance counts attempts conservatively; remote compute
and queue time are unavailable and are never fabricated as measured resources.

The director may plan the next epoch while the current epoch waits for Iris.
Its evidence snapshot is frozen before its call. A subsequent catalog goes in a
new linked epoch; it cannot rewrite the old tree after learning its outcomes.
Each epoch admits at most `max_tasks`; batches are separately bounded by
`max_direction_batches`, LLM call/token budget and the shared submission grant.
Known identical candidates are deduplicated across compatible linked epochs.

## Fixed objective and risk evidence

The primary objective is total **entry-only PL** from the supplied raw ledger and
explicit market calendar. Snapshot records do not contribute PL. Raw commit,
request, evaluator, ledger and accepted model identities are retained. Empty
market days are included. Reports retain every failed or negative candidate.

Reports show PL, count, daily realized drawdown from starting equity zero, worst
day, daily PL, PL with the best day removed, positive product concentration,
mean PR, zero-entry days, entry extremes, entry win rate and five worst entries.
They show per-candidate PL/count/drawdown/worst-day deltas against the baseline.
Risk does not silently become a penalty or change the optimization objective.
Daily realized drawdown does not establish intraday drawdown, capital usage,
capacity, live costs or cancel acknowledgement. Source PL cost semantics remain
explicitly unverified until the backend supplies that contract.

Iris can execute its existing stop/target and cancellation parameters. A remote
parameter comparison does not expose per-tick stop fills or prove a hard loss cap.
The separate local quote simulator still requires actual postfill paths, units,
fees and execution assumptions. Full event cancellation needs all orders,
acknowledgements, partial fills and race resolution. See the
[execution data contract](../adapters/exit-and-cancel-data.md).

## Hermes Dream-RSI

Each sealed `HermesWorldHistory` contains the predeclared candidate catalog,
actual result/cost receipts, source/period identity and event-prefix hash. Exact
replay reveals only the actions selected by a policy. Missing outcomes or evidence
dependencies return `INSUFFICIENT_COVERAGE`; replay performs zero provider,
worker, simulator or network calls. A prior epoch's evidence is usable only if it
was available when the new epoch was created.

The policy DSL controls family priority, after-loss priority and a no-gain stop
rule. Q0 is the declared comparator. The developer proposes two to five policies
using development histories only. Under a fixed experiment budget, a candidate
must not regress in per-world best entry-only PL. Cost can break quality ties;
raw PL is never summed across incomparable tasks. Risk remains in every result.

The engine requires at least ten distinct histories and three declared families,
freezes one development finalist, evaluates one-shot independent heldout groups,
runs fresh paired policy-arm campaigns, activates the research policy and checks
a subsequent canary. A measured canary regression rolls back to Q0. Histories,
group exposure, finalist, policy versions and results remain durable. Renaming a
queue/model/group cannot reset real market exposure. Partial date overlap across
development, heldout, fresh and canary is denied.

The verifier independently recomputes development selection, heldout verdict,
prospective execution order, budget, catalog, costs and final policy status using
only saved evidence. An approval without all required gates is invalid. Fresh
arms use declared realized experiments, not a surrogate prediction of an unknown
branch. Repeated identical real submissions must first be permitted by the
owner's backend protocol; the artificial paired fixture does not grant this.

```sh
jarvis iris world-seal <queue-id> --family <declared-task-family>
jarvis hermes-dream start .jarvis/private/dream-spec.json
jarvis hermes-dream advance <study-id>
jarvis hermes-dream verify <study-id>
jarvis hermes-dream report <study-id>
```

The fixture collects twelve artificial histories over six objective families,
executes a separate fresh objective and deliberately shifted canary, and verifies
rollback. Its numbers are engineering controls, not market returns. Small or
exposed real history pools return a negative/inconclusive gate and retain Q0.
Full real policy efficacy, general capability learning, arbitrary generated-code
execution and live trading are outside this release's demonstrated acceptance.
