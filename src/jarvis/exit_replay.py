"""Explicit tick-quote exit simulation for a single complete fill per episode.

No saved outcome/entry PL enters this calculation. Missing quote/fill semantics
reject admission. Results are simulations under declared assumptions, not fills.
"""

import time
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from jarvis.contracts import AdmissionError, Budget, Cost, IntegrityError, JarvisError, Record
from jarvis.identity import digest, file_digest
from jarvis.storage import Store

Millis = Annotated[int, Field(ge=0, strict=True)]
Positive = Annotated[float, Field(gt=0)]


class FilledPosition(Record):
    episode_id: str = Field(min_length=1)
    side: Literal["long", "short"]
    fill_time_ms: Millis
    horizon_time_ms: Millis
    fill_price: Positive
    quantity: Positive
    price_multiplier: Positive
    fill_semantics: Literal["single_complete_fill"]


class QuoteTick(Record):
    episode_id: str
    timestamp_ms: Millis
    bid: Positive
    ask: Positive

    @model_validator(mode="after")
    def spread(self) -> "QuoteTick":
        if self.bid > self.ask:
            raise ValueError("crossed quote")
        return self


class ExitReplayInput(Record):
    kind: Literal["ExitReplayInput"] = "ExitReplayInput"
    dataset_id: str = Field(min_length=1)
    timestamp_unit: Literal["monotonic_milliseconds_same_clock"]
    price_unit: str = Field(min_length=1)
    pnl_unit: str = Field(min_length=1)
    positions: tuple[FilledPosition, ...] = Field(min_length=1, max_length=10000)
    quotes: tuple[QuoteTick, ...] = Field(min_length=1, max_length=100000)
    provenance: str = Field(min_length=1)


class ExitSettings(Record):
    kind: Literal["ExitSettings"] = "ExitSettings"
    take_profit_bps: Annotated[float, Field(gt=0, le=10000)] | None
    stop_loss_bps: Annotated[float, Field(gt=0, le=10000)] | None
    latency_ms: Annotated[int, Field(ge=0, le=10000, strict=True)]
    slippage_bps: Annotated[float, Field(ge=0, le=1000)]
    fee_per_unit_per_side: Annotated[float, Field(ge=0)]
    max_quote_gap_ms: Annotated[int, Field(ge=1, le=60000, strict=True)]
    horizon_exit: Literal["first_quote_at_or_after_horizon_plus_latency"]

    @model_validator(mode="after")
    def trigger(self) -> "ExitSettings":
        if self.take_profit_bps is None and self.stop_loss_bps is None:
            raise ValueError("declare at least one exit trigger")
        return self


def replay_quotes(data: ExitReplayInput, settings: ExitSettings) -> dict[str, Any]:
    positions = {p.episode_id: p for p in data.positions}
    if len(positions) != len(data.positions):
        raise AdmissionError("duplicate filled episode")
    grouped: dict[str, list[QuoteTick]] = {key: [] for key in positions}
    for quote in data.quotes:
        if quote.episode_id not in positions:
            raise AdmissionError("orphan quote")
        grouped[quote.episode_id].append(quote)
    output: list[dict[str, Any]] = []
    for key, position in positions.items():
        quotes = grouped[key]
        times = [q.timestamp_ms for q in quotes]
        if (
            position.horizon_time_ms < position.fill_time_ms
            or not times
            or times != sorted(set(times))
        ):
            raise AdmissionError("invalid quote ordering or fill/horizon lineage")
        end = position.horizon_time_ms + settings.latency_ms
        if times[0] > position.fill_time_ms or times[-1] < end:
            raise AdmissionError(
                "REQUIRES_CAPABILITY: quote path must cover fill through horizon plus latency"
            )
        # Include the first usable quote at/after the end. An artificial endpoint
        # would hide an arbitrarily long gap before actual horizon execution.
        coverage_end = next(t for t in times if t >= end)
        relevant = [q for q in quotes if position.fill_time_ms <= q.timestamp_ms <= coverage_end]
        check_times = [position.fill_time_ms, *[q.timestamp_ms for q in relevant]]
        if any(
            b - a > settings.max_quote_gap_ms
            for a, b in zip(check_times, check_times[1:], strict=False)
        ):
            raise AdmissionError("REQUIRES_CAPABILITY: quote path is too sparse")
        sign = 1 if position.side == "long" else -1
        trigger_time = None
        reason = None
        chosen = None
        for quote in quotes:
            if quote.timestamp_ms < position.fill_time_ms:
                continue
            price = quote.bid if sign == 1 else quote.ask
            move_bps = sign * (price / position.fill_price - 1) * 10000
            if trigger_time is None:
                if quote.timestamp_ms >= position.horizon_time_ms:
                    trigger_time, reason = position.horizon_time_ms, "horizon"
                elif settings.stop_loss_bps is not None and move_bps <= -settings.stop_loss_bps:
                    trigger_time, reason = quote.timestamp_ms, "stop_loss"
                elif settings.take_profit_bps is not None and move_bps >= settings.take_profit_bps:
                    trigger_time, reason = quote.timestamp_ms, "take_profit"
            if (
                trigger_time is not None
                and quote.timestamp_ms >= trigger_time + settings.latency_ms
            ):
                chosen = quote
                break
        if chosen is None:
            raise AdmissionError(
                "REQUIRES_CAPABILITY: no executable quote after the trigger latency"
            )
        observed = chosen.bid if sign == 1 else chosen.ask
        price = observed * (1 - sign * settings.slippage_bps / 10000)
        gross = sign * (price - position.fill_price) * position.quantity * position.price_multiplier
        fees = 2 * settings.fee_per_unit_per_side * position.quantity
        output.append(
            {
                "episode_id": key,
                "reason": reason,
                "trigger_time_ms": trigger_time,
                "exit_quote_time_ms": chosen.timestamp_ms,
                "exit_price": price,
                "gross_pl": gross,
                "fees": fees,
                "net_pl": gross - fees,
            }
        )
    return {
        "kind": "ExitReplayResult",
        "scope": "single_complete_fill_tick_quote_simulation",
        "result_origin": "SIMULATED",
        "dataset_id": data.dataset_id,
        "settings": settings.model_dump(mode="json"),
        "pnl_unit": data.pnl_unit,
        "episodes": output,
        "net_pl": sum(r["net_pl"] for r in output),
        "limitations": [
            "Observed quotes with declared hypothetical execution; no order acknowledgement or liquidity/queue model",
            "Only single complete fills admitted; partial/rejected/cancelled orders need another backend",
            "First eligible quote including gaps, adverse slippage and explicit per-unit fees; no clipped source PL",
        ],
    }


def create_exit_job(store: Store, input_path: Path, settings_path: Path) -> str:
    if input_path.stat().st_size > 32 * 1024 * 1024 or settings_path.stat().st_size > 65536:
        raise AdmissionError("exit input exceeds the declared local request limit")
    data = ExitReplayInput.model_validate_json(input_path.read_bytes())
    settings = ExitSettings.model_validate_json(settings_path.read_bytes())
    # Schema admission precedes the job. Missing path coverage is a recorded outcome.
    return store.create(
        {
            "kind": "ExitReplayJob",
            "world_id": digest(
                {"data": data.model_dump(mode="json"), "settings": settings.model_dump(mode="json")}
            ),
            "task": {
                "budget": Budget(
                    max_actions=1, max_tool_calls=1, max_compute_units=1, max_wall_seconds=60
                ).model_dump()
            },
            "input_ref": store.artifacts.put(data),
            "settings_ref": store.artifacts.put(settings),
            "runner_id": file_digest(Path(__file__)),
        }
    )


def run_exit_job(store: Store, campaign: str, *, fault: str | None = None) -> str:
    with store.campaign_lock(campaign):
        manifest = store.manifest(campaign)
        if manifest["kind"] != "ExitReplayJob" or manifest["runner_id"] != file_digest(
            Path(__file__)
        ):
            raise IntegrityError("exit runner identity changed")
        done = [e for e in store.events(campaign) if e["event_type"] == "EXIT_REPLAY_COMPLETED"]
        if done:
            ref = str(done[0]["payload"]["result_ref"])
            store.artifacts.get(ref)
            return ref
        jobs = store.jobs(campaign)
        if jobs:
            job = jobs[0]
        else:
            jid = store.reserve(
                campaign,
                {
                    "world_id": manifest["world_id"],
                    "input_ref": manifest["input_ref"],
                    "settings_ref": manifest["settings_ref"],
                },
                Cost(actions=1, tool_calls=1, compute_units=1, wall_seconds=30),
            )
            job = next(j for j in store.jobs(campaign) if j["id"] == jid)
        receipt = store.read_receipt(job["id"])
        if receipt is None:
            if job["status"] != "QUEUED":
                store.set_status(campaign, "NEEDS_RECONCILIATION")
                raise JarvisError("unknown exit replay job; no automatic resubmission")
            store.start_job(job["id"])
            if fault == "after_start":
                raise JarvisError("injected unknown exit operation")
            started = time.monotonic()
            try:
                result = replay_quotes(
                    ExitReplayInput.model_validate(store.artifacts.get(manifest["input_ref"])),
                    ExitSettings.model_validate(store.artifacts.get(manifest["settings_ref"])),
                )
            except AdmissionError as exc:
                result = {
                    "kind": "ExitReplayResult",
                    "status": "REQUIRES_CAPABILITY",
                    "reason": str(exc),
                    "net_pl": None,
                }
            receipt = {
                "job_id": job["id"],
                "request_ref": job["request_ref"],
                "world_id": manifest["world_id"],
                "result_ref": store.artifacts.put(result),
                "cost": Cost(
                    actions=1,
                    tool_calls=1,
                    compute_units=1,
                    wall_seconds=time.monotonic() - started,
                ).model_dump(),
            }
            store.write_receipt(job["id"], receipt)
            if fault == "after_receipt":
                raise JarvisError("injected exit receipt interruption")
        store.finish_auxiliary_job(
            job["id"], "EXIT_REPLAY_COMPLETED", {"result_ref": receipt["result_ref"]}
        )
        store.set_status(campaign, "CLOSED")
        return str(receipt["result_ref"])


def verify_exit_job(store: Store, campaign: str) -> dict[str, Any]:
    """Receipt-only audit: does not execute the simulator or read source inputs."""
    manifest = store.manifest(campaign)
    if manifest["kind"] != "ExitReplayJob":
        raise IntegrityError("not an exit replay job")
    data = store.artifacts.get(manifest["input_ref"])
    settings = store.artifacts.get(manifest["settings_ref"])
    if manifest["world_id"] != digest({"data": data, "settings": settings}):
        raise IntegrityError("exit input/settings identity changed")
    jobs = store.jobs(campaign)
    done = [
        e["payload"] for e in store.events(campaign) if e["event_type"] == "EXIT_REPLAY_COMPLETED"
    ]
    if len(jobs) != 1 or len(done) != 1 or not jobs[0]["finalized"]:
        raise IntegrityError("exit completion/receipt missing or duplicated")
    job = jobs[0]
    receipt = store.artifacts.get(job["receipt_ref"])
    request = store.artifacts.get(job["request_ref"])
    if (
        request != {k: manifest[k] for k in ("world_id", "input_ref", "settings_ref")}
        or receipt["job_id"] != job["id"]
        or receipt["request_ref"] != job["request_ref"]
        or receipt["world_id"] != manifest["world_id"]
        or done[0]["result_ref"] != receipt["result_ref"]
        or Cost.model_validate_json(job["actual"]) != Cost.model_validate(receipt["cost"])
    ):
        raise IntegrityError("exit receipt lineage mismatch")
    store.artifacts.get(receipt["result_ref"])
    return {
        "status": "VERIFIED",
        "job_id": campaign,
        "result_ref": receipt["result_ref"],
        "external_calls": 0,
        "resource_totals": store.resource_totals(campaign),
    }
