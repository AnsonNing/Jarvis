# ADR-015 — Verified analysis delivery and explicit research stages

Status: implemented; synthetic acceptance verified; no real-domain efficacy claim.

New contracts may opt into `VerifiedAnalysisDelivery` and `ResearchStages`.
Absent optional fields retain legacy serialization and historical reporting. Existing
contracts, events and results are never migrated or overwritten in place.

Numerical analyses must deliver existing outputs and an executable regeneration
script. The trusted harness reruns it in a clean isolated workspace before accepting
the worker's finish. Failed delivery returns a concrete correction to the same worker
and workspace; every attempt and resource receipt remains recorded. A successful
rerun proves repeatability, not scientific correctness or causal identification.

A bounded review bundle resolves recorded input/output references into checksummed
previews, lineage, scripts, execution receipts and reproducibility checks. It labels
missing evidence explicitly. The bundle never grants additional tools or reads future
evidence. Its immutable reference is pinned into the review context.

Research stages explicitly declare screening, selection and optional confirmation
periods. Screening cannot select the task finalist. The selection winner's exact
candidate/settings are frozen before confirmation; no subsequent selection tuning
is admitted in that session. Every required confirmation period must contain a matched
baseline and passing observation under the same acceptance profile. Exposed-data
confirmation remains development evidence, never independent validation or permission
for live adoption. Fixed evaluators and acceptance rules remain unchanged.
