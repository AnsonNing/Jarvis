# 04 — Typed Contracts, Persisted Schemas and APIs

All schemas below are **design contracts v1**. Codex should implement typed Python models, machine-checked JSON Schema, serialization tests and versioned migrations. Omit or mark genuinely unavailable values UNKNOWN; never fill with fabricated observations. Every persisted type has a schema_version (e.g. 1), stable type discriminator, canonical ID, created_at UTC and explicit provenance.

## A. Key identifiers

- WorldID = SHA-256 of canonical WorldSpec, including domain+adapter+source artifact identities, initial conditions, evaluator/split IDs, available tools and budget policy. The world is immutable.
- TaskID = canonical versioned research request with goals/constraints, optional parent revision.
- CampaignID = unique execution instance of TaskID in WorldID with Q ID and execution budget. Preserve both stable content identity and unique instance ID.
- AgentVersionID = hash of prompt registry, model/provider config, capability registry snapshot, relevant agent assets and code commit.
- ExperimentID = unique declared intervention + parent and tool version + data/split IDs + settings; job attempts are separate.
- ArtifactID = content hash + media type + schema version; larger binaries live outside git.
- DecisionID, EvidenceID, NodeID, EvaluationID and PolicyID are separate immutable references. IDs in prompts must reference authorized values only.

Canonicalization: deterministic UTF-8 JSON encoding, normalized keys, explicit number policy (reject NaN, Infinity, negative budgets), normalized timestamp UTC, content hashes for referenced files. Do not use raw floating point strings as equality checks; record exact serialized configuration and source hashes.

## B. ResearchTask v1 (public example is synthetic)

~~~yaml
schema_version: 1
kind: ResearchTask
task_id: synthetic-search-v1
domain: synthetic
goal: improve_declared_objective
hypotheses_allowed: [parameter_change, diagnostic_analysis]
primary_metric: objective_score
metric_direction: maximize
secondary_metrics: [computation_cost]
acceptance:
  minimum_primary_improvement: 0.01
  allow_no_improvement: true
split_policy:
  development_id: synthetic-dev-v1
  selection_id: synthetic-selection-v1
  locked_test_id: synthetic-test-v1
budget:
  max_actions: 20
  max_tool_calls: 30
  max_llm_tokens: 200000
  max_wall_seconds: 14400
  max_compute_units: 40
  max_parallel_jobs: 2
permissions:
  allow_generated_code: false
  allow_external_search: false
  allow_live_execution: false
world_ref: synthetic-world-v1
parent_task_id: null
~~~

Fields and acceptance are frozen at campaign creation; threshold change creates new TaskID. All dimensions are optional only if the registered domain has a documented supported default; no silent "best" metric substitution.

## C. WorldSpec v1

~~~json
{
  "schema_version":1,
  "kind":"WorldSpec",
  "world_id":"computed-on-seal",
  "domain":"synthetic",
  "adapter":{"name":"synthetic-optimizer","version":"1.0.0","source_sha256":"..."},
  "environment":{"runtime_lock_sha256":"...","tools":["inspect","evaluate"]},
  "dataset":{"family_id":"toy-family-A","identity":"sha256:...","split_manifest_sha256":"..."},
  "evaluator":{"name":"toy-evaluator","version":"1.0.0","source_sha256":"...","metric_contract_sha256":"..."},
  "initial_state":{"baseline_artifact_id":"...","random_seed":100},
  "budgets":{"max_compute_units":40},
  "scope":"synthetic_validation_only",
  "origin":"declared_fixture"
}
~~~

A World is not merely an experiment's dataset. It also defines source and evaluation semantics. Adapter compatibility must be checked before cross-world replay. Never mutate a World to add test results; append evidence to the campaign ledger.

## D. Hypothesis v1

Required: hypothesis_id, claim, mechanism, assumptions, input_evidence_refs, necessary_observables, falsification_criteria, expected_discriminating_experiment, expected_cost_range (estimate only), status (PROPOSED/TESTED/UNRESOLVED/REFUTED/SUPPORTED_WITH_SCOPE), author provenance and created-in-state-hash.

An ExperimentSpec must reference at least one Hypothesis. "Try bigger model" without mechanism or testable comparator is not a sufficiently specified hypothesis unless deliberately marked exploratory.

## E. ResearchState and ActionSpec v1

~~~json
{
  "schema_version":1,
  "kind":"ResearchState",
  "campaign_id":"camp-001",
  "world_id":"world-001",
  "sequence":7,
  "state_hash":"sha256:...",
  "stage":"SEARCHING",
  "parent_node_id":"node-003",
  "visible_evidence_ids":["ev-001","ev-006"],
  "visible_sibling_node_ids":["node-002"],
  "best_development_candidate_id":"cand-001",
  "remaining_budget":{"tool_calls":10,"compute_units":15},
  "frontier_id":"frontier-007",
  "available_actions":["action-11","action-12"],
  "selected_policy_id":"Q0-v1"
}
~~~

~~~json
{
  "schema_version":1,
  "kind":"ActionSpec",
  "action_id":"action-11",
  "action_type":"RUN_EXPERIMENT",
  "parent_node_id":"node-003",
  "hypothesis_id":"hyp-002",
  "experiment_id":"exp-011",
  "tool_id":"synthetic.evaluate:v1",
  "arguments":{"variant":"B","seed":101},
  "preconditions":["development_split_only"],
  "estimated_cost":{"compute_units_min":1,"compute_units_max":3},
  "required_capabilities":["synthetic.evaluate"],
  "allowed_split_ids":["synthetic-dev-v1"],
  "evidence_refs":["ev-006"],
  "origin":"agent_proposed"
}
~~~

Action types (initial whitelist): INSPECT, DIAGNOSE, PROPOSE_HYPOTHESIS, RUN_EXPERIMENT, COMPARE, BRANCH, REFINE, REQUEST_CAPABILITY, READ_SOURCE, ASK_INPUT, FREEZE_CANDIDATE, STOP. Meta Q selects among **already-admitted frontier IDs**, not raw commands. Real tool arguments must pass per-adapter strict schema and admission.

## F. ExperimentSpec and ToolResult v1

ExperimentSpec: experiment_id, parent_candidate_id, candidate_source_commit/artifacts, hypothesis_ids, declared intervention, comparator_id, controlled variables, data/split identity, build/test steps, training/evaluation jobs, expected result assets, seeds, allowed search scope, required capability IDs, cost ceiling, fail/stop conditions, dependencies, origin and freeze time.

ToolRequest: job_id, idempotency_key, attempt_id, tool_id/version, WorldID, ExperimentID, workspace/commit, parameter artifact ref, budget reservation, sandbox profile, input hashes, caller identity, read scope.

ToolResult: job_id, attempt_id, typed status, started/completed timestamps, evidence_ids, result artifacts/hashes, actual compute vector, backend stdout/stderr refs, version receipt, error class, retriable flag, result_origin (OBSERVED / REPLAYED / ESTIMATED / SIMULATED / UNKNOWN), and visibility sequence. Result values need units and metric semantic IDs.

Only trusted workers can produce OBSERVED results. Replay emits REPLAYED with original evidence refs and **does not** turn it into a fresh observation.

## G. Event journal and DiscoveryNode

Event envelope (append-only, monotonic per campaign):

~~~json
{
  "schema_version":1,
  "kind":"JarvisEvent",
  "event_id":"evt-00017",
  "campaign_id":"camp-001",
  "sequence":17,
  "event_type":"TOOL_RESULT_RECORDED",
  "actor":"trusted-executor",
  "occurred_at_utc":"2026-10-18T00:00:00Z",
  "recorded_at_utc":"2026-10-18T00:00:02Z",
  "payload_artifact_id":"sha256:...",
  "world_id":"sha256:...",
  "previous_event_hash":"sha256:...",
  "event_hash":"sha256:..."
}
~~~

DiscoveryNode materialized from events: node_id, parent_id, chosen_action_id, prior_state_hash, visible_evidence_ids and sibling_ids, chosen policy ID, declared frontier, candidate version, lifecycle status, exact tool receipt IDs, observed metrics, evaluator/metric contract identity, cost vector (including failures), terminal decision, source and timestamps. Do not attach later-discovered test metrics as if visible in the prior state. Corrections use new append-only events, never destructive rewrite.

Graph validation: rooted, acyclic, parent precedes child, evidence referenced exists and is visible at decision, no dangling result ID, no unauthorized formal outcome, artifact hashes verify, no reused attempt IDs across distinct jobs.

## H. Evaluation v1

~~~json
{
  "schema_version":1,
  "kind":"EvaluationResult",
  "evaluation_id":"eval-101",
  "scope":"development",
  "candidate_id":"cand-001",
  "baseline_id":"cand-base",
  "world_id":"sha256:...",
  "evaluator_id":"eval-contract-v1",
  "split_manifest_id":"synthetic-dev-v1",
  "primary":{"name":"objective_score","candidate":0.7,"baseline":0.6,"direction":"maximize","unit":"score"},
  "secondary":{"compute_units":{"candidate":15,"baseline":12}},
  "validity":{"input_consistency":true,"coverage_complete":true},
  "acceptance":"PASS",
  "evidence_refs":["ev-010"],
  "limitations":[]
}
~~~

Distinct acceptance statuses: PASS, FAIL, INCONCLUSIVE, INVALID_EVALUATION, INSUFFICIENT_COVERAGE, NOT_RUN. A numerical PASS on synthetic development data is not a live-trading approval.

## I. Research decision response

Response schema requires request ID, expected state hash, action ID drawn from frontier, optional branch IDs, reasons, existing evidence refs and uncertainties; rejection codes STALE_DECISION, UNKNOWN_ACTION, OVER_BUDGET, WRONG_SPLIT, EVIDENCE_NOT_VISIBLE, INVALID_SCHEMA, UNSUPPORTED_CAPABILITY. Record rejection attempts and LLM costs.

## J. Memory and capability contracts

KnowledgeClaim: claim ID, proposition, supporting and contradictory evidence IDs, scopes/domains, dataset family, sample window, method, confidence/strength category, applicability predicates, unknowns, origin, first_seen and last_validated, lifecycle.

CapabilityDescriptor: name/version, implementation artifact hash, callable interface/parameter schema, dependencies, preconditions, required permissions, validation suite identities, validation scope, lineage, examples, limitations, regression tests, available_to_roles and promotion status.

ResearchPolicy: policy ID, immutable DSL source, compiler version/hash, allowed action set, applicability scope, ancestor policy, development/heldout evaluation refs, lifecycle and provenance.

## K. Required interface protocols (Python pseudotypes)

~~~python
class DomainAdapter(Protocol):
    def describe(self) -> DomainCapabilities: ...
    def validate_world(self, world: WorldSpec) -> AdmissionResult: ...
    def build_frontier(self, state: ResearchState) -> list[ActionSpec]: ...
    def execute(self, request: ToolRequest) -> ToolResult: ...

class EvaluationService(Protocol):
    def evaluate(self, result_refs: list[str], contract_id: str) -> EvaluationResult: ...

class ResearchPolicy(Protocol):
    def choose(self, visible_state: ResearchState, frontier: list[ActionSpec]) -> PolicyDecision: ...

class ReplayEngine(Protocol):
    def replay(self, history_pool_id: str, policy_id: str, budget: Budget) -> ReplaySummary: ...
~~~

Provider implementations must not fetch privileged results directly. Enforce access through typed visibility-aware gateway.

## L. Revisions and compatibility

Keep jsonschema files or Pydantic generated schemas versioned and pinned. Unknown schema version must fail closed. Backfill old World schemas with explicit migration provenance; never silently change scientific meaning. Unit conversions, label semantics and evaluator replacement create new adapter/evaluator IDs. Provide roundtrip serialization and unknown-field rejection tests.
