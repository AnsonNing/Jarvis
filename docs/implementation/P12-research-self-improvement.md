# Reviewed multi-agent research and controlled self-improvement

This work implements the seven agreed stages around the existing trusted kernel.
Research Lab creates causal support packages for the existing ResearchProgram +
independent Critic path. It does not substitute agent-written scores for the
adapter/evaluator, introduce broker access, or resume cancelled Hermes work.

## Stages and contracts

| Stage | Implementation | Acceptance boundary |
| --- | --- | --- |
| 0: baseline | Immutable LabSpec, method/policy/provider identities, complete descendant budget | Paired task, assets, model/config and all budgets must match except the declared improvement axis |
| 1: team/board | Durable human/source board snapshots; Director-selected registered specialists; parallel independent opinions and bounded directed follow-up | Same initial evidence, scoped replies, durable role receipts, shared concurrency cap |
| 2: sources | Crossref paper metadata/abstract and GitHub public implementation metadata adapters; explicit public query and external-search permission | Actual read scope/version/license note preserved; no repository installation/execution or inferred paper effectiveness |
| 3: experience | Curator proposals plus actual experiment observations, counter-evidence, family/evaluator/template/origin scope | Interpretations remain provisional; incompatible, restricted or future experience refused; new task records actual retrieval and use |
| 4: method | Typed ResearchMethod controls role guidance and specialist templates; prospective candidate probes | Freeze finalist before heldout; full new fresh programs; actual continuation, canary and rollback |
| 5: capability | Agent proposes a closed grouped-mean diagnostic recipe with sources; metered fixed fidelity tests; agent selects the registered diagnostic | Fresh utility comparison and new-session use required; no arbitrary Python/C++ generation or interpreter permission changes |
| 6: expanded Dream | Closed policy chooses retrieve/search/consult/discuss/diagnose preparation actions | Fixed method/assets; ten distinct initial development/heldout histories and three development families; prospective independent validation |

## Causal execution

The task freezes source/human/experience references, method, policy, role configs,
adapter identities and resource envelopes. Optional board inputs are expanded
from a sealed event-prefix snapshot. New board feedback reaches a subsequently
created episode; it cannot retroactively rewrite an active episode's evidence.

Independent specialists run from the same root snapshot, with bounded parallel
jobs. That snapshot includes completed retrieval/search preparation available
at consultation time, while excluding another specialist's unfinished opinion.
They can ask a registered peer a focused question. Follow-up replies see
only completed preparation observations. Opinions retain disagreements and a
discriminating test. Neither consensus nor an agent confidence score establishes
a numerical improvement. A source's metadata, abstract or full-text scope is
explicit. Operator-imported sources and human suggestions retain provenance.

Preparation becomes one immutable ResearchSupport artifact. The existing
Director and Critic receive it and the declared method guidance. Their numeric
evidence citations remain actual result IDs; source/support evidence is separately
identified. Only approved experiments enter the existing trusted queue. Results,
negative findings and critiques are retained before experience is curated.

## Sources and capability boundary

External search defaults to disabled. An owner declares a public query and source
backend; no private strategy prompt or raw data is automatically sent to search.
Crossref returns metadata/available abstracts. GitHub returns repository metadata.
These adapters do not read arbitrary URLs, execute source instructions, clone a
repository, install dependencies or claim to have reviewed its implementation.
Full text can be supplied explicitly as a provenance-tagged SourceDocument.
Missing/full-paper/PDF/code-reproduction capabilities remain explicit limitations.

The initial capability compiler supports only `group_mean_argmax` over declared
development observations. It runs fixed positive, negative and tie cases through
a receipt-backed tool job. Its source motivates a recipe; source authority does
not prove utility. The immutable candidate remains EXPERIMENTAL. A validated
evolution profile provides the separate utility/transfer verdict and scope.
Registered-tool selection is a distinct bounded role call; unavailable tools
cannot be selected. New arbitrary-code execution still requires its own boundary.

## Prospective improvement and version adoption

LabEvolution changes exactly one axis: method, capability or preparation policy.
Development collection feeds a bounded proposal role. Candidate variants run
actual complete paired Lab episodes. One development finalist is frozen before
heldout. Fresh pairs run the full preparation + ResearchProgram/Critic path under
equal resource vectors. A quality gain or measured non-time-only cost dominance
is required; small local timer variation alone cannot establish efficiency.

After fresh acceptance a new research episode runs the selected version. The
changed-condition canary then either retains it or records regression/rollback.
Another episode actually runs the resulting version. These two new histories
feed a second improvement proposal, which stops for NEW_INDEPENDENT_VALIDATION_REQUIRED
instead of reusing exposed validation. Further tasks can explicitly pin the
completed profile; runtime, provider/config and protocol scope are checked.
Independently validated axes cannot be silently combined: a profile also checks
the other method/policy/capability settings used in its comparison, and combining
profiles requires separate joint validation.
Profiles never alter a running task, fixed evaluator, data splits, permissions,
global budgets or promotion gates. Model weights remain fixed.

## Replay, cost and recovery

Lab replay exposes only the exact recorded preparation prefix. Changed action
order, omitted observations or a new Director context return INSUFFICIENT_COVERAGE.
It does not estimate the outcome/cost of an unrecorded multi-agent conversation.
Method/routing improvements use actual prospective programs, not fictional replay.

Role calls, source queries, fidelity checks, diagnostic calls, program compilation,
baseline and backend work share immutable budget ancestry. Unknown work retains
its reservation. Tool failures have charged receipts and stop. Role limits count
role jobs, not unrelated metered tools. Known receipts can be adopted with
`resume-receipts`; unknown completion and owner stops cannot be cleared that way.
The foreground runner is bounded and resumes the same episode after RUNNER_LIMIT.
Explicit capability proposals also honor pause/cancel, runtime identity and
unresolved-work stops. Incomplete replay returns INSUFFICIENT_COVERAGE at its
first unrecorded action.
No command implicitly installs a background watcher or recurring automation.

Reports separate actual/pending vectors, application comparison costs and study
development/collection overhead. Remote compute/queue and money are unavailable
unless observed. Fixture token/time units are explicit simulations. Per-request
offline-audit memoization ends when that audit returns, so subsequent audits
recheck artifacts and detect later tampering.

## Commands

```sh
# Complete synthetic team -> experience -> method/capability/policy loop:
jarvis --store .jarvis/lab-acceptance lab fixture
jarvis --store .jarvis/lab-team lab team-fixture

# Explicit public, read-only metadata discovery; no LLM/backend research:
jarvis --store .jarvis/sources lab source-query 'public research question'
jarvis --store .jarvis/sources lab source-query 'public implementation topic' --provider github

jarvis --store .jarvis/private lab board-create 'research goal'
jarvis --store .jarvis/private lab board-add BOARD_ID local/human-idea.json
jarvis --store .jarvis/private lab start local/lab.json --board BOARD_ID
jarvis --store .jarvis/private lab run EPISODE_ID --max-steps 80 --wall-seconds 3600
jarvis --store .jarvis/private lab report EPISODE_ID
jarvis --store .jarvis/private lab resume-receipts EPISODE_ID
jarvis --store .jarvis/private lab evolve-start local/evolution.json
jarvis --store .jarvis/private lab evolve-advance STUDY_ID
jarvis --store .jarvis/private lab evolve-report STUDY_ID
jarvis --store .jarvis/private lab start local/next-task.json --profile STUDY_ID
```

These general commands use dependency-injected providers/adapters. CI fixtures
use declared providers and mathematical backends only. No real Hermes launch,
real-domain transfer claim, general autonomous skill acquisition, LLM fine-tuning
or statistically established monotonic improvement follows from fixture success.
Executed counts/outcomes and remaining acceptance limits belong in RELEASE-0.11.
