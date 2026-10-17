# 15 — Settings exploration and executable evidence

The owner requested exploration of take-profit, stop-loss, cancellation logic
and other settings. A research plan may declare finite numeric/categorical axes,
single-axis controls or a bounded factorial matrix. Every candidate has a content
identity, units, provenance and explicit capability requirements. Enumeration is
design evidence, not an executed experiment or measured performance.

## Supported declaration

| Axis | Unit / contract | Required execution capabilities |
| --- | --- | --- |
| take_profit_bps | nonnegative basis points, explicit finite values | price_path, fill_events, exit_simulation |
| stop_loss_bps | nonnegative basis points, explicit finite values | price_path, fill_events, exit_simulation |
| trading_cutoff_delta | bounded score delta | frozen_threshold_comparison |
| cancel_mode | baseline / disabled | frozen_cancel_comparison |
| cancel_rule | closed all/any conditions on score, age, spread, adverse move | order_event_stream, cancel_rule_execution |
| multiple settings | declared factorial controls and fixed baseline | joint_parameter_comparison in addition to axis requirements |

The current Hermes adapter can execute its existing one-variable frozen-model
Trading cutoff and cancellation-disable comparisons. It does not supply the
price-path exit simulator or the new rule executor. Those declarations therefore
return REQUIRES_CAPABILITY and execution NOT_RUN. The example's 54 combinations
are a bounded contract demonstration, not a search result or recommended settings.

## Required backend evidence for exits and new cancellation logic

The future adapter must negotiate these inputs and semantics explicitly:

- Time-ordered product/entry/order lineage, signal and placement timestamps, and
  independent price/quote path identity; no future row enters an earlier decision.
- Fills, partial fills, cancellations, rejects and acknowledgement timestamps.
  A stop/target applies only to an established position under the declared rule.
- Tick or bar resolution, gap/trigger/priority semantics, ambiguous same-bar
  stop-and-target ordering, available bid/ask and latency assumptions.
- Frozen fees, taxes, slippage, opportunity cost and position-sizing/accounting
  rules. Preserve one contribution per entry and report skipped/unmatched rows.
- Immutable baseline, candidate settings, dataset/split identities, fixed evaluator,
  cost/timeout reservation and durable request/result receipts.
- Independent validation period/family. Existing exposed Hermes data remains
  development; a changed replay score does not establish a deployable strategy.

Do not derive stop-loss/take-profit outcomes by clipping saved entry PL, treat
an immediate cancellation assumption as an observed fill, or interpret a missing
event as a successful exit. Missing inputs are named requirements, not defaults.

## Connection to Dream-RSI

The domain adapter produces measured candidate evidence. The exploration policy
chooses the next admitted experiment using that evidence. Dream-RSI improves this
choice process under fixed data/tools/budgets; changing a stop value itself is a
domain candidate, not automatically a research-policy improvement. New parameter
values without exact realized edges return unknown in offline replay.
