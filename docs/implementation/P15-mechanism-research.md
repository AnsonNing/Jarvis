# P15 — Mechanism research, diagnostics and isolated skill construction

This release implements P0–P3 from the mechanism-research engineering proposal.
It keeps the existing Director, Critic, Store/Evidence Ledger, budgets, Iris
adapter and Dream evaluations. It does not start a Hermes study or restore a
paused mission. The execution boundary remains dependency-injected.

## Pipeline and identities

```text
Owner instruction + fixed baseline/evaluator/data + acceptance + freedom/constraints
  -> store-free preflight -> immutable ResearchContract + SkillAgentVersion
  -> Director: question + competing falsifiable hypotheses + typed next action
     INSPECT | DIAGNOSE | CONSULT | BUILD_SKILL | EXPERIMENT | STOP
  -> trusted harness executes only the admitted typed action
  -> independent Critic sees the actual result and assesses hypotheses
  -> next Director sees only evidence available at that decision time
  -> repeat, retain negative/unknown/insufficient evidence, export private notebook
```

A full owner instruction is preserved verbatim as contract data in Director,
Critic, Builder, specialist and child experiment contexts. Planning also receives the actual
baseline evidence, row-free input metadata and the declared experiment envelope;
opaque hashes alone do not convey the baseline. Captured input bytes are hashed
and parsed together so a changing source cannot silently mismatch its fingerprint.
The question tree is
append-only ledger events, not a parallel mutable state database. Each action
names its question, hypotheses, rationale, expected observation and evidence.
Specialist consultation is advisory; it cannot manufacture observed findings.
The existing Research Lab still provides source-search and discussion adapters;
consultation alone is not evidence that a paper or implementation was retrieved.

The session seals the source identity, code provenance, installed dependency
versions, baseline/input hashes, evaluator, exposed/synthetic dataset status,
provider identities, explicit model/effort, registry prefix, skill references,
builder base commit and budget. Default workflow roles use `gpt-6.1-sol/high`.
A child experiment Director and Critic must match that identity. All session role
calls, diagnostics, Builder tests, compilation/backend work and successors share
the owner cost ledger. Registry provisioning has its own receipted setup ledger,
reported separately; a future utility comparison must explicitly include that
provisioning cost rather than treating it as free. Unknown paid completion keeps its reservation and stops for reconciliation.

Known invalid structured responses may receive at most two separately receipted
corrections. The original answer, frozen prompt, error and cost remain. Settings
are not silently rewritten and experiment admission is not relaxed. Legacy saved
ResearchPrograms omit the new opt-in fields and retain their original contracts.

## P0 — Decide what evidence to seek

Director can inspect existing data before proposing a new backtest, diagnose a
previous observation, request an expert opinion, expose a capability gap, or stop
with a concrete scoped conclusion. Hypotheses include a prediction and a
falsification condition. Critic records supported/refuted/inconclusive assessments;
a descriptive PL decomposition is not automatically accepted as a causal result.

`EXPERIMENT` delegates to the existing reviewed ResearchProgram and adapter queue.
The complete experiment environment (including model lineage, inputs and period)
must be explicitly hash-pinned by the owner contract. The fixed evaluator and
acceptance contract must match; observed experiments need
explicit permission. No backend means UNAVAILABLE, not fabricated returns. Quota,
compile receipts, model lineage, proposal approval and result review still apply.

## P1 — Read-only diagnostics and data contracts

`DiagnosticSnapshot` carries model, policy, evaluator, dataset, period, exposure,
entity kind, keyspace, key provenance, source checksum and PL units. CSV imports
require explicit columns and lineage metadata, with bounded bytes and rows. No
column names, matching rules, time units or opportunity populations are guessed.

The first methods are inventory, paired-record PL decomposition and observed
score distributions. Comparisons refuse mismatched model/evaluator/dataset/period,
entity type, units or exposure. Pairing requires declared, unique stable keys.
Absent keyspace or duplicate IDs gives INSUFFICIENT_DATA. Unidentified rows remain
in an explicit residual. Exact Decimal accounting checks:

```text
PL delta = common-record PL delta + candidate-only identified PL
           - baseline-only identified PL + unmatched residual
```

Candidate-only **fills** are not called new opportunities. Fill-only snapshots
cannot explain rejected entries, unfilled orders, full Cancel decisions, position
exposure or the unobserved counterfactual. Such questions need extra source data:

| Question | Required evidence |
| --- | --- |
| Trigger changes the eligible opportunity set | Stable pre-gate opportunity IDs, full opportunity population, trigger values/times and gate decisions |
| Trading score or choice changes | Frozen model/feature identity, full scores and candidate-to-order lineage |
| Cancel affects execution | Order IDs, decision timestamps/scores/reasons, lifecycle events and fills |
| Risk or time exposure changes | Positions/quantities, session clock and timezone, mark/realized definitions, fees and daily ledger |
| Counterfactual strategy structure | Explicit adapter capabilities and controlled experiments with frozen evaluator; saved fills alone are insufficient |

The existing adapter remains the only path to real backtests. Importing a CSV or
passing preflight does not certify complete opportunity coverage or remote quota.

## P2 — Reusable skills

Public packages in `skills/` contain cards, checksum-pinned manifests, wrappers and
tests. Package installation can select only the trusted closed builtins. Registry
artifacts add pinned input/output schemas, domain, roles, permissions, dependency
requirements, resource limits, source provenance and receipted fidelity evidence.
Name/version is immutable, including revoked versions. Builder actions can declare
a new explicit semantic version for a revision; they cannot overwrite an old one. A historical registry prefix can be audited after new
registrations or revocations; a revoked skill cannot dispatch again.

A session pins a registry snapshot and SkillAgentVersion. Newly built skills do
not silently mutate that version. A settled, closed session can create one
idempotent successor, share its remaining owner budget, retain prior evidence and
pin the new registry version. Owner pause/cancel propagates through the lineage.
A crash between durable child creation and link event adopts the same child.

## P3 — Separate Builder and trusted validation

Research roles remain tools-disabled structured providers. The coding role emits
Python source, self-test and card; it never receives a shell. The harness creates
a detached, no-checkout Git worktree at the declared commit, writes only fixed
package paths, and runs generated Python behind an OS boundary. Before requesting
code it executes trusted sentinel probes for host reads, source writes and network.
If isolation cannot run, BUILD_SKILL returns UNAVAILABLE with no Builder call.

Candidate self-tests cannot alter or replace independent fixed fidelity cases.
Failures retain source, test results, receipts and costs. Passing candidates receive
a local retained Git ref and registry entry. The disposable checkout is removed;
private source and evidence remain. No candidate is automatically pushed publicly.

Current generated capabilities are standard-library implementations of the fixed
`paired-attribution-v1` protocol. General new research protocols and new trusted
tools require additional reviewed validators and capability negotiation. The
Builder does not change evaluator, runtime permissions, model weights or policy.

### Isolation scope

macOS uses `sandbox-exec` with deny-by-default file/network rules; Linux has a
bubblewrap backend which requires working unprivileged namespaces. Executable
presence is insufficient: the probe must succeed. There is no unsafe fallback.
Generated processes receive a clean environment and no credentials, source is
read-only, and writes are limited to private output. CPU, wall time, process,
file-descriptor and output limits apply. Linux also declares an address-space cap.
**macOS has no verified hard memory cap in this release.** Worktrees are not a
sandbox, and passing sentinels is not proof against every hostile program or OS
vulnerability. Unsupported or unavailable isolation yields explicit unavailability.

## CLI and future formal research entry

The supplied declaration and rows are artificial. Preflight is store-free and
makes no provider/backend calls:

```sh
jarvis workflow preflight examples/mechanism-research/declaration.yaml
jarvis workflow sandbox-check
jarvis --store .jarvis/mechanism-fixture workflow fixture
```

The complete fixture uses scripted providers and simulated token units, but really
executes isolated Python, independent fidelity checks and local candidate commits.
It exercises an unavailable diagnostic, construction, versioned adoption, a
separate Critic refuting the composition-only hypothesis, an existing reviewed
synthetic experiment, specialist consultation and a scoped final conclusion.

A future explicitly started study can use a private declaration:

```sh
jarvis --store .jarvis/new-study workflow start local/research-declaration.yaml
jarvis --store .jarvis/new-study workflow run OWNER_ID --max-steps 200 --wall-seconds 3600
jarvis --store .jarvis/new-study workflow report OWNER_ID
jarvis --store .jarvis/new-study workflow notebook OWNER_ID local/reports/new-study
jarvis --store .jarvis/new-study workflow replay OWNER_ID
jarvis --store .jarvis/new-study workflow successor OWNER_ID
```

`start` declares a session and installs validated builtins; it does not advance an
LLM or submit a backtest. `run` is foreground and bounded. Invocation exhaustion
returns RUNNER_LIMIT; resume the **same owner/store**. It does not create a daemon
or recurring automation. Live-source/dependency changes require a newly declared
lineage, not edits to a paused mission. Replay performs zero tools/LLM calls and
unobserved or unfinished branches return INSUFFICIENT_COVERAGE.

Before real Hermes work: declare the accepted baseline and frozen model/package,
export available private snapshots with honest key lineage and coverage, identify
missing gate/order/Cancel/position data, wire the existing admitted Iris program,
check fixed evaluator/PR/risk constraints and current quota evidence, then create
a new owner. The old paused mission is not rewritten or resumed by this release.

## Relation to Dream-RSI

This strengthens the research experience and capability substrate: better questions,
multiple explanations, diagnostics, negative findings, reusable verified methods
and time-ordered action histories. Existing Dream workflows and full-cost policy
comparisons remain intact; a declared child ResearchProgram can retain its selected
research policy. The new mechanism-action policy itself has not been proven better
by an independent Dream comparison. Discovery replay is an evidence screen, not
new execution or a counterfactual estimator. Generated skill fidelity/reuse, skill
utility, strategy gain and exploration-policy gain remain separate claims.

Acceptance commands and measured results are recorded in RELEASE-0.14.md. No real
Hermes research, retraining, order execution or paid provider work is part of this
engineering acceptance.
