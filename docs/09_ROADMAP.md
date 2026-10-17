# 09 — Implementation Roadmap / 可交由 Codex 執行的工作包

## Overall ordering

The first objective is a **real, resilient agent research loop** using a deterministic synthetic domain. Design all records from day one to support Dream-RSI replay, but defer expensive meta-policy learning until trustworthy histories exist.

~~~text
P0 Typed Kernel ──▶ P1 Autonomous Campaign ──▶ P2 Persistent Capability
      │                        │                        │
      └──── replay-ready event records ─────────────────┘
                                                       │
                                              P3 Cross-task Corpus
                                                       │
                                              P4 Dream-RSI
                                                       │
                                              P5 Extended Integration
~~~

Each work package below requires: working code, tests, evidence receipt in docs/implementation/, updated status and a small git commit. A checkmark is earned only when locally verified. Missing real trading adapters are not blockers for P0–P4 synthetic proof-of-concept.

## P0 — Trusted foundation and synthetic proof (top priority)

| ID | Deliverable | Acceptance |
|---|---|---|
| P0.1 | Python project, CLI, test config, typed schemas and version registry | clean install, import and strict schema roundtrip tests |
| P0.2 | Canonical hash / artifact store / event journal | tampering detected, append sequence + readback parity, two-process append tests |
| P0.3 | Immutable World/Task/Campaign contract | edit after seal refused, new revision gets new identity |
| P0.4 | ToolRegistry + capability negotiation + Action admission | invalid action/tool/permission/split/budget refused with reason |
| P0.5 | Budget reservation/charge and job lifecycle | parallel oversubscription prevented, retries counted |
| P0.6 | Deterministic synthetic research domain | baseline, two competing hypotheses, one negative and one positive candidate possible, locked test present |
| P0.7 | Fixed Evaluator + scope/status distinction | result authenticity checked, no LLM writes numeric verdict |
| P0.8 | Discovery Tree v1 materialized from events | ancestry, visible evidence and cost reconstruct exactly; no hindsight |
| P0.9 | Minimal Q0 policy with deterministic tie-break | known fixture decisions reproducible without LLM |

P0 complete when all tests in docs/10_TEST_PLAN.md P0 suite pass; synthetic fixture can be executed via CLI to produce a valid result and Discovery Tree.

## P1 — Autonomous Research Loop

| ID | Deliverable | Acceptance |
|---|---|---|
| P1.1 | Provider-neutral LLM role interface + mock provider | structured responses validated; unknown/stale evidence rejected |
| P1.2 | Research Director + Scientist + Designer | falsifiable hypothesis objects; competing plans with explicit reasons |
| P1.3 | Candidate worktree isolation / sandbox tool runner | versioned patches without touching frozen evaluator or main workspace |
| P1.4 | Evaluation + Critic / failure diagnosis | numerical verdict separate from evidence-linked prose |
| P1.5 | Durable Campaign FSM and resume/stop | injected crash/restart yields same spend, no duplicate tool calls |
| P1.6 | Multi-step adaptive research cycle | first result changes second experiment choice; branches and negative results retained |
| P1.7 | Scoped final reporting | user-friendly report + machine-readable evidence index; no misleading success |
| P1.8 | Bound time/token/GPU and external source reading | hit each budget leads to honest terminal/partial status |

P1 complete when an unattended synthetic campaign runs to closure, includes at least two data-dependent research decisions, can be resumed after injected interruption and outputs verifiable report. Use mock agent for CI and optional real LLM only as supplemental test.

## P2 — Persistent Memory and Capability RSI

| ID | Deliverable | Acceptance |
|---|---|---|
| P2.1 | Knowledge claim and contradiction registry | evidence/negative findings retrievable with scope |
| P2.2 | Structured retrieval (optional semantic rank) | incompatible/unvalidated/private findings filtered before prompts |
| P2.3 | Capability manifest, typed signature and tests | capability invocation only if preconditions/permission/validation pass |
| P2.4 | Gap detector and NEEDS_INPUT/REQUIRES_CAPABILITY routing | distinguish absent data, unsupported adapter, genuine model failure |
| P2.5 | Source-to-capability internalization | source/version/license, fidelity test, scoped domain comparison and asset record |
| P2.6 | Fresh-session reuse and anti-regression | new session can select skill without direct hint; negative-control task resists misuse |
| P2.7 | Baseline arm A vs memory arm B | matched budget, independent test family, cost and valid-quality comparison |

P2 complete when at least one general synthetic diagnostic capability is implemented, independently tested, used in a new task and its actual benefit or no-benefit is reported without claiming generality.

## P3 — Rich corpus and cross-campaign transfer

| ID | Deliverable | Acceptance |
|---|---|---|
| P3.1 | Multi-world task suite | multiple genuinely independent synthetic problem families and varied difficulty |
| P3.2 | Research campaigns with real sibling branching | executable branches share immutable parent and have separate actual cost |
| P3.3 | History Pool index, identity and compatibility | cross-family split overlap rejected, hashes revalidated |
| P3.4 | Time-consistent decision reconstruction | future sibling / evaluator leak simulation detected |
| P3.5 | Transfer A vs B under matched cost | independent tasks and confidence report; memory not credited for generic Q effects |
| P3.6 | Coverage dashboard (CLI report) | eligible recorded vs unknown action decisions, budget and origin visible |

P3 entry to P4: initial target >=10 complete replay-valid trees across >=3 independent task families **and** useful sibling action coverage on declared Q action space. This is a working target to be reviewed *before* inspecting meta-policy outcomes, not a promise that any particular number is statistically sufficient.

## P4 — Dream-RSI research-policy optimization

| ID | Deliverable | Acceptance |
|---|---|---|
| P4.1 | Restricted policy DSL and compiler | no code injection/import/eval and no evaluator edits |
| P4.2 | Deterministic exact replay | offline zero external tools; unrecorded action = UNKNOWN |
| P4.3 | Development / policy-heldout History Pools | independent family isolation, no candidate access to heldout |
| P4.4 | Policy Developer generation | 2–5 candidates + immutable Q0 + rationale/evidence |
| P4.5 | Candidate replay, costs and coverage scoring | bounded matched budgets and actual per-world evidence |
| P4.6 | One-finalist frozen selection | winner chosen development-only before policy-heldout reveal |
| P4.7 | Fresh-campaign policy evaluation | Q0 vs Q1 same base agents/tools/memory, matched resource budgets |
| P4.8 | Promotion/version/rollback | no transfer evidence → no "improved" claim; safety regression preserves Q0 |

P4 complete only when at least one policy round including negative/no-promotion case executes correctly, with independent validation and a credible fresh-campaign comparison. The milestone can legitimately conclude Q0 remains best.

## P5 — Actual research backends, scale and optional specialization

| ID | Deliverable | Acceptance |
|---|---|---|
| P5.1 | Actual training/backtest adapter discovery | truthful schemas, capabilities, fidelity, private data rules |
| P5.2 | Read-only real strategy inspection | real data remains local/private; schema and time-index validated |
| P5.3 | Bounded end-to-end strategy research | one real campaign with frozen evaluation and observed backtester outcomes |
| P5.4 | Memory transfer across distinct strategy tasks | A/B/C matched budgets on fresh strategy families |
| P5.5 | Parallel remote workers, better scheduler, optional UI | only when required and measured; resume and evidence parity preserved |

P5 is blocked on owner-supplied adapter details and privacy/compute decisions. Never use unverified mock market results as proof of P5 success.

## Milestone reporting

For each work package record:
- exact code commit and run ID;
- implemented features and explicitly missing features;
- tests executed + counts + operating environment (not guesses);
- evidence artifact IDs and tool/runtime cost;
- deviations and approved ADR;
- regression and blockers;
- next package and acceptance gap.

Status vocabulary: NOT_STARTED, IN_PROGRESS, BLOCKED, VERIFIED, VERIFIED_WITH_LIMITATIONS. A file existing is not VERIFIED.

## Priority adjustments

If the LLM provider is unavailable, mock scripted agents must complete P0–P1 while reporting provider unavailable. If job queue is unavailable, local durable runner suffices. If memory retrieval adds no value, do not assume vector infrastructure fixes it. If Dream-RSI history coverage is low, collect distinct online branches first; do not relax gates until results look favorable.
