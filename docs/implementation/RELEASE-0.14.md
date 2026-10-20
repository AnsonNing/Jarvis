# Release 0.14 — Evidence-seeking research and reusable diagnostic skills

A narrow experiment stream could repeatedly adjust parameters before explaining
what changed in the observed records. This release gives the Director a complete
owner contract and a typed question/action loop, with actual baseline evidence and input metadata, so it can
inspect evidence, test
competing explanations, diagnose existing data, consult specialists or request a
missing capability before another backtest. Independent Critic review and the
existing experiment/admission/evaluator boundaries remain in place.

## Executed engineering acceptance

The initial complete local regression at `3678a77` passed **307 tests in 384.15 seconds**
(6 minutes 24 seconds), with no skips. The installed package is **0.14.0** and
dependency checks pass. Ruff lint/format and
mypy pass (122 formatted Python files and 92 typed source files). The 29 new
targeted tests passed in 8.22 seconds. Three public skill package self-tests also
passed individually. Existing Critic, reviewed Dream and multi-stage Research Lab
session CLI regressions closed with VERIFIED audits and zero external calls.
The new complete `workflow fixture` CLI reached VERIFIED, with four
initial actions and four successor actions. Real OS-isolated Python execution,
independent fixed fidelity checks, a local candidate Git commit and cross-session
skill adoption completed. The child experiment retained positive and negative
artificial results and separate proposal/result critiques. Registry setup costs remain separately receipted and visible in reports; no
measured full-cost efficiency claim is made. All pending owner
resource dimensions were zero after the successor finished. Exact replay returned
COMPLETE; an unseen branch returned INSUFFICIENT_COVERAGE with zero external calls.

`workflow preflight` validated the supplied checksum-pinned synthetic declaration
without creating a store. `workflow start` declared a READY session with zero
research decisions and zero LLM tokens; it did not start an LLM or submit a backtest.
Schema export, task validation and private notebook export also executed.

The fixture's role responses are scripted and token units simulated. Candidate
Python really executed behind the macOS sandbox; source writes, host sentinel
reads and network access were denied. This is engineering acceptance, not a real
LLM creativity test, a causal trading result or measured research-policy utility.
No Hermes study, Iris submission, model training or background automation started.

## Included changes

- P0: immutable full intent; append-only questions and competing falsifiable
  hypotheses; INSPECT, DIAGNOSE, EXPERIMENT, CONSULT, BUILD_SKILL and STOP actions;
  time-scoped evidence; owner-pinned complete experiment environment; separate
  Critic and known-answer correction receipts.
- P1: explicit snapshot/CSV lineage; unique stable-key matching; single-capture source fingerprints; exact Decimal PL
  decomposition including missing-ID residuals; available-row score distributions;
  insufficient-data outcomes instead of invented opportunity or Cancel evidence.
- P2: source/schema/domain/role/permission/budget manifests; checksum-pinned public
  builtin packages; receipted independent fidelity; immutable registry versions including revoked entries, explicit Builder revisions,
  historical-prefix audits, revocation and actual successor reuse.
- P3: separate tools-disabled coding role; trusted worktree writer; isolation probe
  before any Builder call; private candidate source/tests, fixed independent
  validator, local retained commit and newly pinned AgentVersion.
- Compatibility: old ResearchPrograms retain omitted-default schema identities;
  new reviewed experiments opt in to bounded known-invalid corrections. Unknown
  paid work never enters automatic response repair or blind resubmission.
- Operations: foreground bounded CLI; shared parent research budgets, same-owner
  resume, parent pause/cancel, idempotent successor recovery, offline audit/replay
  and private notebooks. Existing Dream/Research Lab workflows remain available.

## Limits and next formal use

The first generated protocol is paired attribution. A new trusted protocol,
new source permissions or new evaluator requires reviewed engineering. Self-tests
cannot admit their own implementation. General arbitrary skill acquisition is
not enabled. macOS has no verified hard address-space cap; Linux isolation needs
working bubblewrap namespaces and must pass its probe. No unsafe fallback exists.

Snapshot evidence is descriptive. Full opportunity, gate, Trading score, order,
Cancel, fill and position data are separately required to explain the full
execution chain. Full cost efficiency, generalization, causal improvement and
Dream promotion require independent evaluations; capability fidelity is not enough.
The old paused mission/evidence remains unchanged. Future formal research starts
from a new private owner declaration and accepted adapter/model/data package.

Commands, contracts, data requirements and the full pipeline are in
[P15 mechanism research](P15-mechanism-research.md).


## Reproduction commands

```sh
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
python -m pip check
ruff check src tests scripts/iris_mission_supervisor.py
ruff format --check src tests scripts/iris_mission_supervisor.py
mypy src/jarvis scripts/iris_mission_supervisor.py
pytest -q
pytest -q skills/inventory/test_skill.py
pytest -q skills/trade_attribution/test_skill.py
pytest -q skills/score_distribution/test_skill.py
jarvis workflow preflight examples/mechanism-research/declaration.yaml
jarvis workflow sandbox-check
jarvis --store .jarvis/mechanism-acceptance workflow fixture
jarvis schema local/schemas
jarvis task validate examples/research_task.example.yaml
```

Linux regression and a separate macOS isolated-skill job are part of CI. A platform
without usable isolation skips generated-execution tests explicitly; it must not
report a successful full skill-construction fixture. An unavailable Builder yields
INCOMPLETE, with retained observations and no claimed successor adoption.


## Framework Python compatibility follow-up

The first hosted macOS run found that framework CPython's `bin/python` is a
launcher which uses `posix_spawn` to execute `Python.app`. The deny-by-default
profile correctly blocked that second executable and the fixture returned
INCOMPLETE. Hosted targeted tests explicitly reported four isolation skips, so
neither their success nor executable presence was accepted as full P3 validation.

The sandbox now selects the actual framework interpreter directly when present,
otherwise the normal resolved interpreter. Its binary checksum is recorded.
General process-fork/spawn permission remains denied. A planning regression checks
that selection and the unchanged boundary; **30 focused tests passed in 9.06s**
locally, including actual isolation and the full workflow. Hosted acceptance still
requires a successful complete fixture, rather than treating unavailable isolation
as a passing skill-construction run.
