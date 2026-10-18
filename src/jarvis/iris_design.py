"""Closed strategy-rule DSL and owner-pinned private C++ integration profiles.

Only scalar thresholds, signal IDs and all/any are proposed by the agent.
Source anchors, signal expressions and compilation scaffolding are supplied by
the trusted integration owner, hashed into the environment and never prompted.
"""

import math
import operator
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.contracts import AdmissionError, Record


class IrisBinding(Record):
    setting: str = Field(pattern=r"^[a-z_]+$")
    symbol: str = Field(pattern=r"^[A-Za-z_][A-Za-z_0-9]*$")
    region: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    syntax: Literal["double", "int", "macro_bool", "clock"]
    scale: float = Field(default=1, gt=0)
    mode: Literal["absolute", "delta"] = "absolute"
    minimum: float | None = None
    maximum: float | None = None
    units: str = "strategy_source_units"


class IrisPredicate(Record):
    signal: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    op: Literal["gt", "ge", "lt", "le"]
    value: float


class IrisRule(Record):
    combine: Literal["all", "any"]
    predicates: tuple[IrisPredicate, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique(self) -> "IrisRule":
        if len({(p.signal, p.op, p.value) for p in self.predicates}) != len(self.predicates):
            raise ValueError("duplicate rule predicate")
        return self


class IrisSignalBinding(Record):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    expression: str = Field(min_length=1)
    description: str = Field(min_length=1)
    units: str = Field(min_length=1)
    minimum: float
    maximum: float
    availability: Literal["entry_decision", "cancel_decision"]

    @model_validator(mode="after")
    def bounds(self) -> "IrisSignalBinding":
        if self.maximum < self.minimum:
            raise ValueError("signal bounds reversed")
        return self


class IrisRuleBinding(Record):
    setting: Literal["entry_rule", "cancel_rule"]
    region: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    anchor: str = Field(min_length=1)
    replacement: str = Field(min_length=1)
    signals: tuple[IrisSignalBinding, ...] = Field(min_length=1, max_length=8)
    preserved_invariants: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def closed(self) -> "IrisRuleBinding":
        if self.replacement.count("{{rule}}") != 1:
            raise ValueError("owner rule template requires exactly one rule placeholder")
        if "// @" in self.anchor or "// @" in self.replacement:
            raise ValueError("rule bindings cannot modify region markers")
        if len({s.key for s in self.signals}) != len(self.signals):
            raise ValueError("duplicate signal binding")
        stage = "entry_decision" if self.setting == "entry_rule" else "cancel_decision"
        if any(s.availability != stage for s in self.signals):
            raise ValueError("rule signal is unavailable at this decision stage")
        return self


class IrisBuildProfile(Record):
    compiler: str
    # Each chunk is either a region name or an absolute owner-provided file.
    chunks: tuple[str, ...] = Field(min_length=1, max_length=20)


class IrisDesignProfile(Record):
    kind: Literal["IrisDesignProfile"] = "IrisDesignProfile"
    rules: tuple[IrisRuleBinding, ...] = Field(default=(), max_length=2)
    scalar_bindings: tuple[IrisBinding, ...] = Field(default=(), max_length=12)
    build: IrisBuildProfile
    origin: Literal["owner_provided"] = "owner_provided"

    @model_validator(mode="after")
    def settings(self) -> "IrisDesignProfile":
        if len({b.setting for b in self.rules}) != len(self.rules):
            raise ValueError("duplicate rule integration")
        return self


def read_profile(env: Any) -> IrisDesignProfile | None:
    from jarvis.identity import file_digest

    if env.design_profile is None:
        return None
    path = Path(env.design_profile)
    if env.accepted_inputs.get(str(path)) != file_digest(path):
        raise AdmissionError("design profile is not pinned to accepted inputs")
    profile = IrisDesignProfile.model_validate_json(path.read_bytes())
    required = [profile.build.compiler, *[c for c in profile.build.chunks if Path(c).is_absolute()]]
    if any(env.accepted_inputs.get(p) != file_digest(Path(p)) for p in required):
        raise AdmissionError("design compiler/scaffolding is not pinned")
    return profile


OPS = {"gt": operator.gt, "ge": operator.ge, "lt": operator.lt, "le": operator.le}
CPP_OPS = {"gt": ">", "ge": ">=", "lt": "<", "le": "<="}


def evaluate_rule(rule: IrisRule, values: dict[str, float]) -> bool:
    """Reference semantics for fixtures; missing/nonfinite signals never trigger."""
    outcomes = [
        p.signal in values
        and math.isfinite(values[p.signal])
        and OPS[p.op](values[p.signal], p.value)
        for p in rule.predicates
    ]
    return all(outcomes) if rule.combine == "all" else any(outcomes)


def compile_rule(rule: IrisRule, binding: IrisRuleBinding) -> str:
    available = {s.key: s for s in binding.signals}
    expressions = []
    for p in sorted(rule.predicates, key=lambda p: (p.signal, p.op, p.value)):
        signal = available.get(p.signal)
        if signal is None:
            raise AdmissionError("REQUIRES_CAPABILITY: unavailable rule signal " + p.signal)
        if not signal.minimum <= p.value <= signal.maximum:
            raise AdmissionError("rule threshold violates declared signal units/bounds")
        value = format(0.0 if p.value == 0 else p.value, ".17g")
        x = "(" + signal.expression + ")"
        expressions.append(f"(std::isfinite({x}) && {x} {CPP_OPS[p.op]} {value})")
    return "(" + (" && " if rule.combine == "all" else " || ").join(expressions) + ")"


def region_bodies(text: str) -> dict[str, str]:
    return {
        m[1]: m[2] for m in re.finditer(r"^// @start:(\w+)\n(.*?)^// @end:\1", text, re.M | re.S)
    }


def render_rules(env: Any, text: str, settings: Any) -> str:
    requested = {
        k: getattr(settings, k) for k in ["entry_rule", "cancel_rule"] if getattr(settings, k)
    }
    if not requested:
        return text
    profile = read_profile(env)
    for name, rule in requested.items():
        binding = next((b for b in profile.rules if b.setting == name), None) if profile else None
        if binding is None:
            raise AdmissionError("REQUIRES_CAPABILITY: unbound strategy rule " + name)
        body = region_bodies(text).get(binding.region, "")
        if body.count(binding.anchor) != 1:
            raise AdmissionError("rule anchor must match exactly once in its writable region")
        replacement = binding.replacement.replace("{{rule}}", compile_rule(rule, binding))
        new_body = body.replace(binding.anchor, replacement, 1)
        start = text.index("\n", text.index("// @start:" + binding.region)) + 1
        text = text[:start] + new_body + text[start + len(body) :]
    return text


def design_capabilities(env: Any) -> list[dict[str, Any]]:
    profile = read_profile(env)
    return (
        [
            {
                "setting": b.setting,
                "combine": ["all", "any"],
                "predicates_maximum": 4,
                "signals": [
                    s.model_dump(exclude={"expression", "schema_version"}) for s in b.signals
                ],
                "preserved_invariants": b.preserved_invariants,
            }
            for b in profile.rules
        ]
        if profile
        else []
    )


def compile_candidate(env: Any, text: str) -> dict[str, Any]:
    """Compile-only; never executes strategy output or arbitrary agent commands."""
    profile = read_profile(env)
    if profile is None:
        raise AdmissionError("compiled strategy design requires an owner integration profile")
    regions = region_bodies(text)
    chunks = []
    for chunk in profile.build.chunks:
        if Path(chunk).is_absolute():
            chunks.append(Path(chunk).read_text())
        elif chunk in regions:
            chunks.append(regions[chunk].replace("#pragma once", ""))
        else:
            raise AdmissionError("missing declared compilation region")
    try:
        ran = subprocess.run(
            [profile.build.compiler, "-std=c++17", "-fsyntax-only", "-x", "c++", "-"],
            input="\n".join(chunks),
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )
        return {
            "status": "PASS" if ran.returncode == 0 else "FAIL",
            "exit_code": ran.returncode,
            "diagnostic": ran.stderr[:2000],
            "execution": "COMPILE_ONLY_NO_STRATEGY_EXECUTION",
        }
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "reason": "compiler_timeout", "execution": "COMPILE_ONLY"}
