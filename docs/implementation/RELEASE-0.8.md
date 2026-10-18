# Release 0.8 preparation acceptance

Scope: read-only logical-case admission, explicit owner-selected development
baselines, preparation-only inventory, durable cancelled watchers and closed
numeric source decoding. No research launch is part of this release acceptance.

## Executed local evidence

`pytest -q`: **194 passed in 85.40s**. `ruff check src tests` and
`ruff format --check src tests` passed for 80 Python files. `mypy src/jarvis`
passed for 58 source modules. `git diff --check` passed. These local checks used
Python 3.12 and the pinned dependencies, with the isolated source on `PYTHONPATH`.
Synthetic tests prohibit process execution during readiness and verify
that its CLI never constructs an evidence store. They exercise source/model
drift, invalid replacements, retained unknown originals and stopped unstarted
watchers with zero calls. A development-use environment cannot be relabeled as
an independent heldout world. Linux CI separately installs the release and runs
the full test/CLI matrix after publication.

The supplied private model package passed four local baseline-import audits:
both directions over their integration and full development periods. Entry
counts, entry-only PL, daily realized drawdown and worst day matched the supplied
evaluations. Four local C++17 compile-only checks passed for the two baselines
and bounded heuristic changes. Private reports retain environment/input hashes,
requests, raw-ledger hashes, risk, source checks and local cost. No model weights,
private C++, numeric financial results or internal paths are published here.

All four prepared environments expose sixteen controls. An initial preparation
attempt rejected an existing source constant expressed as a literal product.
The closed decoder correction was then exercised by regression tests and the
four actual owner-source compile checks. The earlier rejection remains in the
private preparation record.

New remote submissions: **zero**. New LLM calls: **zero**. New research studies:
**zero**. Existing monitoring was stopped under direct owner instruction, with
history and unknown reservations retained. Preparation records have no execution
authorization. Upstream analysis remains `COMPLETED_WITH_FINDINGS`; its unresolved
runtime-versus-exported-input gap has not been declared fixed.

See [research readiness](P9-research-readiness.md) for commands, provenance,
acceptance boundaries and the baseline-first research sequence. Independent
heldout/fresh/canary data and real-domain policy improvement remain unexecuted.
