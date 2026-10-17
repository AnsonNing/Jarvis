# 08 — Durable Runtime, Resource Management and Security

## 1. Execution model

Start with single-host Python workers plus SQLite WAL metadata and file-backed sealed results. CLI starts/resumes campaigns; orchestrator state persists across process restarts. Later install a broker/remote scheduler only when measured concurrency requires it. Avoid rebuilding actual training/backtesting services; invoke them through controlled adapters.

Data plane and evaluator run independently from LLM process. Expensive jobs use immutable input manifests and idempotency keys; a lost LLM session cannot destroy research evidence.

## 2. Job and lease protocol

Each job has immutable identity, idempotency key, worktree/worker ID, attempt counter, lease expiry, heartbeat, resource reservation, output path and status. Scheduler permits at most one active owner for a given idempotency key, uses atomic compare-and-swap state transition, and reconciles stray outputs.

A job can be:
QUEUED → LEASED → RUNNING → SUCCEEDED/FAILED/INTERRUPTED → FINALIZED; UNCERTAIN requires explicit reconciliation. Never blindly reexecute when the remote training job might still be running.

Retry only safe/idempotent infra failures. User code exceptions, invalid data and genuine negative metrics are distinct. Every attempt and wasted GPU time counted. No maximum retries without an explicit finite bound.

## 3. Crash/restart invariants

- A completed, hash-verified tool result may be adopted once despite coordinator crash.
- If a job has started but its final state is unknown, wait for durable backend receipt or mark blocked for reconciliation.
- Resume rebuilds derived CampaignState and Discovery Tree from durable event journal; checksum/sequence mismatch fails closed.
- Candidate/final report cannot change in place after seal.
- Queue heartbeat/race does not cause duplicate expensive evaluations.
- Run STOP signal is checked between actions; kill switch should release pending reservations while retaining actually consumed resources.

Suggested invariant test: kill coordinator after job submission, after job completion, during receipt commit, and before formal evaluation. In each case restart must not double-count nor silently repeat an expensive attempt.

## 4. Budget accounting

For campaign B = {max_actions, max_llm_tokens, max_external_calls, max_gpu_seconds, max_wall_time, max_parallel_jobs, max_cash_cost, max_experiments}, implement reserve → execute → charge actual → release remainder, using transaction-safe updates. Prevent oversubscription for parallel siblings. For uncertain jobs reserve enough until reconciled or safely expired.

A budget exhaustion stops new work and returns EXHAUSTED with partial report; it never triggers unlimited resampling. Cost metadata should include failed and cancelled attempts and actual queue waiting. Offline Dreaming budget independent from online research budget.

## 5. Sandbox and code-generation boundary

Generated research code is untrusted until reviewed/admitted. Prefer disposable worktrees + container/OS restricted subprocess execution with:
- read-only input snapshot; no write to evaluator/locked-test/source-of-truth repositories;
- no production credentials, broker endpoints or network by default;
- limited file paths, CPU/GPU quota, memory, process count and wall time;
- captured stdout/stderr, source diff, dependency lock, tool- and environment-version;
- explicit allowlist for tools, packages and network sources if human approves them.

A subprocess timeout alone is not a secure sandbox; do not claim process-level isolation to be adversarial-proof. Require a real OS/container security boundary before executing arbitrary generated code on a sensitive host.

Model weights, datasets and training scripts provided later may remain under local/private mounts; do not copy into public repository or prompt vendors without explicit data-access rules.

## 6. Evaluation and information-flow protection

Authorized research LLMs see development evidence and redacted task metadata only. A locked evaluator may open private test data, but outputs only sealed verdict and permitted aggregate metrics. Deny direct file/network read of locked data from Builder/Specialist agents. Memory retrieval must apply source access restrictions before semantic ranking or summarization.

A report generated from formal test may be visible to a human; using its detailed outcomes for another candidate on same declared test requires new independent evaluation cohort. This is an **information-flow rule**, not just a prompt rule.

## 7. Security and permissions

- Role-specific, least-privilege ToolCatalog.
- No tool with arbitrary shell or unrestricted filesystem access for Director/Scientist/Q developer.
- Prompt-injection defenses: retrieved web/GitHub/paper content is data, never authority; strip/exclude tool-execution instructions from retrieved context.
- Separate service credentials for source reading, artifact storage, and optional private model backends; never put tokens in task JSON, logs or Git.
- Hash manifests prove change detection, not authenticity; protect local credentials and storage permissions separately.
- Policy DSL cannot access secrets, import modules, call APIs, alter evaluator, mutate privacy policy or create tools.
- Require explicit human authorization for any external network source, expensive compute above quota, vendor account billing changes or future live-trading interface (not in scope).

## 8. Observability

Persist:
- campaign event sequence, state version, job attempts and reconciliation;
- policy decisions, frontier candidates, reason/evidence refs, invalid-proposal rejection;
- resource reservations, actual spend, model tokens, compute, wall time;
- evaluator errors, split/identity mismatches, missing results, replay unsupported actions;
- capability usage and real transfer outcomes.

Useful dashboards are optional; CLI and structured reports are adequate first. Alerts are best handled by local system monitoring, not by claiming an always-on ChatGPT conversation.

## 9. Public repository hygiene

Jarvis repository currently is public. Generic source, mock fixtures, schema examples and high-level architectural references are safe. Proprietary market data, real order snapshots, model checkpoints, private repo content, personal API keys, server usernames/path details and deployment secrets must stay outside git. Do not copy from RSI/EEHEMT code until ownership, licenses and public disclosure are explicitly reviewed; the documents intentionally reference **concepts**, not transplant proprietary source.

## 10. Suggested CLI surface (implementation target)

~~~text
jarvis task validate <task.yaml>
jarvis campaign start <task.yaml> --policy Q0
jarvis campaign status <campaign-id>
jarvis campaign resume <campaign-id>
jarvis campaign pause <campaign-id>
jarvis campaign stop <campaign-id>
jarvis campaign report <campaign-id>
jarvis history verify <pool>
jarvis rsi replay --pool <pool> --policy <policy-id>
jarvis rsi propose --pool <development-pool> --incumbent Q0
jarvis rsi evaluate --proposal <id> --pool <frozen-pool>
jarvis capability list
jarvis capability validate <candidate-id>
~~~

Do not implement commands as no-op stubs that falsely print success. Each phase adds working commands with tests; unsupported ones should explicitly report NOT_IMPLEMENTED until the phase is delivered.
