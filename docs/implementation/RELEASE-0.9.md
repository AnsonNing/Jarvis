# Release 0.9 independent Critic acceptance

Scope: independent hypothesis/design review before admission, separate result
review after fixed evaluation, and critique-informed next-epoch planning.
The Director and Critic have separate role calls, schemas, contexts, receipts,
provider identities and call allowances. Their total resources remain bounded
by one program ledger. Models may be shared; statistical error independence and
research efficiency improvement are not established.

## Executed engineering acceptance

The complete local suite passed **214 tests in 89.72s** after core integration.
Ruff lint/format passed for 83 Python files, mypy passed for 60 source modules,
and the installed pinned dependencies passed `pip check`.
The final schema-export CLI emitted both Critic contracts successfully, and the
twenty Critic tests passed again in 4.30 seconds after that CLI registration.
The earlier completed 0.7 real-LLM/artificial-design program also passed the
current offline verifier without new provider calls. The local editable package
was installed as 0.9.0.

The deterministic `research critic-fixture` acceptance completed two adaptive
epochs, two Director calls, four Critic calls and four artificial executions.
It retained a positive experiment and actual negative experiments, blocked a
confounded proposal before enqueue, reused the baseline, and supplied both
critiques to the next Director. Offline program/queue/role verification passed
with zero external calls. Twenty added tests cover the two review stages,
receipt-only recovery, unknown-call reservation retention, invalid/stale output,
unavailable evidence, duplicate/missing assessments, budget exhaustion, empty
stops, continuous stop propagation and forged-review rejection.

An initial fixture assertion exposed that its nominal negative candidate still
beat the fixed artificial baseline. The candidate was corrected; the evaluator
was preserved. A separate integration test exposed a fixture Director exceeding
the one-proposal integration envelope. It now honors the declared batch bound.
Earlier failed attempts remain distinct from the final successful checks.

## Executed real-LLM / artificial-backend acceptance

`research critic-fixture --agent codex` completed **CLOSED / VERIFIED** with:

- One observed Director call and two separate observed Critic calls.
- **45,534 observed tokens**: Director 14,552; Critic 30,982.
- Combined observed provider wall time 72.51 seconds; pending tokens/time zero.
- Two artificial backend executions: baseline and one admitted intervention.
- Proposal review approved the bounded single intervention. Result review cited
  its observed improvement while retaining artificial-data, small-sample,
  cost-detail, mechanism and independent-validation limits.
- Program, queue and role receipt verification passed offline with zero calls.

The run did not fill its two-proposal capacity with unnecessary candidates.
Raw requests, prompts, usage, provider events and evidence hashes remain in the
ignored private store. No provider/model default resolution or monetary charge
is inferred beyond the recorded CLI identity and observed token usage.

## Scope and next action

New preparation commands include the typed Critic configuration; historical
declarations without it preserve their original role path and manifest identity.
Cancelled watchers and the deleted recurring automation remain cancelled.
No real Hermes study, remote Iris submission, real strategy backtest, model
training, independent heldout/fresh/canary run or live trading started here.

The accepted ordering for future authorization remains: trustworthy frozen
strategy baseline first, then matched research-policy/efficiency comparisons.
The Critic is connected to Iris-style programs and continuous coordination, not
legacy local ResearchTurn campaigns or the Dream policy developer. Independent
data and real execution capabilities remain prerequisites for stronger claims.
See [P10 implementation](P10-independent-critic.md) for declarations, commands,
durable stopping behavior and compatibility. Linux CI separately runs the full
suite plus the new deterministic Critic CLI after publication.
