# ADR-014 — New baseline admission and real development research

Date: 2026-10-18. Origin: owner requested new-baseline integration, baseline
fidelity checks, multiple real LLM research rounds, and feasible exit/cancel tools.

Add an explicit new-baseline adapter without silently selecting the legacy
artifact layout. User-supplied metadata, normalized data, schema sidecar, trusted
loader, frozen models and saved baseline are immutable private inputs. An opt-in
inference-only worker runs in the supplied environment, disables fitting and
redirects loader audit writes into a new Jarvis-owned private directory. It
compares portable booster and frozen model predictions, checks feature contracts,
reproduces calibration thresholds, and exports episode/event ledgers. The normal
adapter never loads executable model pickles or imports private strategy source.

Independently reconcile all four saved calibration policies. Admission requires
strict thresholds, legal prefill times, unique entry lineage and a single entry-PL
contribution despite multiple snapshots. Current exposed calibration remains
development. Preserve the original frozen September protocol; do not train,
change deployed settings, or select on its already-read external results.

Version new LLM experiment turns separately so older stored answers and schemas
continue to verify. Admit bounded Trading/cancel thresholds, prefill waiting-time
and closed cancellation rules, with explicitly declared joint comparisons.
Missing price/spread/ack/fill-path capabilities fail closed. Prior-round findings
enter a new campaign through a measured, provenance-tagged review tool, after
mandatory inspection and baseline; no earlier decision gains future evidence.

Implement the supported local prefill decision replay and a separate typed exit
backtest input/runner contract with artificial-data tests. Price-path exit replay
on real input requires complete postfill quotes and explicit execution/fee
semantics, not a clipped saved outcome. No external backtester submission or
message is authorized by this local workflow. All real results/reports stay local;
publish generic code, fixtures and scoped engineering acceptance only.
