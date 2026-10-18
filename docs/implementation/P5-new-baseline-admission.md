# New frozen baseline adapter admission

Date: 2026-10-18. Status: VERIFIED_WITH_LIMITATIONS.

The owner-supplied new schema is explicitly selected through `--format new` and
`--artifacts`; it never falls back to legacy models. A new private output holds
inference-only episode/event ledgers and provenance. The supplied trusted loader
and frozen pickles execute only in an opt-in worker, with fitting disabled,
bytecode writes disabled, and loader outputs redirected away from upstream.
The normal adapter reads checksummed ledgers and never opens executable models.

Both actual sides were materialized and admitted. Frozen and portable model
predictions agreed on every retained entry/event, maximum score error zero.
Original feature-only cutoffs were independently recomputed without choosing new
values. The adapter independently reproduced all four saved calibration policies,
checked chronological embargo, strict threshold decisions, legal prefill timing,
snapshot/entry lineage, and one PL contribution per episode. Protected upstream
fingerprints were unchanged across each completed materialization.

The first LONG worker attempt completed inference/calibration/comparison but
failed when serializing an upstream Timestamp in its audit. It exited with a
known failure and published no output directory. Serialization was repaired using
the supplied JSON normalization API, then one explicit new local attempt passed.
There was no uncertain external job, training, model change or remote submission.

New controls support one-factor cancellation risk/waiting-time/closed-rule
comparisons and explicitly declared joint interventions. Current rule fields are
model risk and order age. Missing spread/adverse-price/ack capabilities are denied.
ResearchTurnV2 is a separate persisted kind; old answer schemas and prompt text
remain intact. An actual prior-version LLM campaign history was verified after
the parser change without provider/tool calls.

Executed Ruff lint/format, mypy and `pytest -q`: **107 passed in 40.57s**; 47
Python files formatted, 34 source modules type checked. CI uses inert synthetic
artifacts, including deliberately invalid pickle bytes to prove the default
reader does not load models. New checks cover four-policy parity, source drift,
unique episodes despite duplicate provisional keys, first crossing, unsupported
rule fields, causal waiting times, V2 admission and request-bound schema.

Private operational commands used the actual supplied paths; the public form is:

```sh
jarvis hermes materialize --format new --root "$HERMES_ROOT" --side long \
  --artifacts "$NEW_LONG_ARTIFACTS" --python "$HERMES_PYTHON" \
  --output .jarvis/new-baseline/long
jarvis hermes inspect --format new --root "$HERMES_ROOT" --side long \
  --artifacts "$NEW_LONG_ARTIFACTS" --ledger-root .jarvis/new-baseline/long
```

Research is restricted to already-exposed calibration data. The original frozen
September protocol is retained. Current replay is conditional on historically
filled baseline entries and immediate successful prefill cancellation. No fresh
strategy validation, price-path exit result or execution acknowledgement is
established by adapter fidelity. Multi-round real LLM acceptance follows in a
separate receipt after execution.
