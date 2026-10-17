# 06 — Research Memory, Skill Library and Capability RSI

## 1. Principles

Jarvis must accumulate **evidence-backed reusable ability**, not chat transcript trivia. Memory helps recommend actions; evidence is always retrieved from a durable artifact and interpreted within its validation scope. Semantic embeddings are optional retrieval indexes, not truth stores.

Four stores serve distinct purposes:

| Store | Unit | Query |
|---|---|---|
| Event/Experiment ledger | actions, actual outcomes, cost | What was done, with which code and settings? |
| Knowledge store | claims, assumptions, failures, contradictions | What is supported/unsupported, and where? |
| Capability registry | verified callable implementations and methods | Which tool can be used now under these conditions? |
| Meta-research history | discovery worlds, decisions, Q policies | Which research direction has worked efficiently? |

Keep raw evidence immutable; derived summaries/embeddings can be recomputed.

## 2. Schema: research finding

Required fields: finding_id, canonical claim, evidence_refs, counter_evidence_refs, source (human/agent/external), domain scope, task family, method signature, data assumptions, evaluator scope, sample/period, effect measurement, uncertainty, validity status, contraindications, applicable_context_predicates, observed transfer tests, supersedes / contradicted_by references, created and updated timestamp.

Statuses: PROPOSED, TESTED_NEGATIVE, INCONCLUSIVE, SUPPORTED_IN_SCOPE, REPLICATED, CONTRADICTED, DEPRECATED. An experimental finding does not become global knowledge because the system repeated a paraphrase.

Example:

~~~yaml
schema_version: 1
kind: KnowledgeClaim
finding_id: synthetic-claim-42
claim: The diagnostic tool detects a known fixture error class.
scope:
  domain: synthetic
  task_family: toy-search
  data_identity: synthetic-v1
evidence_refs: [eval-010, test-report-011]
counter_evidence_refs: []
applicability:
  require_capabilities: [synthetic.inspect]
  require_schema: [1]
limitations: [No evidence from financial datasets]
status: SUPPORTED_IN_SCOPE
author: agent
~~~

## 3. Retrieval pipeline

1. Start from current task, available inputs, decision-time information, scope and permissions.
2. Apply **hard structured filters**: tenant, domain/tool version, dataset family, validation status, required feature availability, embargo/locked test access, current capability support.
3. Rank candidates by task relevance, empirical strength, recency, cost/benefit, novelty and explicit contradictions. Vector search can improve recall **after** admission.
4. Return references with exact underlying citations, scope, negative results and unknowns, not a contextless "best method".
5. Record which memories were retrieved, presented and actually used in the research decision; not everything retrieved was consumed.

Separate **memory available** from **memory used** so cross-task transfer experiments can measure actual reuse.

## 4. Capability record and promotion

A capability is a versioned callable tool, library, model interface, analysis procedure, research tactic or validated playbook, with typed inputs/outputs and execution/fidelity requirements.

Lifecycle:

~~~text
IDEA → IMPLEMENTED → FIDELITY_TESTED → EXPERIMENTAL
                                      │
                         DOMAIN_VALIDATED
                                      │
                       CROSS_TASK_VALIDATED
                                      │
                       APPROVED_FOR_RESEARCH
                                      │
                         DEPRECATED / REVOKED
~~~

If implementation fails, keep NEEDS_IMPLEMENTATION. If missing data blocks validation, keep NEEDS_INPUT. Report VALIDATED only with corresponding scope. Approval for research is not approval to run production trading.

Descriptor fields: name/version, owner/author, task/problem types, implementation/commit/artifact hashes, typed parameters, permissions, dependency lock, proof-of-origin, available tooling, preconditions/contraindications, separate fidelity tests and effectiveness tests, independent benchmarks, expected costs, successful/failed cases, last verified runtime, rollback version and license attribution.

## 5. Capability internalization protocol

**Research-to-Capability** (aligned conceptually with the existing robotics RSI project):

1. **Gap discovery:** concrete failed task observation or missing tool; distinguish software missing from data insufficiency, unsupported domain and model misspecification.
2. **Source acquisition:** agent/human-provided paper/repository/library; record URI, retrieval date, version, author and license. Treat content as untrusted and retain rights constraints.
3. **Method proposal:** expected mechanism, minimal reproduction test, target integration, assumptions and cost.
4. **Fidelity check:** reproduce a well-defined numerical/test case from the source or independent expected behavior; report partial or missing reproduction.
5. **Scoped task study:** compare existing system vs added capability under matched budget and frozen evaluator, use untouched test where justified.
6. **Promotion and routing:** if independently supported, register typed skill, emit a concise usage card ("use when / do not use when"); snapshot availability for future agents.
7. **Fresh session transfer:** verify a new agent session actually discovers and uses the capability appropriately, without being given the solution.
8. **Cross-task transfer:** compare with/without-capability under equal budgets on multiple new task families. Record regressions and contraindications.

Technical success (new code compiles) is not scientific success (helps research), and scientific benefit on one task is not generalization.

## 6. Negative knowledge and contradictions

Store failed studies with:
failure category, attempted method+version, valid scope, measured results, cost, hypothesis refuted or merely untested, and suggested conditions to revisit. A missing GPU allocation or broken dependency is NOT evidence that a method is ineffective.

Contradictions become explicit edges in knowledge graph: claim A vs claim B with differentiating context (market regime, task family, target, evaluation horizon, tool version). Prefer investigation of contextual differences over deletion of older claims.

## 7. Memory versioning and anti-contamination

Each campaign pins MemorySnapshotID. The agent may write provisional findings during a campaign, but cannot retroactively change what a previous decision was allowed to see. Commit curated memory after experiment closure or by append-only observation with sequence number. Query index rebuilding must not alter semantics.

Formal locked-test evidence cannot leak through memory summaries into ongoing candidate selection. A claim derived from locked results is tagged restricted and only available in later, newly versioned tasks consistent with test-reuse policy.

## 8. Transfer experiments

Compare three groups on new family/cohort at matched research budgets:

- A: fresh no accumulated memory/assets.
- B: memory/validated capabilities with fixed Q0.
- C: B plus independently selected new Q (Dream-RSI).

Measure valid research quality, cost-to-target, memory retrieval precision, meaningful reuse rate, repeated-failure avoidance, false-positive claims, and transfer regressions. Use multiple independent worlds; repeats of same strategy/time window do not increase effective independent families.

This design isolates whether benefits arise from **what Jarvis knows** or **how it chooses to explore**.

## 9. Storage roadmap

MVP: SQLite tables for claims, artifacts, provenance, capability versions and indexes; human-readable manifests in git only for safe generic skills. Later optional Postgres and full-text/vector index if retrieval scale justifies. Large model weights, datasets, confidential strategies and run outputs remain in external private artifact storage. Access controls must apply before retrieval and again before prompt construction.
