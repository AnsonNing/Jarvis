# P22 — Actionable role repair and accurate finite stops

Status: VERIFIED_WITH_LIMITATIONS, 2026-10-28. Real research remains stopped.

## Diagnosis

The latest inspected private session stopped before its next experiment, with
substantial campaign budget remaining. Two provider processes ended at the
900-second call timeout without reported usage; a later Director proposal
exhausted its corrective attempts. The old per-decision counter combined these
three failures into a generic invalid-decision stop. No next backtest was
submitted. Provider-side reasons for the timeouts remain unknown.

Repair requests contained an answer hash and a generic validation message, but
not the rejected proposal itself. The model recreated existing hypothesis IDs
under a new question, then proposed already recorded baseline configurations.
The validator was right to reject those actions, but its feedback did not explain
how to preserve the intended experiment while reusing the existing question and
baseline evidence. A timeout during correction also lost that useful draft from
the next outer attempt.

An earlier native worker failure exposed a separate harness defect: resolving
`clang++` to `clang` before invocation changed driver behavior and dropped automatic
C++ runtime linking. The previous C-stdio-only fixture did not catch it. This
supersedes the earlier characterization in the P21 history follow-up that explicit
owner linker flags alone were needed. P21's already-compact transcript compatibility
fix is retained and included in regression coverage.

## Small changes to the current runner

- Seal each rejected answer as before, and show its actual bounded proposal plus
  structured validation details in the corrective context. Small proposals are
  exact; large code proposals are explicitly partial. Pydantic input/context
  objects are excluded from validation error feedback. No rejected proposal is
  relabelled as observed evidence or granted permission to execute.
- Name conflicting question/hypothesis IDs and their existing question catalog.
  A new branch may use an existing question with `question: null`. Duplicate
  backtest feedback identifies the exact configuration and only its currently
  visible result references. Only observations with the same evaluator and
  dataset block a matched experiment. Duplicate requests are still denied;
  neither the harness nor the model may silently repeat an observed baseline.
- Carry the latest rejected proposal across an ended-call retry at that same
  decision/review index. Request keys remain monotonic and each frozen request,
  receipt, reservation and charge remains durable.
- Count ended-provider failure waves separately from invalid-decision repair
  waves for Director and Critic. Three semantic waves stop; each wave allows at
  most two corrections. Ended-provider waves stop at the declared
  `provider_retries + 1` (three for legacy sessions). Known-usage transient calls
  retain their existing finite same-context transport retries; an exhausted
  transport wave also counts as provider failure. These are bounded nested
  allowances, not an unlimited retry loop. Budgets and role/context limits still
  take precedence. Unknown completion is never resubmitted.
- Record `PROVIDER_RETRY_EXHAUSTED` or `DECISION_REPAIR_EXHAUSTED`, failure counts
  and limits, and `scientific_conclusion: NOT_REACHED` in the partial stop. Critic
  context-envelope failures propagate immediately rather than becoming invalid
  judgment attempts. Terminal status stays terminal on another run.
- Hash and authorize the resolved compiler, but invoke the owner-declared C++
  driver alias. Seal compile/run argv, invocation/resolved paths and unchanged
  compiler identity. Prior sealed native receipts become hashed inputs when
  consumed by a later native run. Existing OS isolation and fixed owner flags
  remain authoritative.
- Expose reservation-based settlements separately from provider-reported usage
  in both the delivery JSON and Markdown. Explain that owner `resume` only
  changes control: it cannot clear a terminal status or change pinned source.
  Include the specific partial-stop event in the status issue view.

No service, scheduler, automatic successor, budget extension, evaluator change,
model change, permission broadening, or historical schema migration is added.
New feedback is additive event/receipt metadata; old records retain their identities.

The private surrounding monitor also had a latent early-finalization race: it
could observe `PROVIDER_UNAVAILABLE` during the runner's own bounded recovery and
exit monitoring while that runner was alive. Preserve its original script copy,
then require process exit before finalizing a terminal/error view (owner pause
still takes precedence). A separate 36-case synthetic predicate/syntax check
covers live/exited process, error/nonterminal state and owner pause combinations.
This is control-logic evidence only; the private monitor was never launched and
no live provider/monitor recovery was claimed. This race is distinct from the
observed invalid-decision stop.

## Synthetic acceptance

`tests/test_role_feedback.py` reproduces the production failure shapes with
scripted providers: two proved-ended timeouts, an exhausted invalid proposal
wave, reused hypothesis IDs, and a previously observed baseline. A single
`AutonomousResearch.run` call then repairs the actual drafts, executes native
STL/exception code, preserves a negative direction, compares the toy alternative,
passes its frozen confirmation, exports an audited package and leaves no pending
work. The retained acceptance uses one run invocation and no manual continuation.

Persistent invalid proposals, ended timeouts, charged timeouts and unknown
completion have separate negative controls. They demonstrate finite correct
stops and truthful partial delivery; another invocation cannot reset the counts
or dispatch new work. The Critic context test rejects before provider dispatch.
Native alias, prior-receipt input lineage, host-read/write/network/fork isolation,
legacy identity and long workspace-history regressions are also exercised.

All research roles and financial-style scores in these fixtures are synthetic.
Actual local native compilation, isolated program execution and file checks are
performed. There are zero real provider, Iris, training or Dream calls. Engineering
completion is not evidence of real research quality, trading improvement,
provider reliability, independent scientific validation or future model judgment.

Executed validation:

- Full regression: **423 passed in 712.65s**, exit code 0.
- Targeted recovery/native/planning/delivery round: **64 passed in 29.25s**;
  after adding the Critic context negative control, the role-feedback file alone
  passed **10 tests in 6.58s**. The final full run includes all those cases.
- Ruff lint passed; format check passed for **158 files**; mypy passed for
  **117 source modules**; `git diff --check` passed.
- A retained single-run synthetic acceptance completed **8 decisions**, `CLOSED`,
  `stage_gate: PASS`, audit `VERIFIED`, replay `COMPLETE`, zero external replay
  calls and zero pending jobs. Both exact repair codes were observed, the
  negative branch was preserved and no manual continuation was used.
- All **29 original journal databases** retained identical table counts and
  ordered content hashes after the full suite. The existing real session remains
  `NEEDS_ATTENTION`, with its runner and monitor absent. No control/status event
  was appended to resume it; no real backtest, training or operational Dream run
  occurred. Unrelated dirty work was retained and backed up before editing.
- The retained acceptance and final regression use source identity
  `sha256:ba7996ca6af1e4fd957a9ef1c5224ebf27b8daae1b0e52a686e3ba4f3fff83cf`.
  The branch HEAD remains `8b3f170a02d700d6c84f416c9ac2c0d65de0dff6` with local
  changes; no commit, push or deployment was performed.

```sh
.venv/bin/pytest -q tests/test_role_feedback.py tests/test_native_execution.py tests/test_runtime_controls.py tests/test_implementation_evidence.py tests/test_research_planning.py tests/test_trusted_research.py --disable-warnings --maxfail=1
.venv/bin/pytest -q --disable-warnings --maxfail=1
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
git diff --check
```

Private diagnosis, fault-injection acceptance and preservation fingerprints stay
in ignored local storage. The existing research journals were snapshotted before
editing and compared read-only afterward, including every original table's row
count and ordered content checksum. No real study is resumed by this work.
