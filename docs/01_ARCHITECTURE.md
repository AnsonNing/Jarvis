# 01 — System Architecture / 系統架構

## A. Design pattern: trusted kernel + replaceable agents

Jarvis uses an **agentic research control plane**, a separate **trusted execution/evidence plane**, and an **improvement plane**. LLMs may reason, plan, criticize and propose artifacts; deterministic services must own job identity, tool admission, evidence visibility, budget enforcement, evaluation and promotion.

~~~text
USER → Campaign API / CLI
                │
                ▼
         Campaign Manager ────────────────▶ Policy Q: choose branch/action
           (durable FSM)                     ▲          │
                │                           │          ▼
         Research Director ──▶ Scientist / Planner ─▶ bounded proposals
                │                           │          │
                └─────▶ Frontier/Admission ◀─┘          │
                             │                           │
                             ▼                           │
                  Execution Queue / Scheduler ◀───────────┘
                      │                 │
                isolated worker    adapter registry
                      └─────────┬───────┘
                                ▼
                      deterministic results
                                │
                      Evaluator + Critic
                                │
                     Evidence / Event Ledger
                        │               │
                   Discovery Tree    Memory/Skills
                        │               │
                        ▼               ▼
                    History Pool    Next campaign
                        │
                        ▼
                Offline Dream-RSI
                     (Replay)
                        │
                     Q candidate
                        │
               independent validation
                        │
                 approved Q version
~~~

### Three distinct authorities

1. **Research authority:** proposes where to search; re-plans based on available evidence. Can request tools and code changes in scratch workspaces.
2. **Execution authority:** validates and runs admitted action through versioned adapters, snapshots code/config, meters all actual attempts.
3. **Evaluation/promotion authority:** reads immutable artifacts; applies frozen metrics/gates; prevents model/threshold/evaluator changes after formal reveal.

Never implement the evaluator as an untrusted natural-language agent. Critic explanations are not equivalent to numeric verdicts.

## B. Bounded-context agent roles

| Role | Inputs | Allowed outputs | Forbidden |
|---|---|---|---|
| Director | goal, budget, evidence summaries, scope, frontier | hypothesis-priority, plan/replan, stop/escalate | direct execution, edit scoring |
| Scientist | failure diagnostics, prior studies, papers, capabilities | falsifiable hypotheses, candidate rationale, information requests | invent metric results |
| Experiment Designer | approved hypothesis, tool schemas, baseline | trial matrix, isolated patch request, measurement plan | access locked tests |
| Builder | task/worktree, accepted API, tested source | code patch in sandbox, unit tests, builder artifact | unrestricted host access |
| Executor (service, not necessarily LLM) | admitted ExperimentSpec | immutable job receipts, result assets | substitute missing data |
| Evaluator (service) | recorded tool output, pinned metric contract | machine-verifiable verdict, confidence, metrics | change goal/threshold |
| Critic | diagnostics and evaluation (development scope) | evidence-linked critique, next questions | overwrite numerical verdict |
| Curator | finalized evidence, validation scope | memory proposals, skill manifests, contradictions | promote without tests |
| RSI Policy Developer | summarized development history, Q incumbent | bounded Q candidates | view policy-heldout outcomes |
| Replay Harness (service) | frozen worlds, Q candidate | deterministic replay coverage, score/cost | model/training/LLM calls |

"Roles" are **logical capabilities**, not one LLM instance each. One LLM may fulfill Director/Scientist/Designer sequentially with distinct permission contexts. Separate model processes only if justified by measured benefit.

## C. Core packages (proposed)

~~~text
jarvis/
  cli.py                         # campaign / inspect / resume / report / replay / rsi
  api/
    models.py                     # versioned typed schemas
    interfaces.py                 # Protocols, tool/result contracts
  core/
    campaign.py                   # coordinator FSM
    world.py                      # immutable declared world
    state.py                      # research state snapshots
    frontier.py                   # candidate actions & admission
    scheduling.py                 # action proposals -> durable jobs
    budget.py                     # reserve/charge/reconcile
  agents/
    router.py                     # model/provider abstraction
    roles.py                      # prompts + structured outputs + evidence filter
    decisions.py                  # response validation; citations
  execution/
    job_store.py                  # state machine and idempotency
    workers.py                    # sandbox tool workers
    adapters.py                   # registered adapters (including synthetic)
    artifacts.py                  # content-addressed blobs/manifests
  evaluation/
    evaluator.py                  # immutable numeric scoring
    comparison.py                 # controlled selection & statistical reports
  history/
    events.py                     # append-only journal
    discovery_tree.py             # snapshot/action/result DAG view
    history_pool.py               # cross-world indexing and constraints
  memory/
    evidence.py                   # typed claims/failures
    retrieval.py                  # applicability + provenance filters
    capabilities.py               # skills, validation and versions
  rsi/
    policy_dsl.py                 # restricted policy specification
    replay.py                     # exact historical replay
    dreaming.py                   # offline policy development
    promotion.py                  # heldout evaluation/rollout
  domains/
    synthetic/                    # first complete, deterministic research world
    trading/                      # future adapter declarations, no real logic yet
  reporting/
    report.py                     # human + machine-readable reports
  tests/
    unit/ integration/ e2e/ regression/
docs/
  implementation/                # verification receipts produced by Codex
examples/
  research_task.example.yaml
~~~

The tree is a target, not an instruction to create empty placeholder modules. Add directories when packages become functional.

## D. Control flow

1. CLI submits versioned ResearchTask and references a registered World.
2. Trusted Campaign Manager normalizes, validates, freezes core fields and creates a campaign ID.
3. Director sees only entitled development evidence and proposes a plan.
4. Frontier builder produces candidate action descriptors with cost/risk estimates and preconditions (estimates are not results).
5. Q (initially deterministic Q0) chooses one or more admitted branches, constrained by budget.
6. Agent designs an ExperimentSpec; trusted admission checks tool permissions, eligible split, immutable World and resource reservation.
7. Queue executes jobs in isolated worktrees. Workers emit versioned observations/results, raw errors and usage receipts.
8. Evaluator computes declared primary/secondary metrics; Critic annotates failure and possible next directions.
9. Events append to immutable log; derived Discovery Tree and report views can be rebuilt.
10. Continue/branch/stop based on actual new evidence, while preserving discarded branches and failed attempts.
11. Final formal stage freezes candidate, uses reserved independent evaluation evidence, then seals report and capability proposals.
12. Once enough worlds exist, an isolated Dream-RSI process may generate/replay/promote versions of Q for **subsequent** campaigns.

## E. Identity and immutable boundaries

Campaign identity binds: task hash, World ID, engine+schema version, initial Q ID, declared budgets, approved datasets and evaluator/split IDs. For a scientific change to any of those fields, create a new Campaign/World instead of editing an existing run. Candidate worktree versions and validation receipts are child identities.

A **decision-time evidence snapshot** must be stored by ID/hash; without this, later replay could see sibling results unavailable during original choice.

Hash/canonicalization is an integrity check, not proof against a compromised machine. Git commit ID plus artifact SHA-256 and captured runtime metadata is adequate initially; add stronger isolation only if a concrete threat model requires it.

## F. Scheduling and parallel branches

Research branches form a dependency DAG; a discovery node has exactly one primary parent for faithful tree replay, and optional additional referenced inputs/dependencies. A parallel batch can start from one frozen parent, each branch with isolated resource reservation; completion order is recorded, and Q can only react to observed completions.

Parallel job selection must not be silently scored as sequential wall time. Track total compute, actual elapsed makespan and queue delay separately. An interrupted/unknown branch cannot count as an evaluated result.

## G. Governance: lightweight but necessary

Avoid copying obsolete heavyweight approval/pin architecture from earlier experiments. Essential non-negotiable boundaries are: declared World, read permissions, immutable evaluation, audited action events, resource budget, isolation and kill switch. Other review gates require explicit benefit.

## H. Implementation choices (provisional)

- Python 3.12, typed dataclasses/Pydantic, pytest; SQLite WAL for single-host metadata/event registry, immutable JSONL mirrors and content-addressed artifacts.
- Local subprocess/worktree execution for MVP; external worker queue only after actual scale requires it.
- Thin provider-independent LLM interface; external local Codex is an implementation agent, not an assumed API dependency inside Jarvis.
- No mandatory LangGraph/AutoGen/CrewAI, vector database or web UI. Benchmark any added framework against a minimal deterministic state machine first.
- Do not expose arbitrary generated Python as a Dream-RSI runtime policy initially; compile a limited policy DSL.

## I. Extension points

Any new research domain implements DomainAdapter, ToolAdapter, Evaluator, SplitProvider, ArtifactProvider and capability mapping. Domain adapters declare units, availability, cost, determinism and whether a tool result is observation, estimate or simulation. Jarvis core never assumes BTC, LONG/SHORT, prediction target, exchange, latency model or financial PnL semantics.
