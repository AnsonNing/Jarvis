# 02 — Research Lifecycle, State Machines and Decision Protocol

## A. Campaign lifecycle (durable)

~~~text
CREATED
  → VALIDATING → READY → BASELINE_PENDING → BASELINE_COMPLETE
  → DIAGNOSING → PLANNING → SEARCHING
  → CANDIDATE_FROZEN → FORMAL_PENDING → FORMAL_COMPLETE
  → CURATING → REPORTED → CLOSED
~~~

Other explicit states: WAITING_FOR_INPUT, PAUSED, INTERRUPTED, EXHAUSTED, FAILED, INCONCLUSIVE, CANCELLED. A run can report a useful negative conclusion: CLOSED does not mean IMPROVED. Error conditions preserve all completed evidence and charges.

Transitions require declared guards and persist atomically before expensive work; examples: SEARCHING requires World/evaluator/adapter admitted and budget available; FORMAL_PENDING requires candidate, parameters, selection rationale, split boundary and primary metric frozen; REPORTED requires cited evidence and accurate status.

No test reveal can return a campaign to SEARCHING on that same locked cohort. New experiments after a locked reveal require a new versioned campaign and a new untouched future test period/cohort.

## B. Experiment/job lifecycle

~~~text
PROPOSED → ADMITTED → RESERVED → QUEUED → LEASED → RUNNING
                                             │
                               SUCCEEDED / FAILED / INTERRUPTED
                                             │
                                        FINALIZED
~~~

Rejected proposals receive NOT_ADMITTED with reason (budget, permissions, missing data, invalid split, unsupported capability). A technical failure is EXECUTION_FAILED or INFRA_INTERRUPTED, not RESEARCH_NEGATIVE. Retry infrastructure only when action idempotency and failure semantics permit; preserve all attempts/cost. Unknown completion due to disconnect enters reconciliation, not automatic duplicate execution.

## C. Per-decision research algorithm

1. Build a ResearchState snapshot **as-of decision time**, with current hypothesis, observed results, remaining budget, eligible actions and provenance.
2. Produce 1–N falsifiable hypotheses with expected observable evidence, alternatives, estimated resources and failure information.
3. Compute frontier: tool- and stage-compatible candidate ActionSpecs. Q0 selects based on declared base policy; it may include cheap diagnostic, explore, refine, compare, stop or ask-input.
4. Trusted admission validates syntax, source permissions, available split and budget. Reserve atomic action budget.
5. Execute action(s), append one result per attempt. If an experiment has many tool jobs, aggregate only after every required job has terminal status.
6. Deterministic Evaluator produces metrics and verification scope. Critic links conclusions to actual refs; disagreements or missing data are explicit.
7. Replan: CONTINUE, BRANCH, STOP, INCONCLUSIVE, or NEEDS_INPUT. Each decision carries evidence IDs and why rejected alternatives lost priority.
8. If no credible direction remains or declared resources are exhausted, stop with best-known **development** candidate or no candidate; formal evaluation is separate.
9. Freeze candidate and evaluate once on the declared locked test (if available); report, append reusable knowledge candidates and close.

No infinite self-recursion: enforce max steps, tool quotas, wall time, budget and external STOP, with separate budget for optional policy dreaming.

## D. Research graph and experiment identity

- Root node = fixed baseline World + agent snapshot, no experiment result fabricated.
- Each child = one *attempt*, not just one successful model; parent points to the starting candidate/decision state.
- Save visible_siblings, visible_evidence_ids, frontier_id, action_id, snapshot_hash, candidate_commit, tool_receipts, evaluation_id, cost_actual, timing, and observed result.
- Same code candidate tested with different declared objective/test/splits = different experiment identity; identical content hash alone does not imply comparable World.
- Supporting analyses, literature reads, diagnostics and model training are all real research actions with honest resource charges. Non-evaluated proposals are frontier items only, not nodes with results.

## E. Information visibility

The strongest invariant is **no hindsight**. State reconstruction for decision t MUST be able to hide later evaluation, later siblings, future market observations and post-hoc annotations. Use ordered journal sequence and immutable evidence visibility snapshot. If an observer read later evidence in a session, do not claim an earlier simulated decision did not see it without evidence.

Formal evaluation data access is restricted by evaluator service; a Research Agent sees aggregate formal verdict only when allowed, and never raw labels/outcomes used to choose same-round candidates. Dataset split IDs are scoped immutable objects.

## F. Adaptive research without objective drift

Jarvis may revise experiment plans, select a new hypothesis, abandon a model family, or request new data. It may not alter the campaign's primary objective, risk limits, original baseline definition or formal comparator just because results are poor. A legitimate requirement change must create a linked campaign revision requiring explicit user approval or declared project policy.

## G. Example synthetic end-to-end run

World: optimize a toy algorithmic function under 12 deterministic evaluations. Baseline diagnostic reveals poor sensitivity in one parameter. Director proposes H1 and H2. Q0 chooses H1, executes and receives a negative score. Critic points to numerical instability; Q0 chooses a cheap diagnostic then branches to H2. H2 is validated on development data; candidate frozen; untouched synthetic locked seeds tested; knowledge curator records H1 failure and H2 scoped finding. Replay reconstructs the exact order with zero calls to toy evaluator.

This fixture must be completely deterministic and scientifically uninteresting. It validates Agent System behavior before proprietary trading integrations.

## H. Idempotent recovery example

Process crashes after an expensive tool completed but before result aggregation. On restart, reconcile the durable job ID and raw tool receipt; if a result artifact was sealed, adopt it without rerunning. If output is missing and underlying operation might still be running, mark UNCERTAIN and inspect worker lease/heartbeat; never dispatch a duplicate merely because the orchestrator lacks a return message. Resume from the most recent committed event and assert no loss of already incurred costs.
