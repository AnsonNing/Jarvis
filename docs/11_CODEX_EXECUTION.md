# 11 — Local Codex Handoff / 逐步實作指令

## 0. Goal of first handoff

The user is providing the **agent system specification now**, but has NOT supplied the training/backtest/data interface. Implement Jarvis as a working *generic research system* against a synthetic domain. Do not build a dummy trading system that pretends to return PnL.

Read AGENTS.md and all documents linked from README.md. Resolve ambiguous interfaces by honoring the hard invariants and write a concise ADR before materially diverging.

## 1. Start sequence (first commits)

**Commit A — packaging and contracts**
- Create a Python 3.12 package, pyproject, CLI, pytest, code formatting and type-checking config.
- Implement persisted typed contracts and JSON Schema from docs/04_CONTRACTS.md.
- Add canonical JSON hashing, identity/version tests and safe manifest paths.
- Implement command: jarvis task validate examples/research_task.example.yaml.
- Exit criteria: tests reject unknown schema/field, invalid budget, inconsistent World, and serialize roundtrip.

**Commit B — event store / immutable evidence / budget**
- Implement append-only event journal, sealed artifact storage, World/Campaign creation and ResourceLedger.
- SQLite WAL + file-level transaction or equivalent proven single-host safe design.
- Implement integrity check and event-sourced readback.
- Exit criteria: one-byte tampering detected; parallel reservations bounded; crash during append recoverable.

**Commit C — synthetic research tools**
- Implement deterministic synthetic DomainAdapter with observed diagnostic and bounded candidate evaluation.
- Include at least two hypotheses, a negative branch, positive branch, missing-action branch, a fixture locked test and a test-only failure injection.
- Implement fixed EvalService separately from research code.
- Exit criteria: synthetic truth is never exposed to Research Agent before declared evaluation; tool outputs have evidence refs and real resource accounting.

**Commit D — frontier, Q0 and campaign controller**
- Implement admitted ActionSpec frontier, immutable ResearchState, Q0 deterministic policy, campaign FSM and worker scheduler.
- CLI: start/status/stop/resume/report.
- Exit criteria: a scripted/mock research agent uses observed first result to choose different second action, without treating missing branch as known.

**Commit E — role-specific LLM interface**
- Define provider-neutral structured completion protocol; begin with MockLLMProvider.
- Add Director, Scientist, Designer, Critic and Curator as logical roles with permission-filtered prompts.
- Optional real provider integration must be explicit and tested with availability checks; never assume an API key exists.
- Exit criteria: invalid agent action/evidence IDs rejected, actual provider tokens charged when available.

**Commit F — reports and failure recovery**
- Reports from sealed evaluator results; include all evaluated variants and negative findings.
- Inject crash at job completion; reconcile/resume without duplicate evaluation.
- Exit criteria: runnable end-to-end example with immutable Discovery Tree, exact cost, stable results, no external market data.

Only after P1 verification proceed to P2 and P3. Start Dream-RSI code with Q0 and replayable tree formats early, but do not proclaim policy improvement before P4 tests.

## 2. Initial expected CLI workflow

~~~sh
python -m pip install -e '.[dev]'
pytest -q
jarvis task validate examples/research_task.example.yaml
jarvis campaign start examples/research_task.example.yaml --policy Q0
jarvis campaign status <actual-campaign-id>
jarvis campaign report <actual-campaign-id>
~~~

CLI names are target contracts; implement each only when its backing functionality exists. If actual output uses Python module invocation instead of entrypoint, document exact verified commands.

## 3. Implementation branching and reports

- Implement in feature branches / incremental commits, not one giant unreviewable rewrite.
- Each work package must update docs/implementation/<package>.md with actual test runs, code hash, limitations and next actions.
- Keep a factual docs/implementation/PROGRESS.md with NOT_STARTED/IN_PROGRESS/VERIFIED/BLOCKED; no "passed" without command output.
- Never push logs, credentials, generated model weights, full copyrighted papers, private repo source or real financial datasets to public Jarvis.
- Do not auto-merge a feature that violates safety/evidence boundaries.

## 4. Suggested initial Python design details

Typed models: ResearchTask, WorldSpec, Budget, ResourceCharge, Hypothesis, ActionSpec, Frontier, ResearchState, ExperimentSpec, ToolRequest, ToolResult, EvidenceRef, EvaluationResult, DiscoveryNode, JournalEvent, KnowledgeClaim, CapabilityDescriptor, ResearchPolicy.

Suggested service interfaces:
- WorldRegistry.seal/verify
- CampaignStore.create/read/transition/rebuild
- EventJournal.append/read/verify
- BudgetLedger.reserve/settle/reconcile
- AdapterRegistry.describe/validate/execute
- FrontierBuilder.build/admit
- PolicyCompiler.validate/compile
- Scheduler.dispatch/reconcile
- Evaluator.evaluate
- EvidenceGateway.visible_for
- TreeBuilder.from_events/verify
- ResearchAgent.decide (typed, mockable)
- ReportBuilder.render_json/render_markdown

For IDs, use content-derived identity of scientific objects and unique execution-instance IDs separately; do not accidentally deduplicate distinct actions sharing parameters if they intentionally represent repeated experiments. Deterministic seeds and stable fixture version essential.

## 5. What to do when a missing detail appears

- Missing domain feature/column → record MissingInput, declare which exact input is needed.
- Existing local training/backtest path unknown → keep Adapter unconfigured; do not scrape user's desktop or infer a private path.
- Missing model/API access → run mock provider and report that live provider evaluation was not done.
- Unclear evaluation protocol → choose a declared synthetic-only rule and mark explicitly not financial.
- Lacking enough discovery trees → record coverage metrics; do not manufacture replay branches or quietly lower scientific gates.
- Need to reproduce code from other repositories → use architecture concepts first; review public/private status and licensing before source transplant.

## 6. Dream-RSI implementation sequence (after P3)

Implement exact replay **before** policy developer. Then:
- restrictive DSL, deterministic compiler and Q0;
- History Pool compatibility and family splits;
- reproducible replay traces/cost/coverage;
- development-only policy proposals and validator;
- frozen finalist selection and heldout replay;
- fresh-campaign validation, provenance and rollback.

The system must refuse a Dream-RSI claim if any branch result was guessed or generated, or if an evaluator/tool call occurred during replay.

## 7. Acceptance receipt template

~~~markdown
# P0.X Verification
Status: VERIFIED / BLOCKED / VERIFIED_WITH_LIMITATIONS
Commit: <actual SHA>
Environment: <actual host/runtime info, redact private identifiers>
Commands: <exact executed commands>
Observed results: <counts and IDs>
Evidence: <immutable artifact refs>
Not executed: <list>
Limitations: <list>
Decision changes: <ADR refs>
Next work package: <ID>
~~~

## 8. Copyable request for local Codex

> Implement Jarvis P0 according to AGENTS.md and docs/00–04, 08–11. Start by creating the real Python package, typed contracts, deterministic synthetic fixture, append-only evidence store, fixed evaluator, Q0 and campaign CLI. Work in small commits; add tests for each contract; run them and report exact commands/output. The user has not provided a real backtest/data system, so keep all trading integrations as unconfigured typed adapters and do not fabricate market results. Stop only after producing a verified P0 report or an explicit blocker. Do not claim P1–P5 complete.

## 9. Deliverable expected by owner after each phase

A concise human summary: capabilities implemented, tests/receipts, current limits, exact commands, current main branch/PR/commit, and next requested data/integration information. No generic "all done" without evidence.
