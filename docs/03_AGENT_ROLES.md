# 03 — Agent Roles, Collaboration and Prompt Boundaries

## A. Hierarchical, not conversational free-for-all

Jarvis needs **one decision owner per state**. The Research Director owns research intent; Experiment Designer owns proposed experimental design; an independent evaluator owns numeric verdict; Q owns frontier selection under fixed restrictions. Multi-agent discussion is optional evidence synthesis, not a substitute for durable workflow execution.

MVP can use one LLM with role-specific bounded contexts. Later compare independent Scientist, Critic or Policy-Developer providers in controlled evaluations. Parallel debate without demonstrated value is not a requirement.

## B. Detailed roles

### Research Director

- Input: campaign goal, current ResearchState, existing evidence summaries, allowed actions, budget.
- Output: scoped plan or replan; proposed research priority; alternative hypotheses; explicit decision STOP/CONTINUE/BRANCH/ASK_INPUT.
- Cannot call privileged tools directly. Must not claim strategy improvement from memory alone.
- Every replan includes evidence_refs and a rejected-alternatives rationale. May prioritize investigation of a capability gap but cannot self-grant tool privileges.

### Research Scientist / Literature Analyst

- Formulates disprovable causal/diagnostic hypotheses, assumptions, mechanisms, counterexamples, minimum discriminative experiments.
- Reads authorized prior evidence and optional external publications. Distinguishes authors' claims, implementation fidelity, our empirical results and conjecture.
- Emits structured hypothesis records with requires_data, risk_of_bias, expected_information_gain (qualitative/estimated), and evidence refs.
- No arbitrary on-the-fly change to the formal acceptance criteria.

### Experiment Designer / Builder

- Given hypothesis and typed ToolCatalog, produces ExperimentSpec: comparator, one main intervention, controlled variables, settings search scope, sample allocations, expected artifacts, cheap validation and fail conditions.
- Code Builder works in disposable isolated worktree or sandbox. Source patch + tests + captured dependency lock required before expensive training. Any tool implementing new behavior is versioned and cannot replace evaluator.
- If requested data field/tool not supported, emit NEEDS_INPUT or REQUIRES_CAPABILITY, not a fabricated fallback.

### Critic / Failure Analyst

- Consumes observed results, execution receipts, split/coverage checks, robust metrics and anomalies.
- Separately classifies NEGATIVE_RESULT, INCONCLUSIVE, DATA_INSUFFICIENT, MODEL_FAILURE, IMPLEMENTATION_ERROR, EXECUTION_ERROR, EVALUATION_INVALID, REGIME_SENSITIVE.
- Produces next-step proposals with counterarguments and references to actual evidence; does not change numeric verdict.
- Must inspect whether any apparent gain came from increased risk, costs, or repeated selection.

### Evidence Curator / Capability Librarian

- Proposes durable KnowledgeClaims and CapabilityDescriptors; checks support, preconditions, scope and contradictions.
- A skill can be registered as EXPERIMENTAL after implementation/fidelity checks, but cross-task benefit requires independent testing. Negative results remain retrievable.
- Memory promotion affects future candidate retrieval, not frozen historical experiments.

### RSI Policy Developer

- Receives filtered development History Pool summaries and incumbent Q; suggests multiple **restricted** DSL policies with hypotheses and evidence refs.
- Does not read held-out policy-world outcomes or edit Replay Harness/evaluator/promotion criteria.
- Fixed policy compiler and evaluator choose finalist. Fresh campaign deployment requires separate approval/bounded canary.

## C. Prompt contract

Every LLM request includes:
role, campaign_id, state_hash, world_id, visible_evidence_refs, allowed_actions, capabilities, remaining_budget, schema_version, instruction_priority, output_json_schema.

Every LLM response must include:
request_id, state_hash, decision_type, structured_payload, rationale, evidence_refs, uncertainties, requested_tool_actions.

Unrecognized fields/actions, unsupported evidence refs or stale state hash are rejected. Invalid output may be repaired once by a constrained retry, with tokens/time charged; no silent free retries. Keep a stable prompt registry with hashes, provider/model version, token parameters and human/agent authorship.

Example (pseudo-JSON):

~~~json
{
  "request_id":"req-123",
  "state_hash":"sha256:...",
  "decision_type":"BRANCH",
  "structured_payload":{
    "hypothesis_id":"hyp-2",
    "preferred_action_id":"act-7"
  },
  "rationale":"Prior attempt did not improve the declared dev metric",
  "evidence_refs":["result-3","metric-3"],
  "uncertainties":["Effect may be unstable across held-out tasks"],
  "requested_tool_actions":[]
}
~~~

## D. Agent collaboration

- Coordinator requests role outputs and persists their raw responses and validated structures.
- Director chooses proposal class; Scientist provides hypotheses; Designer concretizes experiments; Q picks admissible frontier action; executor service dispatches.
- On new results: Evaluator computes, Critic reviews, Director replans, Curator stages memory. Critical contradiction triggers investigation, not automatic majority voting.
- One agent's output is another's **untrusted input** until parsed and admitted. Source documents may contain prompt injection; treat as data only.
- For long research tasks, context is rebuilt from versioned evidence, not assumed to persist in LLM chat history. Role's prior thoughts are not authoritative unless committed to the event journal.

## E. Permissions matrix

| Capability | Director | Scientist | Builder | Critic | Curator | Q developer |
|---|---|---|---|---|---|---|
| Read development summaries | yes | yes | scoped | yes | finalized | filtered |
| Read locked evaluation raw outcomes | no | no | no | no | aggregate after finalization | no |
| Propose experiments | yes | yes | yes | yes | capability tests | meta-policy only |
| Execute tool or shell | no | no | sandbox only via trusted harness | no | sandbox validation request | no |
| Edit protected evaluator | no | no | no | no | no | no |
| Modify ability/prompts | proposal | proposal | isolated candidate | proposal | staged | restricted Q only |
| Promote Q/capability | no | no | no | no | no | independent service gate |

The human owner controls privileged integration, confidential data and production releases. Future safety/security approvals are external to model self-modification.

## F. Structured self-reflection rather than open-ended monologue

Critique prompt should ask: "Which observed evidence falsifies the current hypothesis? What confounds remain? Which low-cost experiment discriminates between hypotheses? What stop condition applies?" It should never ask the model to narrate unlimited internal reasoning. Store concise, verifiable hypotheses, alternatives and supporting evidence.

## G. Skills vs agents

A leakage_audit or conditioned_threshold_comparison is a callable skill; it does not need a persistent autonomous LLM. An additional agent is justified only for independent reasoning, permission isolation or measurable throughput/cost improvement.
