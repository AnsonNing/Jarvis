# Jarvis 0.1 acceptance receipt

Date: 2026-10-18. Status: VERIFIED_WITH_LIMITATIONS.
Implementation commit: `0de2cd3ef380fe91bd165b822932465ce30f4047`.
Runtime: Python 3.12.14; exact dependencies in requirements-lock.txt.
Source identity: `sha256:3abd1e57b556fe73e27e2e1f8ebb4c3234faee5fc40bc8bfb14707ef788ffeda`.

## Executed validation

| Command / workflow | Observed result |
| --- | --- |
| `python -m pytest -q` | 51 passed, including real process races and abrupt-exit receipt recovery |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 24 Python files formatted |
| `mypy src/jarvis` | No issues in 18 source modules |
| `python -m pip check` | No broken requirements |
| `jarvis task validate examples/research_task.example.yaml` | VALID |
| Synthetic CLI start → pause → resume → report → verify → replay | CLOSED; 5 development nodes; 6 charged tool invocations including formal evaluation |
| Real local Hermes LONG CLI workflow | CLOSED; 4 diagnostic nodes/invocations; protected inputs unchanged |
| Real local Hermes SHORT CLI workflow | CLOSED; 4 diagnostic nodes/invocations; protected inputs unchanged |
| Explicit LONG inference-only materialization | Current model metadata reproduced; 0 training runs; 0 event backtests; protected inputs unchanged |

The final three CLI acceptance campaigns pinned the implementation commit above
with `dirty=false`. JSON reports were sealed in content-addressed storage; readable
Markdown/JSON copies, campaign IDs, full costs, replay traces and verification
references are saved under the ignored local reports directory. No private
financial results or raw order rows are included in this public receipt.

Invocation counts are real call counts. `compute_units` means adapter invocations
in this release; it is not a GPU-hours or monetary estimate. Per-tool elapsed time
is measured; full campaign time includes orchestration, hashing and queue overhead
and is separately bounded by the campaign deadline. LLM usage was exactly zero.
Frozen-model materialization cost is in its own private provenance receipt.

## Scientific and operational limits

This release establishes an engineering research workflow and current-artifact
reproduction. It does not establish trading improvement, live execution fidelity,
capability transfer or Dream-RSI effectiveness. Hermes formal testing is NOT_RUN
because no new independent final cohort is configured.

Q0 and logical roles are deterministic. A mockable provider boundary exists;
external LLM providers, actual token metering, generated candidate code isolation,
real training jobs, external backtester jobs and remote lease reconciliation are
not yet delivered. Started jobs with unknown completion remain pending for
reconciliation rather than being retried automatically.

Recorded-path replay is a tested foundation. History Pool selection, sibling
branch reordering, policy-heldout/fresh-campaign studies and meta-policy promotion
remain P3/P4 work. Structured findings in a campaign are not a completed P2 memory
and capability system.

The release adds GitHub Actions for lint, formatting, typing, tests and a synthetic
CLI smoke. Its remote result is assessed separately from local validation.
