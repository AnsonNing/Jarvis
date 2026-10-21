# Release 0.15 — Autonomous quant research sessions and Dream-RSI over research actions

Jarvis can now work like a junior quant researcher on a declared task: read the code,
analyze observed trades in an isolated workbench, design backtest matrices, retrain
models, change strategy rules or the training pipeline, compare candidates under
multiple acceptance profiles, take owner advice at any time, and deliver a report.
Dream-RSI improves the policy that chooses those actions. Design and limits:
[P16](P16-autonomous-research.md). No Hermes study, Iris submission or real training ran.

## Executed acceptance

- Full local regression after all fixes: **340 passed in 569.52 s**, no failures
  (308 existing tests + 32 new autonomy, Dream and adapter tests).
- Ruff lint/format and mypy pass on all source modules.
- `jarvis autonomy sandbox-check`: host read, outside write and network all denied (macOS).
- Scripted fixture sessions (synthetic world, balanced/seed 11, dev period):
  naive tuner (Q0) finds bp 25, PL +2.9%; the explain-then-structure policy analyzes
  trades, implements a regime-aware cancel rule (+14.4%) and retrains on its population
  (PL 10,694.5 vs 8,774.2 baseline, +21.9%). Audits VERIFIED, exact replay COMPLETE,
  unseen branch INSUFFICIENT_COVERAGE, no pending reservations.
- Dream-RSI fixture round (2 development, 2 validation, 1 canary task): the developer
  proposed two policies; both changed behaviour on replay; paired fresh validation with a
  hidden holdout adopted explain-then-structure (mean holdout gain +12.8%, 2/2 wins);
  canary +15.7% vs +9.7%, no rollback. A degraded-canary control rolls back; a
  guidance-only copy is validated and not adopted; an identical proposal is rejected.
- Four bounded real-LLM sessions (Codex `gpt-6.1-sol`, medium effort) on the synthetic
  world. They exercised every action type with real structured outputs (including a
  2×2 heuristic × model-bypass factorial, matched-order attribution, an accepted
  pipeline patch adding adverse-move/volatility features and a successful retrain) and
  exposed six defects, all fixed with regression tests:
  1. worker `run_python` interface did not accept inline code / root-relative paths;
  2. WAIT consumed the decision budget;
  3. provider calls that ended without usage stopped the session (now settled at their
     reservation and retried, bounded);
  4. consultation provider failures were unhandled;
  5. provider usage-limit refusals were treated as Director failures (now a
     `PROVIDER_UNAVAILABLE` pause that resumes on the next run, zero tokens charged);
  6. unbounded Director context growth (now compacted to a budget while keeping a
     complete experiment ledger).
  The fourth run ended because the Codex account usage limit was reached.

## Not claimed

Scripted roles demonstrate mechanics, not research quality. Synthetic results are
SIMULATED_FIXTURE. No real-domain policy, strategy or efficiency verdict.
