# P20 — Lightweight collaborative research planning

Status: VERIFIED_WITH_LIMITATIONS (engineering and synthetic protocol only).
See [ADR-016](ADR-016-collaborative-research-planning.md).

## Behavior

New declarations can set:

```yaml
research_planning: {}
```

The existing Director maintains a small, versioned direction comparison in its
normal response. Each card includes a falsifiable hypothesis, next action, estimated
effort, priority/feasibility reason, disposition and provenance. Distinct mechanisms
and cheap discriminating tests are encouraged after refutation; arbitrary parameter
variants do not constitute proof of breadth. The harness checks consistency and
visibility, not scientific novelty or subjective opportunity estimates.

Initial framing, a new branch, close/invalid criticism, owner redirect and stopping
need a short review. Routine reads, analyses and waits need no repeated form. Up to
eight current directions bound context size; there is no minimum branch count.
Pending directions cannot be silently dropped. Retired proposals remain in decision
history. Ready directions must have a granted available next action. Unavailable or
deferred work can remain untested with an explicit reason.

Branch failure does not establish task exhaustion. A session stop carries its own
reason and frontier review. Successful stopping still needs the existing compared
finalist and all declared selection/confirmation gates. No-justified-next-step
stopping cannot leave a ready alternative, but justified deferral is valid: resources
need not be spent merely to fill a quota. Forced budget halts remain partial resource
outcomes and retain untried options rather than claiming scientific exhaustion.

## Human collaboration

At a meaningful choice the Director can use `STOP scope=checkpoint` with one owner
question and no branch closures. It pauses and exposes the question in status and
the deliverable. The operator records the owner's reply using `autonomy advise`,
then resumes/runs the same session. Resume without a new reply stays paused without
provider calls. A redirect triggers reconsideration and must be answered. Direct
owner pause/cancel remains available independently of planning rules.

Checkpoint and stop completion survive interruption without repeating experiments.
No new tools, services, models, notification system or permission boundaries were
added. No live research or Dream is required to exercise the workflow.

## Synthetic acceptance

`examples/autonomy/planning.declaration.yaml` uses `planning-fixture`, a scripted
provider and a fixed toy score. All outcomes are `SIMULATED_FIXTURE`, not P&L. It
evaluates baseline, a negative route A, then a distinct positive route B. Additional
acceptance cases cover a premature task stop, a useful one-direction result,
unavailable alternatives, owner handoff/resume, explicit deferral and budget halt.

Commands:

```sh
python -m pytest -q tests/test_research_planning.py tests/test_trusted_research.py
python -m pytest -q
ruff check src tests
ruff format --check src tests
mypy src
```

Observed on 2026-10-25:

- **388 full regression tests passed in 697.09 s**.
- **31 targeted planning/stage tests passed in 13.55 s**.
- A separate shipped CLI example regression passed: **1 test in 1.51 s**.
- Ruff lint/format passed (149 Python files); mypy passed (112 source files).
- Dependency consistency and schema export passed.
- Five retained scripted cases all closed, audited VERIFIED and replayed COMPLETE
  with zero external replay calls: alternate route after failure, premature-stop
  repair, sufficient single direction, unavailable alternative, owner checkpoint.
- The actual start/run/status/deliverable/report/replay CLI path closed the toy
  study in five decisions. The example budget accommodates standard call
  reservations; initial zero-call token/wall budget halts are retained locally.
  The added CLI regression prevents that example-configuration failure recurring.
- 38 existing campaigns/subtasks across five historical stores retain identical
  event counts, final event hashes and immutable manifests. No old job was resumed.

Tests exercise actual durable
sessions, admission repairs, interruption recovery, decision-time audit and zero-call
replay. The scripted fixture intentionally supplies hypotheses; useful autonomous
hypothesis generation and improved real-domain research remain unverified.

Original session journals and the preceding uncommitted delivery/stage work are
preserved. Private preservation manifests and raw test logs stay in ignored local
storage. No historical contract migration, real backtest, paid provider campaign,
operational Dream run, commit or push is part of this package. The full regression
suite includes existing synthetic Dream tests, with no operational policy adoption.
