# Operating Jarvis for an owner

This guide is for the agent the owner talks to (a Claude Code, Codex or CLI conversation) when that
agent starts, steers or reports on Jarvis research. Jarvis keeps all state in its store; a new
conversation never starts from zero if it reads the store first.

## 1. Start of every conversation

```sh
jarvis --store STORE autonomy brief [--domain DOMAIN]
```

`brief` lists every research session (status, decisions, conclusion, finalist), Dream rounds, and
for each domain the remembered lessons, the owner's standing guidance, skills, open questions, the
active research policy and `next_steps` (sessions to continue, sessions not yet in memory).
Summarize it for the owner before doing anything else. Do not restart work that `brief` shows is
already done or running.

## 2. Answering the owner

| Owner asks | Use |
|---|---|
| How is it going / what is it doing | `autonomy status OWNER`: plan, recent actions with reasons and Critic verdicts, best result so far, open questions, guidance and answers, last issue, budget |
| Is it trustworthy / audit | `autonomy report OWNER`, `autonomy replay OWNER` |
| Final results | `autonomy deliverable OWNER DIR` |
| What do we already know | `autonomy memory show DOMAIN` |
| Detail behind a claim | read the cited `sha256:` artifact from the store |

Quote numbers from these outputs with their result refs; never estimate a result that was not
observed. Say when a result is development evidence on exposed data.

## 3. Passing the owner's ideas to Jarvis

Every idea, preference, correction or decision the owner states belongs in Jarvis, in the owner's
own meaning, so later sessions and later conversations know it.

- A session is running or paused: `autonomy advise OWNER "..."` (`--redirect` to change the
  priority). The Director must answer every item; show the owner its answer from `status`.
- No session is running: `autonomy memory note DOMAIN "..."`. Every later session in that domain
  sees it as standing owner guidance.
- Tell the owner exactly what you recorded.

Ended sessions are consolidated into memory automatically, including the owner's guidance; the
Curator turns recurring guidance into `owner_preference` lessons.

## 4. Running sessions

New CLI declarations enable `runtime: {}` by default. This pins bounded context and
prompt admission, finite provider retry/backoff, and preflight for nested clean
analysis reruns. Optional fields are absent in old persisted sessions; do not retrofit
their identities. Library callers may opt in with `RuntimeControls`.

Native worker execution requires an explicitly declared `analysis_environment.native`
with compiler path/hash and fixed flags, plus the needed read-only toolchain mounts.
An installed compiler alone does not grant the worker a callable tool. `run_native`
takes a saved `work/` C++ path, no custom command/flags; its trusted receipt is mounted
under `inputs/` for analysis. Inspect the receipt's source identity and scope before
claiming behavioral evidence. A clean Python summary rerun does not recompile.

`autonomy run` automatically exports a sealed report/markdown/checksum manifest on
closed, partial, paused, cancelled, unavailable and interrupted exits. Its response
contains `delivery.directory`; the store also has `deliveries/OWNER/latest.json`.
Report `runtime_exit.exit`, pending work, recovery history and resume point when a
session stops. `workflow_complete` and the research validation verdict are separate.
No extra provider call is needed to assemble this package. Prompt bytes are an
admission estimate; report actual or explicitly unknown/settled token usage.

Decision repairs receive the rejected draft (explicitly partial if large), exact
validation feedback and the new context identity. Existing hypotheses belong to
their declared question: use `question: null` and its existing IDs on a new branch.
An already observed matched baseline is a visible comparison reference, not a new
experiment. Rejected draft references do not grant evidence visibility.

Provider-ended failures and invalid decisions have separate finite allowances;
`runtime_exit.stop_details` records which one ended. Counts and receipts survive
another runner invocation. `resume` changes owner control only: it cannot clear
`NEEDS_ATTENTION`/`BUDGET_EXHAUSTED` or change a pinned runtime. Diagnose a terminal
session before proposing any continuation. Reservation settlements are estimates,
not observed provider usage or billing.

An unattended engineering check is available at
`examples/autonomy/runtime.declaration.yaml`. Start it in a fresh private store and
run its returned owner once. It performs actual isolated native execution with
scripted roles, a negative/alternative toy comparison and frozen confirmation.
It authorizes no real research, provider, Iris or Dream calls.

For new collaborative research declarations, use `research_planning: {}` (see
[P20](implementation/P20-collaborative-research-planning.md)). Do not add it to an
already declared session: its contract and runtime remain immutable. The owner can
state goals and tradeoffs in ordinary language; the operator supplies the declaration.
There is no required number of branches or routine approval checklist.

For IMPLEMENT, cite the analysis result or exact canonical definition/threshold tables in
`evidence_refs`; the worker receives those admissible records and the cited analysis
's registered visible tables in `inputs/`. Use `analyzable_evidence_ids` for ANALYZE
inputs; role/critique receipts are citations, not workspace input records. Preserve a
no-edit worker limitation as a limitation, rather than a failed scientific hypothesis.

If `status.owner_checkpoint` contains a question, show the owner that question and
the direction comparison. Record the reply with `advise` (or `--redirect`), then
`resume` and run in the background. This continues the same session with its branches
open. Resume alone does not invent an answer. A branch closure is not a mission-wide
negative verdict; use the recorded planning/stop basis when explaining completion.

- Start research only when the owner asks. `autonomy start DECL` only declares a session.
- Run it in the background and check on it, never in a blocking foreground call:
  `autonomy run OWNER --wall-seconds N`. One process per session; if `status` shows it running,
  do not start a second runner.
- `pause`, `resume`, `cancel` are immediate and recorded.
- Do not edit Jarvis or the domain adapter while a session runs: the runtime identity changes and the
  session stops. Declare a new session after code changes.
- Proved transient failures/refusals receive finite in-run retries. Persistent
  outages return a partial delivery. Unknown remote completion remains blocked for
  reconciliation. A hard process/machine kill still needs a process launcher to
  restart the same session; no background service is installed by Jarvis.
- A surrounding monitor must not treat a transient `PROVIDER_UNAVAILABLE` view
  as a finished runner while its process is still alive. Allow its finite retries
  and final export to finish; then use exit status and delivery. Owner stop control
  remains authoritative. Startup files saying `STARTED` do not prove liveness.

## 5. Costs and permissions

Backtests, LLM roles, the Curator (`memory consolidate --agent ...`) and Dream rounds cost quota or
tokens: ask the owner before Dream runs and before curating many sessions. Facts-only consolidation
(`--agent none`) is free. Never read credential files, never commit private data to this public
repository, and never commit or push without the owner asking.
