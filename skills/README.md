# Jarvis research skills

These packages describe human-provided, deterministic research methods. They are
application skills, separate from personal Codex `SKILL.md` instructions.

Each package contains `CARD.md`, `manifest.yaml`, `implementation.py` and
`test_skill.py`. The package manifest pins all three source files. Installation
checks checksums, selects a **closed trusted builtin**, runs receipted fixed
fidelity cases and produces a content-addressed `ResearchSkillManifest` with IO
schema references, source provenance, roles, permissions and budget. It does not
import arbitrary Python from a package. Python wrappers make the method reviewable.

```sh
jarvis --store .jarvis/example workflow skill-list REGISTRY_ID
jarvis --store .jarvis/example workflow skill-install REGISTRY_ID skills/trade_attribution
```

The public examples use the `artificial` domain. A private domain requires an
explicit private package declaration; package permissions cannot enable network,
host-file reads, backtests or evaluator changes. A changed source or domain must
use a distinct immutable name/version in a registry.

Generated skill packages use the same four-file shape, with a fully resolved
`ResearchSkillManifest` in `manifest.yaml` (JSON syntax is valid YAML). The Builder
stores them privately, tests them behind an OS boundary, compares outputs with a
trusted validator, and commits passing candidates into a local Git ref. Only a
new `SkillAgentVersion` may adopt a newly registered skill. Current generated-code
validation supports the fixed paired-attribution protocol; a new protocol requires
trusted engineering and separate validation. A passing self-test is insufficient.

Fidelity proves bounded implementation correctness. Utility requires independent,
same-budget research comparisons and prospective use; registration does not claim
utility, causal discovery, financial gain or Dream policy improvement.
