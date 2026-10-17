# ADR-012 — Evidence-bound LLM research turns

Date: 2026-10-18. Origin: the owner explicitly requested a real LLM research agent
for Hermes, autonomous hypothesis/experiment/analysis/revision, and continued main pushes.

Add a real provider through non-interactive Codex with strict JSON Schema. Reuse
local saved authentication without copying credentials or configuring it in public
CI. The local executable and model option are explicit private campaign inputs;
the executable version/hash and prompt version bind the provider identity. When
no model override is supplied, the installed CLI default is used and recorded as
such; exec JSON does not identify the resolved model in the observed runtime.

Agent calls have their own durable reservations, requests, raw receipts, usage
and rejection records. Report actual input/output tokens; cached input is already
part of input tokens. Reserve an estimated per-call ceiling before dispatch.
The CLI does not expose a verified hard server-side output limit: a measured
overrun remains charged and stops further dispatch, never disappears from cost.
Timeout/unknown completion or unavailable usage retains its reservation and
requires reconciliation. A completed invalid proposal may get one bounded repair.

The LLM receives only bounded aggregate development evidence, tool templates,
remaining resources and prior proposals. It produces a falsifiable hypothesis,
controlled experiment parameters, analysis and next direction. It cannot issue
shell commands, add tools, read locked data or alter the evaluator. Codex runs
ephemerally in read-only mode, with shell/plugins/apps/memory/multi-agent/hooks/web
disabled. Execution telemetry containing a tool attempt is rejected and charged.
This is a constrained reasoning integration, not arbitrary generated-code execution.

Hermes experiment tools compare frozen-model threshold/cancel decisions and
conditional aggregates under the declared dataset replay assumptions. The LLM
may vary one bounded intervention at a time. All candidate numeric values come
from adapters. Existing exposed periods remain development, with no automatic
promotion of any candidate and no claim of a new realistic execution backtest.

References: [official non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
