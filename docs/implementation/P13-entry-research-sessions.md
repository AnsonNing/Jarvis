# P13 — Selected entry research sessions

## Boundary and controls

The owner supplies the selected model package, checksum index, source snapshots,
settings, calendars, original completed-job manifest and an explicit local
selection record. `prepare_selected` checks these together and emits a pinned
environment and baseline proof. This path is separate from the older multi-month
workflow admission gate: it neither changes that gate nor relabels its findings.
Known findings and exposed-data status remain in the model lineage. Actual
original job commits remain distinct from a source-equivalent selected commit.

An optional `entry_trigger_bps` binding exposes a side-specific price trigger.
The trusted binding declares its symbol, region, scale, range and unit. It is
separate from `trading_cutoff_delta`, the model-score cutoff. Baseline rendering
remains byte-identical. Compilation proves syntax and declared bindings; actual
backtest interventions are required to test consumption by hidden callers.
Filled entry counts do not establish the full opportunity or order funnel.

Explicit date-subset baseline proofs preserve the original raw ledger hash,
completed job period, source and parent trading calendar. The trusted worker
first evaluates the original ledger under that calendar, then evaluates a
derived CSV with exactly the declared subset. No remote baseline rerun occurs.
The fixed evaluator is unchanged. A wrong parent calendar, source, date coverage
or local selection record fails admission. Dependencies next to the supplied
evaluator are imported from its declared directory.

`ResearchAcceptance` optionally declares a maximum relative mean-PR decline,
maximum relative absolute daily-realized-MDD increase and positive-PL-gain
requirement. Reports retain every candidate, per-check reasons and an explicitly
qualified best candidate. Missing/non-finite metrics are insufficient evidence.
This selection protocol does not alter the fixed objective or hide a risk penalty.
Evolution protocols pin these controls when present. Legacy serialized records
omit new optional fields when absent, preserving old identities.

## Sequential reviewed research

An `IrisLabSessionSpec` declares all stages before execution. Each stage is an
ordinary Research Lab episode: source and experience retrieval, a Director-selected
specialist team, independent initial opinions, directed discussion, reviewed
experiments and Curator output. The session rejects a model/effort mismatch among
specialists, Director and Critic. Optional AgentConfig reasoning effort is also
part of the provider identity and Codex invocation.

Smoke stages precede development. A successful non-baseline intervention must
change measured PL, count, daily PL or the entry execution hash. Otherwise the
session stops with SMOKE_NEEDS_ATTENTION. This is an integration gate, not a
statistical or economic success criterion. Completed compatible experiences are
added only to later declared episodes; sibling initial opinions see the same
frozen evidence. A shared root budget includes preparation and all descendants.

The session owns one conservative quota ledger. Timestamped acknowledgements
are snapshots, not live quota; pending reservations and uncovered local attempts
reduce availability. Missing or stale evidence blocks dispatch. Request IDs and
candidate identities are durable. Unknown operations stop for reconciliation
and are not silently resent. Session pause/cancel stops further execution.
Finite stage sizes permit audit and resume; separately declared later sessions
can continue when actual research value, owner authority and quota justify them.
No old watcher or cancelled workflow is restarted by this API.

Optional final reviews propose bounded research-method and action-policy changes
from observed development histories. They record independent coverage as absent
and promotion as false. An optional capability proposal uses the existing closed
diagnostic fidelity path and remains utility-unverified. None of these records
is a demonstrated real-domain self-improvement gain. Independent heldout/fresh/
canary tasks, sufficient distinct histories/families and complete paired costs
are still required for promotion.

## Commands and private evidence

```sh
jarvis --store .jarvis/session-acceptance lab session-fixture
jarvis lab session-start --help
jarvis lab session-advance --help
jarvis lab session-report --help
jarvis lab session-quota --help
jarvis lab notebook --help
```

`lab notebook OWNER --output PRIVATE_DIRECTORY` exports an offline `audit.json`
and readable `NOTEBOOK.md`. It includes immutable manifests, observable role
contexts and final structured answers, source/tool/experiment/critic/experience/
Dream/capability events, receipt references and full actual/pending usage. It
makes zero external calls. It does not infer or expose hidden chain of thought.
Raw provider outputs and content-addressed artifacts stay in the private store.
Export directories and real experiment evidence must remain outside public Git.

## Executed engineering acceptance

The complete deterministic `lab session-fixture` executed two stages through the
existing framework and closed with VERIFIED quota accounting and zero pending
usage. It executed two artificial remote-adapter attempts, carried actual declared
experience forward, generated a closed capability candidate and two retrospective
review records. It made zero external LLM calls and zero real Iris submissions.
Scripted token units remain labeled simulation units. Notebook export did not
increase resource usage. Tests also cover model/effort identity, baseline subset
lineage, selected-package integrity, side-specific trigger rendering and PR/MDD
candidate feasibility. See RELEASE-0.12.md for the final regression evidence.
