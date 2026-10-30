# ADR-016 — Collaborative research planning and distinct stop scopes

Status: implemented; synthetic acceptance and regression verified with limitations.

The owner wants Jarvis to replace the research coordination previously done with an
agent while retaining normal human collaboration. Do not add a planner service,
mandatory branch quotas, numeric opportunity scoring or an extra provider call on
every step. The Director already has a typed question tree, branches, owner advice,
evidence visibility and controlled execution.

New contracts opt into `research_planning: {}`. A small `planning_review` on the
existing Director decision records falsifiable directions, qualitative effort and
priority, deferral/unavailability, provenance and the selected next step. Reviews
occur at framing, new branches, important criticism, owner redirects and stopping;
routine steps may omit them. Unresolved directions require explicit disposition
before omission. No minimum number of directions, families, tools or experiments
is enforced. Qualitative priorities remain unvalidated agent estimates.

Three stop scopes serve different purposes:

- `branch` closes an evidenced local branch and leaves other directions available.
- `session` needs a separate review: `goal_met`, subject to existing candidate and
  confirmation gates, or `no_justified_next_step`, explaining each remaining
  direction's disposition. A ready direction cannot coexist with an exhaustion
  claim. Explicitly justified deferral is allowed without executing that direction.
- `checkpoint` asks one concrete owner question and pauses with branches open.
  Existing `advise`, `resume` and `run` continue the same session. No routine approval
  is required. Guidance cannot alter the fixed evaluator, permissions or contract.

Checkpoint state is derived from its durable completed action, so an interruption
before writing the pause cannot silently resume work. Stop finalization is
idempotent. A checkpoint does not curate an unfinished session into a final memory
snapshot. Research method quotas cannot block this tool-free human handoff.

No database schema or new service is introduced. Optional fields are omitted when
absent; historical contracts/decisions retain their hashes and semantics. Audit
checks new planning against the original decision-time state, with zero external
calls. Reports keep proposal history, untested directions, stop basis and pending
owner questions distinct from measured outcomes. Existing records are not rewritten.

The fixed toy objective and scripted roles validate these mechanics only. They do
not demonstrate improved research quality, novel useful hypotheses, financial gains,
real-provider behavior or Dream effectiveness.
