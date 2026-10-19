# Release 0.11 multi-agent research and self-improvement acceptance

Scope: strengthen the existing Research Agent + independent Critic with a
bounded specialist team, public source discovery, durable human input, scoped
experience reuse, and separately evaluated research-method, tool-capability and
exploration-policy changes. Real Hermes research remains unstarted.

## Executed verification

The final fixed-environment suite passed **259 tests in 305.25 seconds**.
Ruff lint/format passed for 98 Python files and mypy passed for 73 source
modules. The installed package and source both identify as 0.11.0;
`pip check` passed, 90 schemas exported, and the bounded runner help and
example-task validation passed. Twenty-seven new tests cover this work package.
Linux CI runs the complete suite and all earlier CLI acceptances, followed by
the new complete `lab fixture` command.

During development the validation-family gate rejected an attempt to reuse a
validation cohort across improvement axes. The fixture now supplies distinct
mathematical functions and model lineages for those axes; the gate was retained.
Artifact-tamper testing exposed an audit that compared reference hashes without
materializing the referenced answer. Role, tool and preparation audits now read
the referenced artifacts and recheck each separate audit request. Another test
expectation was corrected to the actual fixed mathematical result; the evaluator
was not changed.

An initial full-suite run passed 255 tests and failed one recovery test because
package installation ran concurrently with testing: its frozen dependency
manifest declared 0.10.0, while resume observed 0.11.0. Inspection confirmed an
unchanged source hash and that single dependency-version change. The guard was
correct to refuse resume. Installation and final testing now run sequentially;
no environment-identity requirement was relaxed.

## Complete artificial acceptance

The foundation executes independent specialists, directed peer questions,
source/human inputs, the original Director/Critic program, and Curator records.
A new mathematical task actually retrieves compatible experience. Its matched
memory-only comparison holds the new task, method, provider and full budget
fixed. A different-family negative control refuses the experience. Observed
fixture scores and execution counts are engineering controls, not market data.
The installed `lab fixture` completed as VERIFIED with **154 artificial backend
executions** across 77 Lab episodes. The foundation control scores were
1.0, 4.0 and 4.75. On the changed new task, the memory-only matched comparison
measured 1.0 without retrieval versus 4.75 with compatible experience.

Three separate prospective studies then execute the complete process:

| Axis | Actual validation and continuation | Observed acceptance result |
| --- | --- | --- |
| Research method | Development candidate probes; frozen finalist; heldout and fresh paired programs; actual adopted continuation; changed-condition canary; fallback continuation | Measured regression produces ROLLED_BACK |
| Tool capability | Agent-proposed closed diagnostic; metered fixed fidelity cases; same-budget independent utility pairs; agent tool selection and subsequent-task adoption | APPROVED_FOR_RESEARCH within the declared artificial protocol |
| Exploration policy | Ten or more distinct initial development/heldout histories; three development families; bounded action-policy proposal; prospective independent pairs; adopted continuations | APPROVED_FOR_RESEARCH within the declared artificial protocol |

Each study feeds its two new continuation histories into a second proposal
cycle, then stops with NEW_INDEPENDENT_VALIDATION_REQUIRED. Exposed validation
is not reused. A further actual task pins the retained or fallback profile.
Cross-axis profile composition requires separate joint validation rather than
silently treating independent single-axis gains as a combined verdict.

The method and capability studies each completed 17 Lab episodes, and the
policy study completed 36. Their full ledgers recorded 13,800, 12,700 and
22,000 simulated token units respectively; every pending resource dimension
was zero. These totals include collection, developer calls and all study
descendants, rather than only the selected successful experiment. The foundation
and three subsequent profile-pinned tasks are separately retained. The entire
acceptance made zero external LLM calls and zero remote Iris submissions.
Exact replay returned COMPLETE; an unrecorded path returned INSUFFICIENT_COVERAGE.

The existing trusted queue, fixed evaluator and Critic remain on the execution
path. Agent opinions cannot create measured outcomes or bypass proposal review.
All role calls, source queries, diagnostic/fidelity operations, compilation,
baseline and backend work remain in shared actual/pending budget accounting.
Acquisition/development overhead remains visible separately from paired
application cost. Fixture providers explicitly declare simulated token/time
units. Remote compute, queue time and monetary charges remain unavailable.

## Public-source smoke acceptance

Both read-only source CLI adapters were actually queried without an LLM or
Iris submission. Crossref returned three literature records and GitHub returned
three implementation records. In these two queries every returned document was
metadata, with one metered tool receipt per query and no pending resources.
This establishes live API retrieval, not full-paper review, code reproduction
or usefulness to a trading strategy. Returned content stays in ignored storage.
CI uses controlled source responses and does not depend on external services.

## Recovery, evidence and limits

Tests cover genuine specialist overlap, identical initial preparation snapshots,
bounded peer exchange, board pinning, causal experience use, rejected restricted
experience, immutable method controls, equal-budget enforcement, provider scope,
validation-cohort reuse, receipt-only recovery and retained unknown operations.
Pause/cancel also blocks explicit capability proposals. Incomplete or altered
replay paths return INSUFFICIENT_COVERAGE with zero external calls. Offline
audits neither rerun diagnostic functions nor start provider/backend jobs.

The initial capability is a closed `group_mean_argmax` diagnostic recipe over
declared development observations. This is a tested proposal/fidelity/utility/
reuse path for one supported tool operation. Arbitrary generated-code isolation,
general capability acquisition, automatic full-paper/PDF reading, repository
execution and LLM weight training remain future work. Abstracts are consumed
only when actually returned; explicit owner-imported full text retains its
provenance and read-scope label.

Synthetic acceptance establishes executable control flow, evidence handling,
adoption and rollback. It does not establish general research efficiency,
monotonic improvement, real-domain transfer or financial gain. Actual LLM role
quality and Hermes effectiveness require a later separately declared task,
accepted adapters, finite budgets and genuinely independent validation data.
Cancelled watchers stay stopped, and no recurring task is created.

Commands and contracts are in
[P12 research self-improvement](P12-research-self-improvement.md).
