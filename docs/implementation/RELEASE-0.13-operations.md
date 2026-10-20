# Research operating supervisor acceptance

A collection-time SlackError could propagate an attention state through the
mission and end its runner even though the original submission was known and
the failed action was only a read. This operating harness reconciles that narrow
case without replacing an active library or changing its source identity.

The complete fixed-environment suite passed **278 tests in 346.87 seconds**.
Ruff lint/format passed for 111 Python files; mypy passed for 83 source files
including the operating script. The kernel remains 0.13.0. Its preceding release
already passed Linux CI, including all complete synthetic CLI acceptances.

The three new synthetic tests demonstrate same-request read recovery with no
new submission or provider call, refusal to clear an unfinished provider
reservation, and preservation of an explicit owner pause. Known submission
receipts, original failures, unchanged request IDs, affected ancestor lineage
and runtime identity are checked before any resume. Unknown submissions and
unclassified failures are not automatically retried.

The supervisor holds one operating lock, checks the saved runner command and
mission identity, and resumes invocation limits using the saved arguments.
Its source is separately hashed and recorded as a versioned operating artifact.
All recovery events, actual reads and retained reservations remain in the store.
This is infrastructure reliability work; it does not establish trading or
research-policy improvement.

Executed commands:

```sh
ruff check src tests scripts/iris_mission_supervisor.py
ruff format --check src tests scripts/iris_mission_supervisor.py
mypy src/jarvis scripts/iris_mission_supervisor.py
pytest -q tests/test_iris_read_supervisor.py
pytest -q
```

Deployment uses private process/state arguments and preserves the active
kernel. No real source, model, market outcome, credential or internal server
path is included in the operating script or this acceptance report. See
[P14 recovery](P14-research-recovery.md) for the operating command and boundary.
