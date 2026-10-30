# Jarvis

[Bounded native runtime and automatic delivery](docs/implementation/P21-bounded-native-runtime.md)
add a declared C++ compile/run tool, bounded role contexts, finite receipt-based
recovery and provider-free evidence packages on runner exits. New CLI sessions use
the runtime envelope; historical session identities remain unchanged. The shipped
runtime fixture uses only scripted roles and synthetic inputs.

[Collaborative research planning](docs/implementation/P20-collaborative-research-planning.md)
adds a small direction comparison at important decisions, separate branch/task stop
reviews and resumable owner questions. No fixed search breadth or routine approvals
are required; existing research records remain unchanged.

[Verified analysis delivery and staged conclusions](docs/implementation/P19-trusted-research-delivery.md)
add opt-in clean-rerun delivery gates, bounded trusted Critic evidence, explicit
screening/selection/confirmation periods and frozen-candidate confirmation. Existing
session records retain their original contracts and reporting rules.

**Jarvis — Autonomous, Evidence-Driven, Self-Improving Research Agent System**

Jarvis is an executable, domain-extensible research platform. **Version 0.15 adds autonomous research sessions:** give Jarvis a baseline, a goal, a budget and what may change, and a Director chooses among reading source, agentic analysis in an OS-isolated workbench, explicit backtest matrices, model retraining, code changes to strategy rules or the training pipeline, and multi-objective comparison. An independent Critic reviews each result, the owner can advise at any time (every item must be answered), a stop guard prevents premature stops, and the session ends with a reproducible research deliverable. Dream-RSI now improves the action-selection policy itself, with paired fresh validation on a hidden holdout, adoption, canary and rollback. See the [autonomous research guide](docs/implementation/P16-autonomous-research.md); it is verified end to end on a synthetic Hermes-like world and starts no real Hermes research. Sessions now accumulate: every ended session is written to a per-domain knowledge base (trusted facts, evidence-linked lessons, versioned skills promoted from reproducible analyses), new sessions start from that memory with skills mounted in their workbench, and Dream-RSI can learn from those real histories and adopt into the domain policy registry used by every later session ([P18](docs/implementation/P18-research-memory.md)).

```sh
jarvis autonomy sandbox-check
jarvis --store .jarvis/auto-fixture autonomy fixture --policy explain --output local/auto-fixture
jarvis --store .jarvis/auto-dream autonomy dream-fixture
```

Version 0.14 added mechanism research: a complete owner contract, competing hypotheses, typed inspection/diagnosis/consultation/experiment/build/stop actions, independent Critic review, reusable diagnostic skills and a separate isolated Skill Builder. The Director can seek evidence before submitting another backtest. Newly validated skills require a versioned successor session; fidelity does not imply research utility. The [mechanism research guide](docs/implementation/P15-mechanism-research.md) explains the pipeline, data requirements and boundaries. This release starts no real Hermes research or background automation.

Version 0.13 introduced selected-package baseline admission, side-isolated entry-trigger controls, explicit PR/drawdown feasibility and a shared-budget session joining specialist teams, independent Critic review and sequential integration/development stages. On-demand public source discovery, pinned human-input boards, scoped experience reuse and controlled research-method/capability/policy evolution remain available. Shared full budgets, prospective validation, actual adoption, canary rollback and new-history feedback remain mandatory. Give the system a strategy baseline, goal and research directions: it admits bounded experiments, executes the supplied backend, analyzes entry-only PL and risks, revises direction and seals replayable histories. Domain details stay in adapters.

Choose **deterministic Q0** for reproducible fixtures or **`--agent codex`** for real LLM hypothesis generation, experimental design, result analysis and revision. The researcher proposes one bounded intervention; the trusted adapter/evaluator computes results. Research Lab verifies scoped reuse and a closed numerical diagnostic capability on artificial tasks. Generated Python is admitted only for the fixed paired-attribution protocol behind an available OS sandbox and independent fidelity tests. General capability acquisition, new trusted protocols, model-weight updates and real-domain policy transfer remain future work. Hermes comparisons use existing exposed data and frozen models; they do not establish independently validated trading improvement.

The [Iris research guide](docs/implementation/P6-iris-and-hermes-dream.md) describes
model acceptance, baseline reuse, submission budgets, multi-round research and
policy validation. Start without external Iris access:

```sh
jarvis --store .jarvis/strategy-fixture research fixture
jarvis --store .jarvis/hermes-dream-fixture hermes-dream fixture
# Independent proposal/result critique on an artificial objective:
jarvis --store .jarvis/critic-fixture research critic-fixture
# Complete policy feedback, full-cost comparison, continuation and rollback:
jarvis --store .jarvis/reviewed-dream-fixture research dream-loop-fixture
# Team, human/source inputs, experience and three prospective improvement axes:
jarvis --store .jarvis/lab-fixture lab fixture
# Sequential integration/development, shared quota, retrospective proposals and notebook:
jarvis --store .jarvis/session-fixture lab session-fixture
# Store-free mechanism-research declaration checks:
jarvis workflow preflight examples/mechanism-research/declaration.yaml
# Actual isolated skill construction and successor use, with scripted providers:
jarvis workflow sandbox-check
jarvis --store .jarvis/mechanism-fixture workflow fixture
# Real LLM research on artificial objectives; still zero remote Iris submissions:
jarvis --store .jarvis/strategy-llm-fixture research fixture --agent codex
jarvis --store .jarvis/policy-llm-fixture hermes-dream fixture --agent codex
jarvis --store .jarvis/critic-llm-fixture research critic-fixture --agent codex
```

Artificial outcomes always carry `SIMULATED_FIXTURE`. The deliberate shifted
canary exercises rollback. A real Iris study needs a completed accepted model
package and explicit owner authorization, with bounded programs and quota accounting. August/September results
already seen during model development remain retrospective research data.

Standing owner authorization can enable [continuous Iris research](docs/implementation/P7-continuous-iris-research.md):
short programs automatically continue using recorded results and a shared quota
ledger, with no fixed eight-run lifetime cap. `iris continuous-watch` waits for
full model acceptance and persists the study before any new dispatch. Missing or
stale quota evidence, unknown operations and recorded research stops are retained.

[Strategy-design research](docs/implementation/P8-strategy-design.md) supports
owner-bound entry/cancellation conditions, session clocks and position limits.
Agents propose a closed rule DSL; trusted private profiles render and compile it.
An isolated successor watcher preserves an active earlier runtime and waits for
known, settled work before taking over. Start with the compiled artificial fixture:

```sh
jarvis --store .jarvis/design-fixture research design-fixture
```

[Research readiness](docs/implementation/P9-research-readiness.md) can inventory
private models, baselines and controls without creating a study or calling a
provider/backend. An explicit owner-bound development-use record retains known
findings on exposed data; it grants no execution permission or independent-test
claim. Cancelled or attention states do not restart automatically.

[Independent Critic](docs/implementation/P10-independent-critic.md) reviews new
prepared programs in two stages. A proposal needing revision cannot execute;
result critiques and negative evidence inform the next Director. Old saved
programs retain their original role contract. Adding a Critic does not establish
an efficiency gain or activate real research.

[Reviewed Dream feedback](docs/implementation/P11-reviewed-dream-loop.md) runs a
selected policy in subsequent reviewed research programs. Exact catalog replay
is a screen; changed Director/Critic paths execute in new paired programs under
the same complete budget. The artificial acceptance includes policy adoption in
continued research, measured rollback and a second history-driven Dream cycle.
Its scripted providers use declared simulation units; real research remains an
explicitly supplied and authorized scope.

[Multi-agent research and self-improvement](docs/implementation/P12-research-self-improvement.md)
adds a Director-selected specialist team with independent initial contexts and
directed follow-up, a human/source board whose snapshot is pinned per episode,
and a Curator whose interpretations retain actual observations and counter-evidence.
Method candidates can change bounded role guidance; capability candidates compile
only a closed diagnostic recipe; extended Dream candidates choose preparation
actions. Each axis uses separate independent validation cohorts and full new
programs. Completed profiles are checked before adoption by subsequent tasks.

External source discovery is read-only and explicitly enabled. Crossref exposes
metadata/available abstracts; GitHub exposes repository metadata. No discovered
repository is installed or executed. All fixture sources and token units are
clearly declared engineering controls. Cancelled Hermes workflows remain stopped.

[Selected entry research sessions](docs/implementation/P13-entry-research-sessions.md)
pin owner-selected model packages and original baseline receipts, including explicit
calendar subsets evaluated locally from the original raw ledger. Every declared LLM
role can pin its model and reasoning effort. Integration stages must demonstrate
an observed intervention effect before development continues. A private notebook
exports actual contexts, structured answers, cowork decisions, searches, costs,
experiments and retrospective proposals. Development-only method/policy proposals
and diagnostic fidelity do not establish independent Dream or capability utility.

[Research recovery and sustained missions](docs/implementation/P14-research-recovery.md)
adds exact-reference schemas, receipted correction of known invalid answers,
explicit recovery of completed histories, trusted execution evidence for Critic,
and a task controller that continues useful reviewed sessions. Atomic checkpoints
distinguish child success, task completion, runner limits and failures. Unknown
operations remain unretried, and all original negative evidence is retained.

## Run locally

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
pytest -q
jarvis task validate examples/research_task.example.yaml
jarvis campaign start examples/research_task.example.yaml --max-steps 2
# Use the actual campaign ID printed above:
jarvis campaign resume <campaign-id>
jarvis campaign report <campaign-id>
jarvis history verify <campaign-id>
jarvis rsi replay <campaign-id>
```

The campaign records negative experiments, changes direction based on observed
results, freezes its candidate, runs a separate synthetic locked evaluation and
produces evidence-linked reports. Receipts survive an abrupt coordinator exit;
uncertain started jobs require reconciliation. The default private store is
`.jarvis/`; use `jarvis --store <private-directory> ...` to choose another.

## Use existing Hermes data

Set `HERMES_ROOT` and `HERMES_PYTHON` privately to the provided repository and its
Python environment. Raw data, models, local paths and numerical reports stay local.

```sh
jarvis hermes inspect --root "$HERMES_ROOT" --side long
jarvis hermes start --root "$HERMES_ROOT" --side short
# If saved order decisions do not match the current model metadata, materialize
# frozen-model predictions into a NEW private folder, then use it explicitly:
jarvis hermes materialize --root "$HERMES_ROOT" --side long \
  --python "$HERMES_PYTHON" --output .jarvis/materialized-long
jarvis hermes start --root "$HERMES_ROOT" --side long \
  --ledger-root .jarvis/materialized-long
```

The default reader never opens model pickles. The explicit materialization worker
uses trusted local source/models, disables model fitting, redirects loader writes,
verifies checksums and reproduces the declared baseline. It preserves upstream
files. Existing exposed evaluation data is tagged development/replay; formal
strategy validation reports NOT_RUN. See the
[integration receipt](docs/implementation/HERMES-local-integration.md).

## Run the real LLM researcher

For the new `cotrain_controls_v1` frozen baseline, explicitly select its artifacts.
After materialization, two or three bounded development rounds can share earlier
verified comparisons. Each later campaign must reproduce the baseline and execute
`prior_round_review` before proposing new comparisons. Reviews carry sealed report
and receipt references; the study verifier rebuilds their causal lineage offline.

```sh
jarvis hermes materialize --format new --root "$HERMES_ROOT" --side long \
  --artifacts "$NEW_LONG_ARTIFACTS" --python "$HERMES_PYTHON" \
  --output .jarvis/new-baseline/long
jarvis hermes study --root "$HERMES_ROOT" --side long \
  --artifacts "$NEW_LONG_ARTIFACTS" --ledger-root .jarvis/new-baseline/long \
  --agent codex --rounds 2 --max-llm-calls 8 --llm-token-budget 250000
jarvis hermes study-resume <study-id>
jarvis hermes study-verify <study-id>
jarvis hermes study-report <study-id>
# After an implementation fix, an explicit source upgrade is allowed only when
# every already-started child is CLOSED with known receipts and settled costs:
jarvis hermes study-upgrade <study-id>
```

The new adapter supports cancellation risk deltas, minimum prefill order age and
bounded all/any rules over model risk and order age, with one contribution per
episode. These comparisons assume immediate successful cancellation on the
historically filled population. Price-based rules and acknowledgement races need
additional data. Stop/target simulation accepts a separate typed quote/fill
contract and records hypothetical execution under explicit latency/slippage/fee
assumptions; it cannot derive a price path from saved PL or exit prices.

```sh
jarvis hermes exit-replay --input "$EXIT_INPUT_JSON" --settings "$EXIT_SETTINGS_JSON"
jarvis hermes exit-resume <job-id>
jarvis hermes exit-verify <job-id>
jarvis schema .jarvis/schemas
```

See [execution data contract](docs/adapters/exit-and-cancel-data.md) for required
fields and [new-baseline admission](docs/implementation/P5-new-baseline-admission.md)
for the verified fidelity checks.

Use the locally installed Codex CLI with its existing login. Keep executable paths,
credentials and run outputs private. The default model is selected by the installed
CLI; pass `--model` to pin an explicit supported model. Jarvis records the executable
version/hash and declared model option. The observed exec JSON reports token usage
but does not identify the resolved default model, so Jarvis does not invent it.

```sh
jarvis hermes start --root "$HERMES_ROOT" --side long \
  --ledger-root .jarvis/materialized-long --agent codex \
  --max-llm-calls 8 --llm-token-budget 200000 \
  --goal 'Diagnose the current policy, propose falsifiable hypotheses, run controlled comparisons, and revise after each observed result.'
# Optional: --codex-executable "$JARVIS_CODEX_CLI" --model <supported-model>
jarvis campaign report <campaign-id>
jarvis history verify <campaign-id>
jarvis rsi replay <campaign-id>
```

The LLM must first inspect and reproduce the baseline. It then chooses diagnostics
or designs a frozen-model comparison, with a one-variable threshold/cancel change,
day/product grouping, or declared extra-cost sensitivity. Every hypothesis has a
mechanism, falsification rule, controls, expected observation and criterion. Later
turns receive the actual measured results and prior proposals; the model chooses
continue, branch, stop or request input. Identical repeated experiments are denied.

Calls are reserved before dispatch and settled with observed input/output tokens.
Rejected responses remain charged; at most one schema/admission repair is allowed.
Unknown completion or missing usage stops for reconciliation and retains a pending
reservation. Reports distinguish measured consumption from pending reservations.
Per-call token reservations are estimates: the CLI has no verified hard server-side
output cap, so observed overruns are retained and stop further dispatch. Provider
timeouts and maximum call counts bound the run.

Codex runs ephemerally with user configuration ignored, read-only permissions,
and shell/plugins/apps/memory/multi-agent/hooks/web disabled. Provider prompts use
bounded aggregate development evidence, with raw inputs/models retained locally.
Provider tool attempts are rejected; the LLM cannot execute a proposed command or
edit the fixed evaluator. CI uses fixtures and no saved account authentication.

Default replay of an LLM campaign follows recorded decisions/designs and charges
their historical costs with zero new LLM/tool calls. Alternative policies cannot
claim an unrecorded parameter design just because its tool ID matches.

Implementation status and verified limits: [PROGRESS](docs/implementation/PROGRESS.md)
and [release acceptance](docs/implementation/RELEASE-0.8.md). The documents below
remain the broader P0–P5 design, not a claim that every planned capability exists.

## Run the complete synthetic Dream-RSI loop

```sh
# Three recorded rounds plus an unseen condition-shift canary. No LLM required.
jarvis --store .jarvis/dream-fixture rsi study
# Real policy developer; one bounded, paid request per round, existing CLI login.
jarvis --store .jarvis/dream-codex rsi study --developer codex
# Optional: --codex-executable "$JARVIS_CODEX_CLI" --model <supported-model>
jarvis --store .jarvis/dream-codex rsi report <run-id>
jarvis --store .jarvis/dream-codex rsi verify <run-id>
jarvis --store .jarvis/dream-codex rsi resume <run-id>

# Individual work packages and exact coverage inspection:
jarvis --store .jarvis/dream-manual rsi collect
jarvis --store .jarvis/dream-manual rsi coverage --pool <pool-ref>
jarvis --store .jarvis/dream-manual rsi dream --pool <pool-ref>
jarvis --store .jarvis/dream-manual rsi replay-tree --tree <tree-ref> --policy <policy.yaml>
```

The default study first preserves a coverage failure, then executes new collections
to fill missing branches, selects a single development finalist and validates it
on disjoint families and new online paired campaigns. A third round checks for
further development gains. An unseen spurious-proxy canary can regress and roll
back the policy that passed earlier gates. A study may legitimately retain Q0.

The full pool has twelve realized trees from six mathematical families. Fresh
families are separately declared before proposals. Each branch receives only its
ancestors, pays its recorded cost before reveal, and has an immutable receipt.
Unknown branches receive no score. Candidate generation sees development-only
observations; replay performs zero domain/provider/evaluator calls. Heldout and
fresh families are one-shot across the local store, including renamed pools.
Additional independent families are needed for further promotion after exposure.

Policy origin distinguishes declared fixture enumeration from real LLM proposals.
Both arms use the same tools and empty L2 memory. Reports separate original history
collection, historical replay costs, actual developer tokens, pending reservations,
fresh execution, target attainment and rollback. This is synthetic engineering
acceptance; it does not demonstrate general or financial research improvement.

## Explore domain settings

```sh
jarvis rsi space
jarvis rsi space --spec examples/hermes_search_space.example.yaml
```

The finite design space supports take-profit/stop-loss basis points, Trading
cutoff deltas, cancellation mode and bounded cancellation conditions, as either
single-axis or factorial comparisons. Example values are declared fixtures.
Current Hermes replay supports its existing one-variable threshold/cancel tools.
Price-path exits and new cancellation logic require explicit backend capabilities;
their candidates return REQUIRES_CAPABILITY and NOT_RUN until those are supplied.
See [experiment-space contract](docs/15_EXPERIMENT_SPACE.md).

## Mission / 願景

Given a research question, Jarvis should autonomously decompose the problem, diagnose the baseline, generate hypotheses, plan and execute bounded experiments, analyze evidence, revise research direction, and produce reproducible results. Across campaigns it accumulates reusable knowledge, methods, code capabilities, and failure lessons. Given enough well-recorded discovery histories, Dream-RSI improves **how Jarvis researches**, not necessarily the weights of any trading model.

Three separate improvement loops:

1. **Strategy improvement** — improve a domain artifact (e.g., a model, threshold, execution decision).
2. **Capability improvement** — validate and reuse new tools, methods, and research skills.
3. **Meta-research / Dream-RSI** — learn more efficient exploration, branch allocation, and stopping policies from discovery trees.

## System boundary

**Inside Jarvis:** research orchestration, agent decision protocols, structured evidence, capability registry, policy-driven experiment selection, history/replay, policy evaluation, resource accounting, and reporting.

**Outside Jarvis:** proprietary market data and strategy source, training pipelines, event-backtest engines, broker/exchange connections and trading deployment. The current adapter reads declared local artifacts and optionally invokes trusted frozen-model inference. Data, weights and private source remain in their original local infrastructure.

No live orders or access to production credentials is permitted. Generated research code execution is disabled until a real isolation boundary is implemented.

## Read first

| Document | Purpose |
|---|---|
| [AGENTS.md](AGENTS.md) | Start here when implementing with local Codex |
| [00 — Master plan](docs/00_MASTER_PLAN.md) | Goals, boundaries, success criteria and non-goals |
| [01 — Architecture](docs/01_ARCHITECTURE.md) | Layers, components, decisions and repository layout |
| [02 — Research lifecycle](docs/02_RESEARCH_LIFECYCLE.md) | Durable state machine, branching, lifecycle and failures |
| [03 — Agents](docs/03_AGENT_ROLES.md) | Roles, collaboration, tool permissions and prompting |
| [04 — Contracts](docs/04_CONTRACTS.md) | Required schema definitions and I/O protocols |
| [05 — Dream-RSI](docs/05_DREAM_RSI.md) | Discovery world, exact replay, policy improvement and promotion |
| [06 — Memory/capabilities](docs/06_MEMORY_CAPABILITIES.md) | Persistent memory, retrieval, skill internalization and transfer |
| [07 — Evaluation](docs/07_EVALUATION.md) | Strategy-level and research-policy-level evaluation |
| [08 — Runtime/security](docs/08_RUNTIME_SECURITY.md) | Job queues, resumability, safety, budgets and isolation |
| [09 — Roadmap](docs/09_ROADMAP.md) | Phased work packages, dependencies and acceptance |
| [10 — Tests](docs/10_TEST_PLAN.md) | Deterministic contracts, regression and end-to-end fixtures |
| [11 — Codex implementation](docs/11_CODEX_EXECUTION.md) | Exact sequence of local implementation steps |
| [12 — References/decisions](docs/12_REFERENCES_DECISIONS.md) | Source mapping, architectural decisions and limits |
| [13 — Adapter boundary](docs/13_ADAPTER_BOUNDARY.md) | Future training/backtest integration contract |
| [14 — Research techniques](docs/14_RESEARCH_TECHNIQUES.md) | VOI, tree search, cost-aware experimentation, meta-learning alternatives |
| [中文導讀](docs/zh/README.md) | Jarvis 完整規劃的中文架構導讀 |
| [Research task example](examples/research_task.example.yaml) | Domain-neutral synthetic example |

## Development policy

- Plan-first, test-first, small work packages. Do not claim unimplemented modules are operational.
- Every decision/action/result has stable IDs and explicit evidence visibility.
- Replay of an unexecuted branch must return UNKNOWN; it must never fabricate a result.
- Evaluator, locked-test cohorts, and budgets are frozen and separated from research agents.
- Memory must carry evidence, applicability, lineage and negative results; a vector search hit is not a scientific result.
- Improve policies using independent **research tasks**; a higher historical replay score alone is not proof of transfer.
- Never upload proprietary strategy code, confidential datasets, model weights, credentials or machine-specific paths to this public repository.

## Existing references

- [Dream-RSI project](https://dream-rsi.com/) · [paper](https://arxiv.org/abs/2609.14858)
- [Existing RSI research-agent project](https://github.com/AnsonNing/RSI) (may require private access)
- [Existing EEHEMT policy/replay project](https://github.com/AnsonNing/EEHEMT) (may require private access)

Documents are design targets, not evidence that Jarvis already supports a feature.
