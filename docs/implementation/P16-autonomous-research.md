# P16 — Autonomous quant research sessions and Dream-RSI over research actions

Goal: give Jarvis the working loop of a junior quant researcher. The owner supplies a
baseline, a goal, a budget and what may change; Jarvis decides how to research it —
read the code, analyze observed trades, backtest controlled designs, retrain models,
change the training pipeline, write new rules — and delivers a reproducible report.
Dream-RSI then improves *how* it chooses those actions across tasks.

This release adds the machinery and verifies it end to end on a synthetic Hermes-like
world. It starts no Hermes study, Iris submission or real training run.

## What was missing in 0.14 (and what closes it)

| Gap in 0.14 | Now |
| --- | --- |
| Diagnostics could only use snapshots fixed at session start; backtests returned summaries | Every backtest registers its trades/daily tables as `ObservedTable`s with lineage; later actions analyze them |
| Only three fixed skills; generated code limited to one protocol; no pandas | `ANALYZE`: an agentic worker writes and runs analysis code in an OS-isolated workbench with declared packages; the final script is re-run and outputs compared |
| Roles could not read the strategy or pipeline | `READ_SOURCE` over owner-declared, hash-pinned source scopes |
| The outer Director only gave a direction text | `BACKTEST` takes an explicit experiment matrix (candidate × settings × period) |
| One pinned model lineage; comparisons required the same model | `Candidate` = model artifact + strategy patches + settings, with data-generation lineage; cross-model comparison requires only the same evaluator/dataset/period |
| Training was blocked | `TRAIN_MODEL` through an injected `Trainer`; immutable `ModelArtifact`; trained candidates |
| New rules only through a closed DSL | `IMPLEMENT`: agentic code change in a private workspace, accepted only after a trusted validator |
| PL-first selection only | Owner-declared `AcceptanceProfile`s (PL-first, PR-first, risk limits), Pareto front and a pre-declared finalist rule |
| One action at a time, blocking | Long actions run in background child campaigns; the Director keeps working or WAITs |
| No research-plan view, easy to stop early | Branch/question plan derived from the ledger; a stop guard requires evidence-backed closure of every open branch |
| No steering channel | `advise`/`redirect` at any time; the next decision must answer each item |
| Dream screened Iris parameter families only | Dream-RSI over the new action policy with paired fresh validation on a hidden holdout, adoption, canary and rollback |

## Session loop

```text
owner: AutonomyContract (+ baseline Candidate, settings space, training space,
       implement targets, source scopes, metrics, acceptance profiles, grants)
  -> AutonomousResearch.create  (pins providers, executors, policy, budget)
  -> advance (one durable step):
       collect finished background actions -> register tables/candidates/models/patches
       Critic reviews each completed ANALYZE/BACKTEST/TRAIN_MODEL/IMPLEMENT/COMPARE
       Director decision: answer pending guidance, open branch/question, ONE action
         validated against contract, executors, policy Q and the stop guard
         (invalid answers get up to two receipted corrections)
       READ_SOURCE / COMPARE / CONSULT / WAIT / STOP run inline;
       ANALYZE / BACKTEST / TRAIN_MODEL / IMPLEMENT launch a child campaign
  -> run: repeats advance until CLOSED, BUDGET_EXHAUSTED, NEEDS_RECONCILIATION,
     NEEDS_ATTENTION (three failed decision attempts at one index) or owner pause
```

Every role call, sandbox run, training job and backtest is reserved against the
session budget before it runs and settled from its receipt. Local side-effect-free
work interrupted by a crash is settled at its reservation and re-run
(`LOCAL_JOB_INTERRUPTED`); paid provider calls and remote submissions never are.
Remote Iris backtests write `submission.json` before upload and only collect after a
restart.

### Action space

| Action | Executor | Output |
| --- | --- | --- |
| `READ_SOURCE` | harness, hash-checked | source text (truncated at 60 KB) |
| `ANALYZE` | agentic worker in the workbench | summary, numeric findings, scripts, output tables, re-run check |
| `BACKTEST` | `Backtester` | one `BacktestObservation` per experiment + trades/daily tables |
| `TRAIN_MODEL` | `Trainer` | `ModelArtifact` + trained `Candidate` |
| `IMPLEMENT` | agentic worker + `PatchValidator` | `CodePatch` (+ `Candidate` for strategy patches) or a rejected attempt |
| `COMPARE` | harness | per-profile verdicts, Pareto front, declared-rule finalist |
| `CONSULT` | specialist role | advisory opinion (not a measurement) |
| `WAIT` / `STOP` | harness | — |

Guards enforced on every decision: the action is granted; cited evidence was visible;
branches and hypotheses are declared; settings are declared and in range, with at most
`max_setting_changes` changes; an identical configuration or analysis is never repeated;
training/implementation grants are not exceeded; `WAIT` needs a running action.

### Stop guard

A session `STOP` is accepted only when no action is running and every open branch is
closed with visible evidence. `research_complete` additionally needs a finalist that
appeared in a completed `COMPARE`. Rejections carry a correction the Director must act
on. Budget exhaustion and the decision bound stop the session with `SESSION_PARTIAL_STOP`.

### Owner guidance

`jarvis autonomy advise OWNER "..."` (or `--redirect`) appends `OwnerGuidance`. From the
next decision on, the Director must answer each pending item (adopt or decline with a
reason). Responses are part of the decision record and of the deliverable. Guidance is
data; it never changes the contract, evaluator, data or budget.

## Agentic workbench

The worker is a tools-disabled provider: each turn it returns one structured step
(`write_file`, `run_python`, `read_file`, `list_files`, `validate`, `finish`) and the harness
executes it behind `Workbench`:

- macOS `sandbox-exec` deny-by-default profile (Linux: bubblewrap), probed before use;
- read-only `inputs/` (tables), `sources/` (declared scopes), `base/`; only `work/` is writable;
- no network, clean environment, no credentials; CPU/file/size/time limits;
- the analysis interpreter and site paths are declared in the contract (pandas, numpy, …).

Codex's own `workspace-write` sandbox was not used: it can read the whole disk (for
example the Slack token file used by `iris_agent.py`). The harness-executed loop keeps
every step receipted and inside Jarvis's own isolation.

## Comparison and deliverable

`session_outcome` recomputes comparisons from observations (never from Director claims)
and applies the pre-declared finalist rule: the earliest acceptance profile with a
passing candidate, best on that profile's first objective. `jarvis autonomy deliverable`
writes `deliverable.md` / `deliverable.json`: baseline, branches and their closures,
hypotheses and Critic verdicts, every action, candidates with model and data lineage,
the comparison table, the finalist, owner guidance and responses, next questions and
costs. The verdict is `DEVELOPMENT_IMPROVEMENT`, `NO_DEVELOPMENT_IMPROVEMENT` or
`INSUFFICIENT_EVIDENCE`; independent validation is explicitly NOT_RUN on exposed data.

## Dream-RSI over research actions

`ResearchPolicy` (Q) is a closed policy: method guidance for the Director plus
harness-enforced rules (explain passing results before more backtests, maximum values
per setting, consecutive-backtest limit, required analyses before stopping, required
model branch when a passing candidate changed model inputs, branch shares). Q0 has no
extra rules.

```text
round: development sessions (active Q) -> sealed history pool
       -> developer proposes <=3 candidates from development histories only
       -> exact replay screen: first recorded decision each candidate would reject
          (guidance-only changes are flagged; they cannot be screened by replay)
       -> paired fresh sessions on disjoint validation tasks, same budget;
          utility = holdout gain of the declared-rule finalist, measured by a trusted
          evaluator on days no research role ever saw
       -> adopt only with a positive mean gain and a majority of paired wins
       -> fresh canary task; regression rolls the registry back
```

`PolicyRegistry` is append-only (`POLICY_ACTIVATED`, `POLICY_ROLLED_BACK`). A session pins
the active policy when declared; a round refuses to run if the registry changed after it
was declared. Sessions stuck in `NEEDS_ATTENTION` under a candidate policy are scored as
measured outcomes; unknown paid work never is.

## Synthetic quant world

`quant_world.py` generates days of orders with decision-time features, an observed
adverse move and a would-be outcome, on calm and volatile days. A heuristic cancels
orders whose adverse move exceeds the bp; a logistic cancel model trained on survivors
of the 40 bp heuristic cancels the rest above its cutoff. Known structure (balanced
family, seed 11, dev period):

| Candidate | PL vs baseline |
| --- | --- |
| best single bp (25) | +2.9% |
| retrained cancel model at the new bp | +8% |
| regime-aware heuristic | +14% |
| regime-aware heuristic + retrained model | +22% |

All candidate code (strategy rule, features, training) runs inside the workbench. Days
40–59 form the hidden holdout used only by Dream's trusted evaluation.

`quant_fixture.py` provides scripted roles. Its Director is deliberately a naive
parameter tuner; policy corrections, owner guidance and analysis findings redirect it.
This exercises the mechanics; it is not evidence of research ability.

## Real-domain adapters (declared, not run)

`autonomy_adapters.py`:

- `IrisRegionBacktester`: candidate model files and strategy patches are whole region
  bodies (`<region>.region`); bound settings use the existing scalar renderer; one pinned
  `IrisEnvironment` per contract period; durable request before upload, collect-only resume.
- `CommandTrainer`: runs the owner's training entry point inside the workbench with
  `request.json` → `outputs/` + `result.json`; source hashes are checked on every run.
- `RegionPatchValidator` (markers, protected text, size, optional compile/smoke command)
  and `CommandPatchValidator` (owner tests and leakage checks for pipeline patches).
- `declared_domain`: builds contract, baseline candidate, inputs and executors from a
  private declaration (see `examples/autonomy/hermes.declaration.template.yaml`).

## Commands

```sh
jarvis autonomy sandbox-check
jarvis --store .jarvis/auto-fixture autonomy fixture --policy explain --output local/auto-fixture
jarvis --store .jarvis/auto-fixture-q0 autonomy fixture --policy q0
jarvis --store .jarvis/auto-dream autonomy dream-fixture
# Real LLM on the synthetic world (no Hermes data):
jarvis --store .jarvis/auto-llm autonomy fixture --agent codex --effort medium --max-decisions 8
# Declared sessions (synthetic example or a private real-domain declaration):
jarvis --store .jarvis/s autonomy start examples/autonomy/world.declaration.yaml
jarvis --store .jarvis/s autonomy run OWNER --wall-seconds 3600
jarvis --store .jarvis/s autonomy advise OWNER "Check whether the cancel model needs retraining"
jarvis --store .jarvis/s autonomy status OWNER
jarvis --store .jarvis/s autonomy pause OWNER   # resume / cancel
jarvis --store .jarvis/s autonomy report OWNER  # offline audit
jarvis --store .jarvis/s autonomy replay OWNER
jarvis --store .jarvis/s autonomy deliverable OWNER local/reports/s
```

## Provider robustness (from real-LLM acceptance)

- A call whose provider process provably ended (timeout, incomplete stream) without usage
  is settled at its full reservation (`JOB_SETTLED_AT_RESERVATION`) and retried as a new,
  separately receipted call; three failures at one decision stop with `NEEDS_ATTENTION`.
  Calls without a receipt stay unknown (`NEEDS_RECONCILIATION`).
- A usage/rate-limit refusal (`PROVIDER_QUOTA_EXHAUSTED`, zero tokens) pauses the session as
  `PROVIDER_UNAVAILABLE`, including paused background workers; the next `autonomy run`
  resumes at the same step. It never counts as a Director failure.
- WAIT does not consume the decision budget. The Director context is compacted to a
  character budget while keeping a complete `experiment_ledger` of every backtest.

## Limits

- Scripted roles and synthetic worlds verify mechanics, not research quality or real
  trading improvement. Real Hermes use needs the private declaration, a training entry
  point around the existing pipeline, Iris region submission and real-time quota policy.
- Iris quota accounting is not yet wired into `IrisRegionBacktester`; the session budget
  and grants bound submissions. Use the existing quota ledger before unattended runs.
- Background actions run on threads of the coordinating process; after a crash the same
  child resumes on the next `run`.
- macOS has no verified hard memory cap for workbench processes.
- Dream utility depends on the declared holdout; with only exposed data the policy verdict
  is development-only.
