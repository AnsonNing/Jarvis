"""Declarative domain settings, explicit units, combinations and capability gates."""

import itertools
import math
from typing import Any, Literal

from pydantic import Field, model_validator

from jarvis.contracts import Record
from jarvis.identity import digest


class CancelCondition(Record):
    field: Literal["cancel_score", "order_age_ms", "spread_bps", "adverse_move_bps"]
    operator: Literal["gt", "ge", "lt", "le"]
    value: float


class CancelRule(Record):
    combine: Literal["all", "any"]
    conditions: tuple[CancelCondition, ...] = Field(min_length=1, max_length=8)


class SettingAxis(Record):
    name: Literal[
        "take_profit_bps", "stop_loss_bps", "trading_cutoff_delta", "cancel_mode", "cancel_rule"
    ]
    values: tuple[float | str | CancelRule, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def declared_units(self) -> "SettingAxis":
        if self.name == "cancel_mode":
            if any(v not in {"baseline", "disabled"} for v in self.values):
                raise ValueError("invalid cancellation mode")
        elif self.name == "cancel_rule":
            if any(not isinstance(v, CancelRule) for v in self.values):
                raise ValueError("cancel_rule requires a closed condition DSL")
        else:
            limit = 0.002 if self.name == "trading_cutoff_delta" else 10000
            lower = -limit if self.name == "trading_cutoff_delta" else 0
            if any(type(v) is not float or not lower <= v <= limit for v in self.values):
                raise ValueError("numeric setting outside declared score/basis-point range")
        if len(
            {digest(v.model_dump() if isinstance(v, CancelRule) else v) for v in self.values}
        ) != len(self.values):
            raise ValueError("duplicate axis values")
        return self


class SearchSpace(Record):
    kind: Literal["ResearchSearchSpace"] = "ResearchSearchSpace"
    axes: tuple[SettingAxis, ...] = Field(min_length=1, max_length=5)
    max_candidates: int = Field(default=128, ge=1, le=256, strict=True)
    comparison: Literal["single_axis", "factorial"] = "single_axis"
    origin: Literal["human_provided", "agent_proposed", "declared_fixture"] = "human_provided"

    @model_validator(mode="after")
    def bounded(self) -> "SearchSpace":
        if len({a.name for a in self.axes}) != len(self.axes):
            raise ValueError("duplicate setting axes")
        count = (
            sum(len(a.values) for a in self.axes)
            if self.comparison == "single_axis"
            else math.prod(len(a.values) for a in self.axes)
        )
        if count > self.max_candidates:
            raise ValueError("declared experiment matrix exceeds finite candidate limit")
        return self


REQUIRES = {
    "take_profit_bps": {"price_path", "fill_events", "exit_simulation"},
    "stop_loss_bps": {"price_path", "fill_events", "exit_simulation"},
    "cancel_rule": {"order_event_stream", "cancel_rule_execution"},
    "trading_cutoff_delta": {"frozen_threshold_comparison"},
    "cancel_mode": {"frozen_cancel_comparison"},
}


def enumerate_settings(space: SearchSpace, capabilities: set[str]) -> list[dict[str, Any]]:
    entries = [[(axis.name, v) for v in axis.values] for axis in space.axes]
    combinations = (
        itertools.product(*entries)
        if space.comparison == "factorial"
        else ((item,) for axis in entries for item in axis)
    )
    results = []
    for combination in combinations:
        settings = {
            key: value.model_dump(mode="json") if isinstance(value, CancelRule) else value
            for key, value in combination
        }
        needed = set().union(*(REQUIRES[key] for key in settings))
        if len(settings) > 1:
            needed.add("joint_parameter_comparison")
        missing = sorted(needed - capabilities)
        results.append(
            {
                "settings": settings,
                "candidate_id": digest(settings),
                "origin": space.origin,
                "status": "REQUIRES_CAPABILITY" if missing else "ADMITTED_FOR_DESIGN",
                "missing_capabilities": missing,
                "execution_status": "NOT_RUN",
            }
        )
    return results


def hermes_space() -> SearchSpace:
    return SearchSpace(
        axes=(
            SettingAxis(name="take_profit_bps", values=(10.0, 20.0, 40.0)),
            SettingAxis(name="stop_loss_bps", values=(5.0, 10.0, 20.0)),
            SettingAxis(name="cancel_mode", values=("baseline", "disabled")),
            SettingAxis(name="trading_cutoff_delta", values=(-0.0001, 0.0, 0.0001)),
        ),
        comparison="factorial",
        origin="declared_fixture",
    )
