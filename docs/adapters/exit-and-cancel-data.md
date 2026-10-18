# Exit and cancellation data/backend contract

New Hermes frozen predictions admit only exposed calibration episodes and legal
prefill snapshots. The adapter negotiates abilities through `backend_requirements`.
Model risk and elapsed order age can drive a closed cancellation rule. The first
matching snapshot cancels at most once per episode. This is a conditional replay
assuming immediate success on historically filled entries; it cannot estimate
fills for orders absent from that population.

## Quote exit simulation implemented

`ExitReplayInput` and `ExitSettings` are strict versioned JSON schemas exported by
`jarvis schema`. Every input declares a dataset identity, provenance, price and PL
units, and a shared monotonic millisecond clock. Each filled position supplies a
unique episode ID, long/short side, fill/horizon time, fill price, quantity, price
multiplier, and `fill_semantics="single_complete_fill"`. Quotes supply the same
episode ID, strictly increasing timestamps, positive bid/ask and bid <= ask.

Settings explicitly require nullable positive stop/target basis points (at least
one), latency milliseconds, adverse slippage basis points, per-unit per-side fees,
maximum acceptable quote gap and
`horizon_exit="first_quote_at_or_after_horizon_plus_latency"`.

Long exits observe bids; short exits observe asks. Stop/target triggers are tested
against entry price before the horizon. At/after the horizon, horizon close takes
precedence over any new price trigger. Execution uses the first quote at or after
trigger plus latency and applies adverse slippage. Gaps can produce a loss after a
target trigger. Net PL uses quantity and multiplier, then subtracts both-side
fees. It never clips or reuses saved outcome PL. Results are labelled SIMULATED:
quotes do not prove execution, available depth or acknowledgements.

The full quote path must cover fill through horizon plus latency within the
declared maximum quote gap. Orphan/duplicate/unsorted ticks, ambiguous position
lineage, missing/sparse paths and unsupported fill semantics are rejected. Missing
path coverage becomes a recorded REQUIRES_CAPABILITY result without a PL value.
Jobs pin inputs/settings/runner identities, reserve cost before running, adopt a
completed receipt after interruption, and refuse to resubmit an uncertain started
job. Receipt verification makes zero simulator/provider calls.

## Data still required for actual Hermes exits

Supply complete postfill bid/ask paths joined by stable episode ID, fill price and
time, quantity/multiplier/fee units, explicit session end/horizon, clock/timezone,
quote gap policy and trigger/fill semantics. Saved entry PL/PR, entry/exit prices
and prefill feature snapshots are insufficient. Feature slots are not interpreted
as price/spread without an independently verified mapping.

## Event backend still required for realistic cancellation

Supply all submitted orders (including never-filled and cancelled orders), stable
order/candidate IDs and event sequence, submit/ack/reject/expire times,
cancel-request/cancel-ack times, partial fills and remaining quantities, execution
prices and fees. The backend must resolve cancel/fill races, reject outcomes,
late/duplicate events and end-of-session handling without outcome information in
decision features. Its response must include per-order lineage, first trigger,
actual acknowledgement/fills, cash flows, execution assumptions and costs. Frozen
baseline reproduction and independent entry accounting are admission gates.

This event backend is not implemented using guessed timestamps. Remote backtest
submission, model retraining, fresh strategy validation and live policy promotion
are not performed by the local quote simulator or prefill comparisons.
