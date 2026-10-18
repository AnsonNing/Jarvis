# Independent Critic for bounded research programs

The reviewed path separates Director and Critic into different provider calls,
prompts, schemas, immutable input snapshots, receipts and call allowances. They
may use the same configured LLM/model; this is role/context independence, not
evidence that their errors are statistically independent. Neither role can run
tools or change the fixed numeric evaluator.

## Sequence and responsibilities

1. The trusted queue verifies or reuses the frozen baseline.
2. The Director proposes falsifiable hypotheses and bounded settings using only
   visible development evidence and declared adapter capabilities.
3. The proposal Critic assesses each proposal exactly once. It can approve,
   require revision or reject, identifying confounds, missing controls, capability
   gaps and risk. Only approved proposals can enter the execution queue; the
   trusted adapter still performs its own admission and preflight.
4. After admitted work has terminal receipts, a separate result Critic call
   reviews the original hypotheses against observed results, risks and negative
   evidence. It distinguishes supported, negative, inconclusive, invalid and
   unexecuted proposals. These classifications are advisory; evaluator values and
   validation/promotion gates remain unchanged.
5. The next Director receives both critiques and admission feedback. A stop
   recommendation closes the program after retaining reviews, rejected proposals
   and results. Continuous research respects it, including its integration phase.
   The next epoch starts only after result review and history sealing.

Review-enabled epochs wait for completed result critique before planning again.
The earlier overlap behavior remains available to persisted programs without a
Critic. A critique can veto research admission but cannot grant an unavailable
capability or authorize a backend submission.

## Declaration and compatibility

`ResearchProgramSpec.critic` and `IrisContinuousSpec.critic` are optional typed
`AgentConfig` records. Omitted/null values retain the earlier combined-role path,
preserving old histories and their raw manifest hashes. New declarations from
`research prepare` and `iris continuous-prepare` include a Critic. Existing saved
declarations and cancelled watchers are never edited or restarted automatically.
Prepare a new reviewed declaration when a subsequently authorized study begins.

The library accepts a separate `critic_provider_factory`; its default uses the
configured provider factory with the Critic configuration. Both identities are
pinned in the program manifest. Director and Critic call limits are separate,
while actual/pending tokens and wall time share the fixed program budget. Reports
include per-role usage plus the combined ledger. Two Critic calls per batch add
measurable cost; no research-efficiency gain is asserted here.

Role input and output schemas are frozen before reservation. A restart uses the
exact snapshot and existing receipt. An uncertain started call retains its
reservation and enters `NEEDS_RECONCILIATION`; it is not resubmitted. Malformed
observed replies enter `CRITIC_NEEDS_ATTENTION` with cost retained. Insufficient
call/token/wall allowances enter `BUDGET_EXHAUSTED`; execution cannot bypass an
unfinished review. The CLI and continuous coordinator stop accordingly.

## Artificial acceptance

```sh
# Declared engineering providers; no LLM, credentials or remote Iris access:
jarvis --store .jarvis/critic-fixture research critic-fixture

# Real Director and independent Critic calls, still an artificial backend:
jarvis --store .jarvis/critic-llm-fixture research critic-fixture --agent codex
```

The deterministic control covers two adaptive epochs, one rejected confounded
proposal, executed negative and positive results, and subsequent revision. It
uses two Director calls, four Critic calls and four artificial executions,
including a baseline executed once. The real-LLM command is bounded to one batch,
one Director call and two independent Critic calls. On unknown completion,
preserve the same store/program and inspect the receipt; do not rerun to hide a
failed or uncertain paid attempt.

`program_report`/`verify_program` audit recorded contexts, provider/config
identities, time-visible evidence, proposal coverage, approval-before-enqueue,
cost, result review and sealed history with zero external calls. Tests cover
receipt-only recovery, unknown completion, invalid/stale reviews, unavailable
evidence, duplicate/missing assessments, insufficient budget, empty stops and
continuous stop propagation.

This feature connects to the Iris-style research program and rolling-program
coordinator. It does not add independent Critic calls to legacy local ResearchTurn
campaigns or the Dream policy developer. Critic implementation is not L2 capability
learning or L3 policy improvement. Real Hermes research, independent
heldout/fresh/canary validation and live trading remain unexecuted in this release
acceptance. See the release receipt for checks actually executed and limitations.
