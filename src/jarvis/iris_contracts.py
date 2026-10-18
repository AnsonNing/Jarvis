"""Versioned remote research contracts; all strategy bindings live in private config."""

from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from jarvis.contracts import Budget, Record
from jarvis.iris_design import IrisBinding as IrisBinding
from jarvis.iris_design import IrisRule

Family = Literal["entry", "cancel", "timing", "heuristic", "exit", "joint", "baseline"]


class IrisSettings(Record):
    take_profit_bps: Annotated[float, Field(gt=0, le=1000)] | None = None
    stop_loss_bps: Annotated[float, Field(gt=0, le=1000)] | None = None
    heuristic_cancel_bps: Annotated[float, Field(ge=0, le=1000)] | None = None
    trading_cutoff_delta: Annotated[float, Field(ge=-0.002, le=0.002)] | None = None
    cancel_risk_delta: Annotated[float, Field(ge=-0.2, le=0.2)] | None = None
    sampling_ms: Annotated[int, Field(ge=10, le=5000, strict=True)] | None = None
    bypass_heuristic_cancel: bool | None = None
    bypass_cancel_model: bool | None = None
    entry_rule: IrisRule | None = None
    cancel_rule: IrisRule | None = None
    session_start_ms: Annotated[int, Field(ge=0, le=86399999, strict=True)] | None = None
    session_end_ms: Annotated[int, Field(ge=0, le=86399999, strict=True)] | None = None
    flat_time_ms: Annotated[int, Field(ge=0, le=86399999, strict=True)] | None = None
    hang_volume_ratio: Annotated[float, Field(gt=0, le=1)] | None = None
    max_pending_notional: Annotated[float, Field(gt=0, le=100000000)] | None = None
    max_position_notional: Annotated[float, Field(gt=0, le=100000000)] | None = None

    def changes(self) -> dict[str, object]:
        return self.model_dump(exclude={"schema_version"}, exclude_none=True)


class IrisPeriod(Record):
    start: date
    end: date
    trading_dates: tuple[date, ...] = Field(min_length=1)
    exposure: Literal["development_exposed", "sealed_unseen"] = "development_exposed"
    group_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def calendar(self) -> "IrisPeriod":
        if self.end < self.start or tuple(sorted(set(self.trading_dates))) != self.trading_dates:
            raise ValueError("invalid period or duplicate/unsorted market calendar")
        if any(not self.start <= d <= self.end for d in self.trading_dates):
            raise ValueError("market date outside period")
        return self

    @property
    def request_period(self) -> str:
        return self.start.strftime("%Y/%m/%d") + "-" + self.end.strftime("%Y/%m/%d")


class IrisEnvironment(Record):
    kind: Literal["IrisEnvironment"] = "IrisEnvironment"
    root: str
    python: str
    branch: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    template: str
    template_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    evaluator: str
    evaluator_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    agent_script_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    model_lineage: str = Field(min_length=1)
    accepted_inputs: dict[str, str] = Field(min_length=1)
    bindings: tuple[IrisBinding, ...] = Field(min_length=1, max_length=20)
    design_profile: str | None = None
    period: IrisPeriod
    side: Literal["long", "short", "both"]
    origin: Literal["owner_provided"] = "owner_provided"
    result_origin: Literal["OBSERVED_IRIS_BACKTEST", "SIMULATED_FIXTURE"] = "OBSERVED_IRIS_BACKTEST"

    @model_validator(mode="after")
    def binding_names(self) -> "IrisEnvironment":
        names = [b.setting for b in self.bindings]
        if len(set(names)) != len(names) or len(
            {(b.region, b.symbol) for b in self.bindings}
        ) != len(names):
            raise ValueError("duplicate setting or symbol binding")
        if not set(names) <= set(IrisSettings.model_fields) - {"schema_version"}:
            raise ValueError("unrecognized setting")
        return self


class IrisAuthorization(Record):
    """Owner-approved finite submission envelope, separate from LLM authority."""

    kind: Literal["IrisAuthorization"] = "IrisAuthorization"
    environment_id: str
    max_submissions: Annotated[int, Field(ge=1, le=32, strict=True)]
    expires_at: datetime | None
    owner_note: str = Field(min_length=1)

    @model_validator(mode="after")
    def timezone(self) -> "IrisAuthorization":
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("authorization expiry needs a timezone")
        return self


class IrisExperiment(Record):
    kind: Literal["IrisExperiment"] = "IrisExperiment"
    label: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    family: Family
    hypothesis: str = Field(min_length=1, max_length=1600)
    falsification: str = Field(min_length=1, max_length=1600)
    settings: IrisSettings = IrisSettings()
    evidence_refs: tuple[str, ...] = ()
    origin: Literal["human_provided", "agent_proposed", "declared_fixture"]

    @model_validator(mode="after")
    def intervention(self) -> "IrisExperiment":
        n = len(self.settings.changes())
        if (self.family == "baseline" and n) or (self.family != "baseline" and not n):
            raise ValueError("baseline has no changes; candidates require an intervention")
        if n > 3 or (self.family != "joint" and n > 1):
            raise ValueError("single-factor or explicitly joint experiment required")
        families = {
            "entry": {
                "trading_cutoff_delta",
                "entry_rule",
                "hang_volume_ratio",
                "max_pending_notional",
                "max_position_notional",
            },
            "cancel": {"cancel_risk_delta", "bypass_cancel_model", "cancel_rule"},
            "timing": {"sampling_ms", "session_start_ms", "session_end_ms", "flat_time_ms"},
            "heuristic": {"heuristic_cancel_bps", "bypass_heuristic_cancel"},
            "exit": {"take_profit_bps", "stop_loss_bps"},
            "baseline": set(),
        }
        if self.family == "joint":
            if n < 2:
                raise ValueError("joint intervention requires at least two settings")
        elif not set(self.settings.changes()) <= families[self.family]:
            raise ValueError("intervention family does not match the changed setting")
        return self


class IrisBaselineProof(Record):
    """Completed local result whose source, calendar and ledger are reverified."""

    manifest: str
    label: str
    raw: str
    raw_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class IrisQueueSpec(Record):
    kind: Literal["IrisQueueSpec"] = "IrisQueueSpec"
    goal: str = Field(min_length=1, max_length=3000)
    environment: IrisEnvironment
    authorization: IrisAuthorization | None = None
    baseline: IrisBaselineProof | None = None
    max_tasks: Annotated[int, Field(ge=1, le=64, strict=True)] = 16
    budget: Budget = Budget(
        max_actions=64,
        max_tool_calls=32,
        max_llm_tokens=400000,
        max_compute_units=64,
        max_wall_seconds=7200,
        max_parallel_jobs=2,
    )
    primary_metric: Literal["entry_only_pl"] = "entry_only_pl"
    risk_policy: Literal["record_all_no_hidden_penalty"] = "record_all_no_hidden_penalty"
    prior_queues: tuple[str, ...] = Field(default=(), max_length=64)
    operating_owner: str | None = None


def read_environment(path: Path) -> IrisEnvironment:
    return IrisEnvironment.model_validate_json(path.read_bytes())
