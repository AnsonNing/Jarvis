# P21 follow-up — previously compacted workspace histories

Status: VERIFIED_WITH_LIMITATIONS, 2026-10-28.

The workspace runner already summarizes steps older than eight turns as
`{step, note, tool}`. The context envelope's second compaction pass assumed every
transcript entry still contained a nested `request` and `observation`, causing a
KeyError on a longer real worker history. Preserve those already compact entries
verbatim; continue compacting full recent entries without changing evidence,
tool permissions, candidate code, evaluator or budget semantics.

The affected research run was quiescent with known settled receipts before this
small runtime correction. Its history remains immutable. A new source identity
is required for continuation. An unrelated owner toolchain declaration also needs
explicit C++ runtime linkage when a compiler symlink resolves to the C driver;
an STL/exception synthetic probe covers that declaration separately.

Executed checks:

```sh
ruff check src/jarvis/runtime_controls.py tests/test_runtime_controls.py
ruff format src/jarvis/runtime_controls.py tests/test_runtime_controls.py
mypy src
python -m pytest -q tests/test_runtime_controls.py tests/test_native_execution.py tests/test_implementation_evidence.py tests/test_trusted_research.py tests/test_research_planning.py --disable-warnings --maxfail=1
```

Result: **54 passed in 21.24s**, exit code 0; mypy passed for 116 modules and Ruff
passed. The regression builds a long history mixing already compact entries and
large full entries, preserves all step identities and the task, and enforces the
context byte cap. The preceding full-suite result of 411 tests belongs to the P21
base source; the entire suite was not repeated for this focused compatibility fix.
Synthetic checks establish execution behavior, not research or trading quality.
