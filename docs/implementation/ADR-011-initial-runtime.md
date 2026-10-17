# ADR-011 — First executable release

Date: 2026-10-18. Origin: implementation decision based on the owner's supplied
specification and instruction to use the existing Hermes repository and data.

The planning branch is incorporated into the implementation history. The owner
authorizes incremental pushes to main; implementation happens in cohesive commits.

Start with Python 3.12, Pydantic, SQLite WAL, a deterministic synthetic adapter,
logical research roles and Q0. Generated code, arbitrary commands, external LLM
calls and live trading remain disabled. A mock or deterministic controller is
explicitly identified; it is not presented as an independently reasoning LLM.

The user has now supplied a local Hermes repository. Add an opt-in, read-only
adapter using explicit local configuration. Inspect data and reproduce existing
entry-level accounting, without copying private source, unpickling models,
retraining, submitting paid backtests, or modifying the upstream repository.
Private paths and outputs go into ignored local storage. Machine-readable
artifacts and source checksums determine the baseline if prose is stale.

Existing exposed evaluation periods are development/replay, never pristine tests.
This integration advances a narrow part of P5 without bypassing synthetic core
validation. Full P1 generated-code isolation and P2–P4 remain separate milestones.

Collect complete frontier, prior state, visible evidence, evaluator, source and
resource identities from the first action. An early exact-path replay primitive
may be delivered to validate those records; it does not establish Dream-RSI gains.
