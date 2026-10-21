# P17 — Real-domain executors: Iris region backtests, owner training, C++ rule checks

P16 verified autonomous research on a synthetic world. P17 adds the generic executors
needed to run the same loop on a real strategy kept outside this repository (Hermes on
Iris): every candidate is the pinned strategy source with whole regions replaced and
bound constants changed. The private adapter that declares Hermes lives with the
private data and is never committed here.

## Iris region backtester

`IrisRegionBacktester` (in `autonomy_adapters.py`) renders a candidate as:

1. the owner-pinned template (hash checked);
2. whole region bodies from the candidate's model files and strategy patches
   (`<region>.region`); markers and protected text must stay byte-identical;
3. `constants.json` from a trained model (e.g. its trading cutoff) set in `config`;
4. research settings through declared `RegionSetting` bindings
   (`double` with a unit scale, `int`, or `macro_bool`), exactly one literal per binding.

One `IrisEnvironment` is pinned per contract period, so each period has its exact
market calendar. Submissions go through the existing private Iris worker. A
`SubmissionLedger` records every upload before it happens and enforces an owner daily
cap (`QUOTA_GUARD`); `submission.json` is written before upload so a restarted run only
collects by request id. Results register two tables: `trades` (the raw Iris rows) and
`raw_source` (a pointer with hash, commit, calendar and settings) that `TRAIN_MODEL`
accepts as training data. Drawdown is reported as a positive magnitude.

A private adapter may also reuse earlier identical submissions (same candidate bytes and
period) by evaluating their saved raw output locally — baselines then cost no quota.

## Owner training entry

`CommandTrainer` runs the owner's training script inside the workbench and reads
`outputs/` + `result.json`. `CommandSpec.mode = "interpreter"` execs the pinned
interpreter directly so the owner pipeline may invoke the declared compiler toolchain
(for example an HPP C++/Python parity probe), and `AnalysisEnvironment.allow_exec_in_workdir`
lets it run binaries it compiled inside its own work directory. Still no network, no
credentials, no writes outside the work directory. Source hashes of the entry point are
checked on every run; a `pipeline_patch` produced by `IMPLEMENT` is placed first on the
import path.

## C++ region checks before any quota is spent

`RegionPatchValidator(harness=...)` lets trusted owner code turn the full candidate text
into a translation unit (engine API stubs + config + model regions + edited regions);
`check` then compiles it with `clang++ -fsyntax-only` inside the workbench. Errors return
to the agentic worker, which fixes and re-validates. Engine members the stubs do not
model fail here and need a one-day Iris smoke instead.

## Providers

- `CodexProvider` (existing), `ClaudeCodeProvider` (headless `claude -p --json-schema`,
  all tools disabled; requires a logged-in CLI or `JARVIS_CLAUDE_EXECUTABLE`), and
  `RelayProvider` (each request is answered through files; estimated token units).
- Usage-limit refusals from either CLI pause the session (`PROVIDER_UNAVAILABLE`).

## Declarations

`autonomy start` accepts `adapter_path` so a private adapter module outside the repository
can be imported. A session pins the Jarvis source identity and the executor identity;
editing Jarvis or the private adapter during a run stops it with
"research runtime changed" — declare a new session after code changes.
