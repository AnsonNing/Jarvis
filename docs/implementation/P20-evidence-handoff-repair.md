# P20 follow-up — analysis evidence reaches implementation

Status: VERIFIED_WITH_LIMITATIONS (engineering/synthetic regression, 2026-10-26).

A Director could cite a completed numerical analysis when requesting IMPLEMENT, but
its worker received only source/base files. The actual feature definition and
threshold tables were absent. An unchanged delivery then returned REJECTED without
its reason or the worker's explanation. Recovery also confused visible role receipts
with materializable ANALYZE inputs and paraphrased a fixed planning hypothesis.

The repair reuses existing action evidence_refs; persisted action schemas and
historical hashes do not change. IMPLEMENT receives its cited admissible records and
registered output tables of cited analyses. Expansion is restricted to the frozen
decision-time visible set; unregistered, hidden and later tables are excluded. The
workspace manifest preserves artifact/blob identities. Critic evidence carries those
same implementation inputs. Rejected deliveries expose structured rejection reasons,
summary, findings and finish check, without creating a candidate or treating a syntax
check as a behavioral test.

Director context now lists analyzable_evidence_ids and recovery instructions explain
the difference between a citation and an input record. ANALYZE checks visibility as
well as registration. Planning still preserves the original hypothesis exactly;
corrections identify the declared text instead of relaxing that guard. No new grants,
services, schemas, evaluator changes or dynamic permission edits were added. A
changed runtime requires a successor session with retained historical evidence.

Verification actually executed:

```sh
python -m pytest -q tests/test_implementation_evidence.py tests/test_research_planning.py tests/test_trusted_research.py
python -m pytest -q tests/test_implementation_evidence.py tests/test_autonomy.py tests/test_autonomy_adapters.py tests/test_planning_cli.py
ruff check src tests
ruff format --check src tests
mypy src
```

- First test set:34 passed in13.38s.
- Second set:39 passed in78.55s;3 tests overlap, covering70 unique tests in total.
- Ruff lint/format passed for150 files; mypy passed for112 source modules.
- New synthetic regressions run the actual isolated worker and validator: a rule uses
  a supplied definition; hidden/unregistered tables are excluded; no-edit rejection
  retains its cause and findings; Critic sees the same canonical table; hypothesis
  repair reports the original declared text.

Limitations: the complete repository suite was not rerun for this small repair.
Synthetic probes establish protocol behavior, not scientific correctness, profitable
rules, hidden engine behavior, or improved autonomous research quality. Existing
private research records are retained. No real backtest or Dream is needed to verify
this repair, and no baseline adoption or public commit/push is part of it.

Next action: continue the already-authorized private study under the new source
identity, supplying its preserved analysis tables and exact baseline observations.
Require a bounded real experiment before any performance conclusion.
