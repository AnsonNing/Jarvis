# Hermes local integration

Date: 2026-10-18. Status: VERIFIED_WITH_LIMITATIONS.

The owner supplied the existing local Hermes repository and current data.
This supersedes the original planning assumption that all real inputs were
unavailable. The integration remains an injected adapter; the core never assumes
a side, product, feature formula, target definition or numerical PnL policy.

The default reader verifies raw Parquet, module and model fingerprints without
opening executable pickles. It checks all declared 91 feature slots, unique entry
lineage, fixed strict thresholds, first-crossing cardinality/timing, one-entry
financial accounting, daily metrics including zero-activity days, and agreement
with the selected model metadata. README differences are explicit diagnostics.

Real LONG and SHORT inspection was executed. The saved LONG ledger was found to
belong to older thresholds than the current model metadata and summary. Jarvis
rejected that mixture; it did not silently choose whichever performance looked
better. SHORT's saved ledger reproduced the declared baseline directly.

An explicit inference-only worker was then executed with the upstream Python
environment to materialize LONG's **current frozen model** into an independent
private folder. Source/model checksums were checked, the upstream loader's audit
writes redirected, and LightGBM fit disabled in the worker. No retraining or
external event backtest was submitted. The new ledger reproduced the current
metadata; protected upstream input fingerprints were identical before and after.
The original stale ledger and README were preserved.

The worker imports the user-provided local module and opens checksum-matched
model pickles. These are **trusted executable local inputs**. This opt-in bridge
is not an arbitrary-code sandbox. The default artifact reader never opens them.
The worker source and numerical provenance are saved beside the private outputs.
Reconstruction cost is recorded there, separately from later diagnostic campaigns.

Hermes campaigns execute inspect → baseline → an evidence-conditioned attribution
or cancel-effect diagnostic → cost sensitivity → report. All operations are
existing-data analysis; none claim a newly profitable strategy. A previously
exposed evaluation period is development/replay. No untouched locked test is
configured; formal evaluation returns NOT_RUN. PL is source entry PL, whose units
and fee inclusion still depend on the upstream generator. Extra cost scenarios
are arithmetic sensitivity, not a calibrated fill/slippage simulation.

Reproducible local commands (set paths privately):

```sh
jarvis hermes inspect --root "$HERMES_ROOT" --side short
jarvis hermes materialize --root "$HERMES_ROOT" --side long \
  --python "$HERMES_PYTHON" --output .jarvis/materialized-long
jarvis hermes start --root "$HERMES_ROOT" --side long \
  --ledger-root .jarvis/materialized-long
jarvis hermes start --root "$HERMES_ROOT" --side short
jarvis campaign report <campaign-id>
```

Generic automated tests use synthetic artifact fixtures only. They reject stale
cutoffs, duplicate entries/crossings, wrong PL, missing scores, changed sources,
escaping model paths and mismatched materialization lineage. No private data,
model, strategy source, local path or actual trading row is committed here.

Next integration: an explicit training job interface writing isolated outputs,
existing event-backtester submission/status/result/usage contracts, realistic
execution semantics and a newly declared untouched final evaluation period.
