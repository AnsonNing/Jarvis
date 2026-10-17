# 10 — Test Matrix and Acceptance Evidence

Tests need not use real HFT data. Implement a small deterministic research fixture with negative, positive, ambiguous and unexecuted branches; the same fixture powers exact replay and state-machine tests. Test names below are suggested semantic identifiers.

## 1. Contract and storage suite

| Test | Input / fault | Required outcome |
|---|---|---|
| schema_roundtrip | serialize/deserialize all persisted models | identical normalized representation and stable version |
| reject_unknown_schema | unsupported schema version | explicit failure, no silent coercion |
| world_immutable | change input/evaluator/budget after seal | old identity unchanged; mutation refused/new World needed |
| artifact_integrity | modify one byte of sealed result | verify fails; no scientific verdict |
| journal_atomicity | simultaneous writes and forced crash | monotonically ordered, gap/duplicate detected or recovered |
| decision_state_hash | response to prior state | STALE_DECISION rejection |
| evidence_access | evidence beyond visible sequence | EVIDENCE_NOT_VISIBLE rejection |

## 2. Research campaign suite

| Test | Required property |
|---|---|
| synthetic_adaptive_campaign | second decision differs based on observed first experiment result |
| alternate_branch | negative result causes a legitimate branch; abandoned path kept |
| no_model_capacity_shortcut | agent may choose diagnostic instead of expensive training; no forced model search |
| early_sufficient | strong baseline can go to verification, no needless search |
| budget_exhaustion | exact finite budget stops actions, retains partial results |
| invalid_action | unknown tool/action and forbidden split never execute |
| multi_action_budget | parallel sibling reservation cannot exceed global limits |
| negative_result_preserved | valid negative stays RESEARCH_NEGATIVE, not ERROR |
| infra_vs_science | injected job failure not classified as model failure |
| report_evidence | each numeric claim matches deterministic evaluator/artifact |
| locked_terminal | after frozen locked evaluation, no candidate tuning in same cohort |
| no_real_backend | synthetic works; unavailable real adapter reports NOT_CONFIGURED |

## 3. Resume and idempotency suite

Inject process kill at: pre-reservation, post-reservation, in-flight job, after tool receipt, after evaluation but before event append, at final-report writing. Restart must either complete idempotently, reconcile uncertain action, or stop with recoverable state. There may never be duplicate expensive compute due solely to coordinator retry. Require exact charged costs (including failed attempts), journal checksum parity and stable final report hash.

Concurrent two-worker fixture must not exceed max_parallel nor spend same reservation twice. An orphan result with wrong world or job hash must not be adopted.

## 4. Knowledge/capability suite

| Test | Required property |
|---|---|
| negative_memory | failed valid method retrieved with limitations |
| context_filter | unsupported domain/version/split never treated as validated transferable |
| contradiction | incompatible claims coexist with distinguishing context |
| provenance | human/source/agent origin and licensing recorded |
| fidelity != utility | passing unit tests cannot auto-promote cross-task benefit |
| tool_lifecycle | EXPERIMENTAL capability denied on validated-only execution route |
| fresh_session_reuse | new agent session can discover capability by need/capability registry |
| no_hindsight_memory | post-test summary cannot appear in earlier decision snapshot |
| skill_missing_data | missing required input → NEEDS_INPUT, not fake default |
| transfer_ab | matched-budget A/B reports results and uncertainty even if B loses |

## 5. Dream-RSI replay suite

| Test | Required property |
|---|---|
| q0_parity | recorded Q0 fixture path reproduced |
| replay_no_tools | zero LLM/adapter/tool/evaluator/network calls from replay |
| unknown_action | non-executed eligible action → UNKNOWN_ACTION, no score |
| causal_parent | alternative order with invalid state path → INVALID_REPLAY / insufficient coverage |
| reveal_when_paid | observed branch outcome invisible until historical path cost paid |
| future_info_hidden | future test/sibling outcomes unavailable to Q |
| budget_and_parallel | charge actual recorded compute, do not assert invented wall-clock |
| hash_pin | tampered result/source/compiler rejects replay or promotion |
| heldout_family_overlap | correlated family split across policy cohorts forbidden |
| incumbent_preservation | if candidates fail/unknown, Q0 not displaced |
| finalist_one_shot | only frozen finalist enters policy-heldout, no post-reveal retune |
| replay_not_fresh | scope REPLAY_ONLY cannot be labeled fresh online gain |
| regression_rollback | measured fresh campaign regression deprecates Q1 and preserves Q0 |

## 6. Security suite

- Agent cannot read locked-test path or private credential in context and tool execution.
- Generated code cannot edit evaluator, World manifest or protected job ledger.
- Retrieved source containing "ignore instructions" remains untrusted data.
- Tool action may not request network/command outside declared allowlist.
- A policy DSL with arbitrary code, unsafe predicate or injected import fails validation.
- Public-git safety test scans for credentials, real market data patterns, large binary checkpoints and private path leakage before push.

## 7. Research validation suite

Synthetic family suite needs at least:
- baseline sufficient task;
- model improvement valid task;
- no improvement/negative task;
- data unavailable task;
- misleading/overfit development task;
- changed conditions/shift task;
- tool crash task;
- multi-branch task with distinct realized outcomes;
- sibling action missing for replay.

Measure valid success and resource vector under fixed budgets for A (fresh/Q0), B (memory/Q0), C (memory/Q1). Report family-level uncertainty, no fabricated statistical significance or "universal" gain.

## 8. Report artifact for tests

Every verified package has:
test command, raw exit code/output, environment+dependency lock, commit/version IDs, fixture identity and sealed evidence refs; failing tests and missing prerequisites explicitly called out. The Codex agent must not self-attest performance without running the test. Store fixture truth separately from agent-visible input.

## 9. End-to-end release gates

- **Gate P0:** contract+storage+synthetic+Q0 tests green; no external data required.
- **Gate P1:** 2+ evidence-driven research decisions, correct report, durable restart, bounded execution.
- **Gate P2:** experimental capability fidelity + independent utility/no-utility evaluation + fresh-session retrieval.
- **Gate P3:** multiple independent worlds, replay-ready branching, A/B matched-budget transfer comparison.
- **Gate P4:** exact offline replay, honest unknown action, frozen candidate selection, heldout and fresh campaigns, negative promotion test.

Failing a **scientific improvement** expectation can still close a development milestone if the design's honest negative evaluation and control flow are verified; it cannot be presented as a successful strategy or policy improvement.
