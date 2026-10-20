# P14 — Recovery and sustained research

## Known invalid answers

Research Lab freezes exact allowed evidence IDs into each structured role schema.
Curator contexts contain measured results and comparisons, without unrelated
source/opinion payload. The runtime checks references, context identity and typed
output after the provider returns. A known invalid answer is retained with its
receipt, cost and rejection reason. Up to two explicit corrective calls use new
contexts and receipts; restart adopts known receipts rather than repeating them.
Unknown completion or usage never enters this path: its reservation is retained
for reconciliation. Outcomes, fixed evaluators and committed evidence do not change.

## Explicit historical recovery

`ExperienceRecovery` creates a separate successor for a closed measured program
whose original Lab did not complete curation. It checks the immutable original
manifest, complete receipts, closed program and absence of pending resources
before allowing a new Curator call. The recovered provisional experience retains
original observations and producer lineage. The original Lab status, failed
answer, world, source identity, costs and decisions remain unchanged. No original
experiment is re-executed. Unknown original work blocks recovery.

New sessions may explicitly reuse prior integration queues. These must be
settled, sealed, audited, match the declared side/source/model/evaluator/origin,
and include a successful baseline plus an observed intervention effect. This
preserves negative performance and Critic recommendations. Integration evidence
does not become an independent strategy verdict.

## Critic evidence and stop scope

Before critique the trusted renderer provides the proposed local diff and
candidate artifact identity, labeled rendered rather than compiled or executed.
After admission, decision-time evidence includes actual compile receipts,
candidate bytes, declared controls and measured result lineage. The offline audit
reconstructs execution visibility at the original decision time; later results
cannot enter earlier snapshots. Hidden remote caller traces and complete live
costs remain separate unavailable capabilities.

A negative candidate is retained as evidence. A Critic stop closes that program;
the task controller separately assesses other admitted hypotheses and directions.
It cannot undo a stopped program, relax constraints or bypass the next review.

## Mission continuation and truthful checkpoints

`IrisResearchMission` joins finite reviewed sessions under one task budget. Initial
coverage and allowed continuation templates are immutable. After a session closes,
the declared LLM controller reviews completed observations and costs, then either
selects one template with new directions or records a concrete task-wide stop.
Further sessions retain fixed model, evaluator, PR/MDD selection, provider and
quota controls, compatible prior queues and scoped experiences. There is no fixed
lifetime experiment count; finite budgets and evidence of research value remain
required.

The single-owner runner distinguishes successful child operations from a complete
mission. It atomically writes status, process liveness, the last operation and
task completion. Exceptions record a diagnostic and finalize the checkpoint and
private notebook. A wall-time limit is a resumable invocation limit, not task
completion. A supervisor can resume the same mission/store with saved arguments
after checking process exit, source identity and receipts. Duplicate runners and
blind unknown-call retries remain prohibited.

```sh
jarvis lab recover-experience EPISODE --authority 'Explicit recovery scope'
jarvis lab recovery-advance RECOVERY
jarvis lab mission-start PRIVATE_SPEC.yaml
jarvis lab mission-run MISSION --state PRIVATE_STATE.json --notebook PRIVATE_NOTES
jarvis lab mission-report MISSION
```

Real models, candidate source, metrics, traces, notebooks and configuration remain
private ignored artifacts. Recurring supervision is an explicit deployment action,
separate from the library and fixtures. Dream/capability promotion still requires
independent utility/effectiveness protocols. Continuation alone is not an improved
research policy.

## Read-only transport supervision

`scripts/iris_mission_supervisor.py` is a separately versioned operating harness.
It does not edit or replace the active library. Its source hash and artifact are
recorded when attached. A single supervisor lock prevents duplicate supervisors;
the saved process command and mission identity prevent duplicate runners.

Only a classified collection-time SlackError on a durably known SUBMITTED request
can enter automatic read recovery. Any unfinished provider or other operation,
unknown submission, owner pause/cancel, runtime drift or unexpected failure stops
that path. The existing queue collects the same request ID. A known read outcome
and explicit lineage record are required before affected ancestor states resume.
Original failure events, reservations and costs remain. No candidate is resubmitted.

The supervisor also resumes invocation limits with the same saved mission/store/
arguments. Unclassified failures remain attention states for independent diagnosis.
This is controlled infrastructure recovery, not agent-discovered policy improvement.

```sh
python scripts/iris_mission_supervisor.py --process PRIVATE_PROCESS.json --state PRIVATE_SUPERVISOR_STATE.json
pytest -q tests/test_iris_read_supervisor.py
```

The three synthetic controls passed: same-request read recovery with unchanged
submission/provider-call counts, preserved unknown provider reservations, and an
owner pause that cannot be cleared. The kernel identity remains unchanged while
this harness is tested and deployed.
