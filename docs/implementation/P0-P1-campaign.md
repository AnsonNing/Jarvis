# Bounded research campaign verification

Date: 2026-10-18. Status: VERIFIED_WITH_LIMITATIONS.

The executable kernel includes a single-host sequential controller, sealed World
identity, complete decision-time state/frontier/evidence, transactional resource
reservations, durable job receipts, fixed evaluator, deterministic Q0, discovery
tree verification, reports and exact recorded-path replay.

The four synthetic worlds exercise negative-first research, positive-first
research, no demonstrated improvement and a sufficient baseline. A failed
capacity experiment causes a diagnostic followed by a different hypothesis;
an observed positive capacity result instead causes refinement. Fixture methods
are declared human/fixture inputs, not LLM scientific inventions.

Director, Scientist and Designer emit structured decision/hypothesis/action
records. Critic and Curator produce evidence-linked negative/positive/inconclusive
records. The provider-neutral completion interface and mock provider are tested.
The active controller is deterministic; no external LLM provider is invoked.

Recovery is exercised after reservation, after receipt, and with a real child
process exiting via os._exit after the durable receipt. The original job/result
is adopted exactly once. A started job without a receipt stops at
NEEDS_RECONCILIATION and retains its reservation. Its cost is pending, not a
fabricated measured charge. Only one coordinator can run each campaign.

Evaluation/selection rules are pinned by source hash. The formal synthetic
evaluation opens only after a durable candidate freeze and is separately charged.
It does not become development evidence. Resume of a closed campaign is a no-op.
Source, dependency, data or evaluator changes refuse resume of the frozen World.
Elapsed campaign deadline includes time spent paused.

Replay invokes no domain adapter, evaluator, LLM or external service. It pays
historically measured invocation costs before revealing a result. An unrecorded
eligible action returns INSUFFICIENT_COVERAGE / UNKNOWN_ACTION with no invented
score. The implementation follows one recorded causal path and can stop early;
it does not yet reorder sibling experiments, build a History Pool or promote Q.

Validation commands:

```sh
python -m pytest -q
ruff check src tests
ruff format --check src tests
mypy src/jarvis
jarvis task validate examples/research_task.example.yaml
jarvis campaign start examples/research_task.example.yaml --max-steps 2
jarvis campaign resume <campaign-id>
jarvis campaign report <campaign-id>
jarvis history verify <campaign-id>
jarvis rsi replay <campaign-id>
```

JSON report references are content-addressed evaluator and job artifacts.
Detailed test results and phase accounting are in PROGRESS.md and the commit's
GitHub Actions run. Synthetic results have no financial interpretation.

Remaining before full P1: a tested live structured provider with actual token
metering; generated candidate code in an OS/container security boundary;
asynchronous worker leases/heartbeats; more failure/ambiguity fixture families;
GPU/cash/token budgeting for real external tools. No secure arbitrary-code
sandbox, capability transfer, or Dream-RSI efficacy is claimed.
