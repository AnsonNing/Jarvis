# P18 — Research memory, skill accumulation and Dream from real histories

P16/P17 sessions were independent: each finished session left evidence in the store, but the
next session started from zero. P18 makes every finished (or paused) session change later ones:
what was tried, what was learned, reusable analysis code, and the research policy itself.

## One knowledge base per domain

`KnowledgeBase` (`research_memory.py`) keeps one `ResearchKnowledgeBase` campaign per
`contract.domain` per store, with its own research-policy registry (`autonomy:<domain>`).
It is append-only: each session is recorded once as `SESSION_CONSOLIDATED`.

Consolidation of a session writes:

1. **Trusted facts** (`SessionMemoryFacts`, no LLM): observations, candidates, models, patches,
   tables, analyses (with the reproducibility flag), rejected implementations, failed actions,
   branch closures, the recomputed finalist, open Critic questions and usage.
2. **Curated lessons** (`autonomy_curator` role, optional): short statements (finding, method,
   pitfall, open question) that must cite evidence visible in the facts. The curator may
   supersede earlier lessons; validation rejects unknown evidence or superseding unknown lessons.
3. **Promoted skills** (`ResearchSkill`, versioned): only from analyses whose scripts reran with
   identical outputs. Script files are stored as content-addressed blobs.

`AutonomousResearch(consolidate=True)` (the CLI default, `memory: true` in a declaration)
consolidates automatically at the end of every run: closed, budget exhausted, needs attention,
paused or cancelled. If the curator is unavailable, trusted facts are still recorded.

## How memory enters a new session

`autonomy start` pins `KnowledgeBase.pin(domain)` as `AutonomySessionSpec.memory_ref`, an
immutable snapshot, so replay and audit stay exact. The session then:

- treats remembered candidates, tables and experiments as visible evidence (reuse costs nothing);
- shows the Director the latest same-dataset experiments (`experiment_ledger`, `from_memory`,
  `comparable`), compact remembered tables, lessons, earlier session conclusions and skills.
  Recall is bounded (`MEMORY_SHOWN`), so role contexts do not grow with the number of sessions;
  older or other-dataset items are summarized by sessions and lessons;
- mounts every skill read-only under `skills/<name>/` in each analysis workspace (with a README);
  the worker is told which skills exist, and results record `skills_used`;
- gives the Critic the remembered lessons;
- counts model research done in earlier sessions for the "open a model branch" policy rule.
  Other policy rules (enumeration limits, explain-before-more-backtests) judge only this
  session's own actions.

COMPARE and the recomputed outcome only use observations with the contract's evaluator and
dataset; remembered experiments from another dataset are context, never comparison rows.

## Dream-RSI from real histories

`AutonomyDreamSpec.development_sessions` lets Dream learn from sessions that already ran instead
of running new development sessions. `autonomy dream start DECL` with
`development_sessions: memory` uses every finished session in the domain's knowledge base and the
domain registry as the incumbent. Validation and canary still run fresh paired sessions through a
declared `task_factory` (module:Class with `factory_args`) with a hidden holdout evaluated only by
trusted code. An adopted policy becomes the domain's active policy, which `autonomy start` uses for
every later session. Canary regression rolls it back.

## CLI

```sh
jarvis --store S autonomy memory show DOMAIN
jarvis --store S autonomy memory consolidate [--agent codex|claude|fixture|none] [--session OWNER]
jarvis --store S autonomy memory export DOMAIN DIR    # self-contained bundle (events, artifacts, blobs)
jarvis --store S autonomy memory import DIR           # hash-checked; idempotent per session
jarvis --store S autonomy dream start DREAM.yaml      # declares only
jarvis --store S autonomy dream run ROUND             # runs validation/canary sessions
jarvis --store S autonomy dream report ROUND
```

Use one store per domain so that sessions, memory and the policy registry stay together; export
and import move knowledge between stores.

## Owner guidance and resuming in a new conversation

The owner works with Jarvis through whatever conversation started it (Claude Code, Codex or a
shell). Nothing said there should be lost when that conversation or the session ends:

- every `advise` item and the Director's answer are part of the session facts; the Curator turns
  recurring guidance into `owner_preference` lessons;
- `autonomy memory note DOMAIN TEXT` records guidance given while no session runs;
- new sessions show the standing guidance to the Director (`research_memory.owner_guidance`,
  citable refs) and the Critic (`owner_standing_guidance`); current guidance overrides it;
- `autonomy status OWNER` reports the plan, recent actions with their reasons and Critic verdicts,
  the best result so far with profile verdicts, open questions, guidance with answers, the last
  issue and budget;
- `autonomy brief [--domain D]` gives a new conversation the whole store: sessions, Dream rounds,
  per-domain lessons, guidance, skills, active policy, sessions not yet remembered and next steps.

[OPERATOR.md](../OPERATOR.md) tells the conversational agent how to use these commands; `AGENTS.md`
and `CLAUDE.md` point to it.

## Verification

`tests/test_research_memory.py` (synthetic world, scripted roles):

- session 1 writes lessons and a `regime_threshold_scan` skill; session 2 starts from memory,
  uses the skill, needs fewer decisions, and still audits and replays;
- consolidation refuses running sessions, records facts only without a curator, and is idempotent;
- export/import reproduces lessons and skills, including skill files;
- Dream learns from two remembered sessions, adopts into the domain registry, and the next
  session runs with the adopted policy;
- CLI sessions start from the pinned memory and the registry's active policy;
- owner guidance and notes reach the next session's Director and Critic and survive export/import;
- `status` and `brief` report outcomes, guidance answers, unfinished sessions and memory.

Found and fixed while testing: remembered experiments counted against this session's enumeration
limit; remembered experiments from another dataset entered COMPARE and the outcome; remembered
tables made role contexts grow with history.

Full regression after these changes: **352 passed**; Ruff and mypy pass.

## Not claimed

Scripted roles show the mechanics, not research quality. A lesson is a curated hypothesis linked
to evidence, not a verified fact. Dream on real histories needs several real sessions and a
genuinely unseen holdout period to be meaningful.
