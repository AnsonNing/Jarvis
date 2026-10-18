# Continuous Iris research under standing owner authorization

This extends the finite P6 programs with a persistent study and shared quota
ledger. There is no eight-experiment lifetime limit. Each program remains a small
bounded unit; a new program follows a completed one when results and executable
hypotheses justify continuation. The owner's standing instruction, model scope,
fixed metric and risk policy are sealed before execution.

## Execution and continuation

`IrisContinuousSpec` declares one or two tracks, each with a two-day integration
environment and a complete development-period environment using the same frozen
model/source. Model-package preparation still requires all upstream training,
export, compiled-source, smoke, comparison and final analysis gates to pass.

The study starts with one novel smoke experiment per track. A valid integration
result admits the development phase. The director proposes up to two experiments
per batch by default, bounded by the observed allowance, and can adjust exit,
entry, cancellation, heuristic and timing settings. A declared configuration can
request one to three experiments per batch. The next direction can be planned
while the current batch's remaining backend work waits.
Explicit joint experiments retain the existing closed intervention contract.
Every program has a finite call/token/experiment envelope. Completing that
envelope creates a subsequent program with causal prior evidence; it does not
end the overall study. The causal input window retains the founding scope, best
comparable prior queue and recent histories; all older evidence stays archived.
A recorded research stop, no novel executable hypothesis,
capability gap or backend failure is a meaningful stopping condition.

Existing baselines are reconstructed from accepted ledgers and subsequently
reused between compatible programs. The same source/branch/market-calendar
candidate is not uploaded again, even after renaming its queue or study. All
negative, rejected and uncertain attempts remain in the shared accounting.
The LLM cannot change the objective, evaluator, model, quota policy or its own
permissions. This remains development research on exposed periods.

## Shared quota and uncertainty

The worker uses the owner's approved local Iris API for read-only `files.list`
and acknowledgement retrieval. It restricts results to known request IDs and the
assigned branch. No token or channel configuration enters the parent, prompt or
report. An observation preserves the acknowledgement's original server-created
timestamp and its `today used/limit` values. It explicitly declares that this is
not an authoritative live remaining-quota endpoint.

Before every new upload, one transaction reserves a shared slot and subtracts
attempts not already covered by the latest acknowledged submission. A reserve
margin and the shared maximum outstanding job count apply across linked programs
and study instances using the same store/account scope. New finite grants never
reset this shared consumption. An acknowledgement for a later own request can
cover preceding attempts; an unknown submission retains its slot.

Stale, missing or exhausted quota observations prevent new uploads. An explicit
quota refusal requires a newer service observation. The local date does not
reset the ledger or prove the backend's quota window changed. Without a live
quota endpoint or newer acknowledgement, the study waits for new evidence rather
than resubmitting unchanged candidates. Other shared-account users can consume
quota after a snapshot; server rejection remains authoritative.

Quota observations, reservations and settlements have immutable journal events.
The offline verifier reconstructs allowance/slots and checks the derived tables
against those events. Every standing-study dispatch must have an earlier shared
reservation. Known terminal receipts release slots before receipt adoption, so a
coordinator interruption cannot strand an already completed job. Unknown
provider/backtest operations stop for reconciliation and preserve reservations.

Standing authorization is explicit owner provenance. Its local grant has a
bounded submission count but may use `expires_at=null`; it is constrained by the
study's environment scope, control state, shared quota and program envelope.
Standalone finite grants remain available. Pausing/cancelling the study prevents
further dispatch; active studies cannot silently change runtime.

## Persistent admission watcher

```sh
jarvis --store .jarvis/iris-live iris continuous-watch \
  --upstream "$ACCEPTED_PACKAGE" --python "$HERMES_PYTHON" \
  --owner-instruction "$OWNER_STANDING_INSTRUCTION" \
  --state local/iris/live-state.json \
  --wall-seconds 14400 --poll-seconds 45
```

The watcher holds a nonblocking file lease, waits for the accepted model package,
creates its study and atomically saves its ID before dispatch, then advances
rolling research. A restarted watcher reuses the same state and job IDs. A
completed watcher resumes without provider, quota or backend calls. Each
invocation has finite wall/step limits and can be resumed by the owner-authorized
heartbeat. A second coordinator cannot run concurrently against the same state.
The watcher refreshes quota observations periodically while preserving their
original timestamps. It never runs training or rewrites upstream artifacts.

Explicit operations are also available:

```sh
jarvis iris continuous-prepare --upstream "$ACCEPTED_PACKAGE" \
  --python "$HERMES_PYTHON" --owner-instruction "$OWNER_STANDING_INSTRUCTION" \
  --output local/iris/continuous-spec.json
jarvis --store .jarvis/iris-live iris continuous-start local/iris/continuous-spec.json
jarvis --store .jarvis/iris-live iris continuous-quota <study-id>
jarvis --store .jarvis/iris-live iris continuous-advance <study-id>
jarvis --store .jarvis/iris-live iris continuous-run <study-id>
jarvis --store .jarvis/iris-live iris continuous-report <study-id>
```

Reports retain each program's analysis, accepted/rejected proposals, capability
requests, results and actual/pending costs. Best candidates are compared within
the same track/period; two-day and full-period PL are never ranked together.
Entry-only PL remains primary, with all risk vectors and baseline deltas visible.
Intraday drawdown, full costs, capacity and acknowledgement semantics remain
limited by the supplied backend contract. This is research, with no live trading
or automatic strategy/policy deployment.
