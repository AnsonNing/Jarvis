# ADR-013 — Exact branching synthetic Dream-RSI

Date: 2026-10-18. Origin: owner requested the complete synthetic history,
candidate-policy, independent-validation and subsequent Dream-RSI workflow.

Keep the existing campaign and v1 replay formats readable. Add a separately
versioned branching collector using the same immutable artifacts, durable jobs,
budget ledger and append-only journal. Execute each sibling from its immutable
parent; its decision state contains ancestors only. A realized edge is reusable
only at that exact state. Missing edges return unknown, without an estimated score.

Predeclare at least twelve complete trees across six distinct mathematical problem
families: three policy-development families and three policy-heldout families.
Variants within one family are correlated and never split across cohorts. Fresh
validation uses additional families and newly executed paired campaigns. The
fixture equations and gates are fixed before examining any policy comparison.

Use a bounded, closed policy DSL with trusted observation predicates. Include the
incumbent unchanged. Candidate generation sees development summaries only; freeze
one finalist before opening heldout outcomes. Refuse incomplete replay, enforce
the same path budget, preserve negative results, and require both independent
replay and fresh paired execution before research-only promotion. A fresh
regression leaves the incumbent active; subsequent canary regression can roll back.

The default synthetic developer enumerates declared policy fixtures and labels
their origin accordingly. An optional real Codex developer returns the same
restricted schema with actual token receipts and no access to heldout artifacts.
Replay never invokes either developer, domain tools or numerical evaluator.

L2 memory/capability learning is held constant (empty) in both arms, so differences
are attributable to L3 exploration. Persistent capability learning and arbitrary
generated-code execution remain distinct packages; neither is required to test
this isolated L3 loop. Synthetic results do not establish Hermes transfer,
financial improvement or general statistical efficacy.
