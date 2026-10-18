"""Artificial quote fixtures exercise execution assumptions, never real trading evidence."""

import json

import pytest
from pydantic import ValidationError

from jarvis.contracts import AdmissionError, JarvisError
from jarvis.exit_replay import (
    ExitReplayInput,
    ExitSettings,
    create_exit_job,
    replay_quotes,
    run_exit_job,
    verify_exit_job,
)
from jarvis.storage import Store


def request(side="long", prices=(100.0, 101.0, 99.0, 98.0)):
    return ExitReplayInput.model_validate(
        {
            "dataset_id": "artificial-quotes",
            "timestamp_unit": "monotonic_milliseconds_same_clock",
            "price_unit": "toy-price",
            "pnl_unit": "toy-pl",
            "provenance": "test fixture",
            "positions": [
                {
                    "episode_id": "a",
                    "side": side,
                    "fill_time_ms": 0,
                    "horizon_time_ms": 3000,
                    "fill_price": 100.0,
                    "quantity": 2,
                    "price_multiplier": 1,
                    "fill_semantics": "single_complete_fill",
                }
            ],
            "quotes": [
                {"episode_id": "a", "timestamp_ms": i * 1000, "bid": price, "ask": price + 0.1}
                for i, price in enumerate(prices)
            ],
        }
    )


def settings(**updates):
    return ExitSettings.model_validate(
        {
            "take_profit_bps": 50,
            "stop_loss_bps": 50,
            "latency_ms": 0,
            "slippage_bps": 0,
            "fee_per_unit_per_side": 0,
            "max_quote_gap_ms": 1000,
            "horizon_exit": "first_quote_at_or_after_horizon_plus_latency",
            **updates,
        }
    )


@pytest.mark.parametrize(
    "side,reason,price", [("long", "take_profit", 101.0), ("short", "stop_loss", 101.1)]
)
def test_bid_ask_exit_and_one_contribution(side, reason, price):
    value = replay_quotes(request(side), settings())
    assert value["result_origin"] == "SIMULATED"
    assert len(value["episodes"]) == 1
    assert value["episodes"][0]["reason"] == reason
    assert value["episodes"][0]["exit_price"] == price


def test_gap_latency_slippage_and_fees_use_observed_execution_quote():
    value = replay_quotes(
        request(prices=(100, 101, 99, 98, 98)),
        settings(latency_ms=1000, slippage_bps=10, fee_per_unit_per_side=0.25),
    )
    row = value["episodes"][0]
    assert row["trigger_time_ms"] == 1000
    assert row["exit_quote_time_ms"] == 2000
    assert row["exit_price"] == pytest.approx(99 * 0.999)
    assert value["net_pl"] == pytest.approx((99 * 0.999 - 100) * 2 - 1)
    assert value["net_pl"] < -2  # A triggered target does not guarantee a target fill.


def test_horizon_precedes_a_price_trigger_after_the_horizon():
    data = request(prices=(100, 100, 100, 110))
    p = data.positions[0].model_copy(update={"horizon_time_ms": 2500})
    value = replay_quotes(data.model_copy(update={"positions": (p,)}), settings())
    assert value["episodes"][0]["reason"] == "horizon"
    assert value["episodes"][0]["trigger_time_ms"] == 2500


@pytest.mark.parametrize("fault", ["sparse", "truncated", "duplicate", "orphan"])
def test_missing_and_ambiguous_paths_are_denied(fault):
    data = request()
    quotes = list(data.quotes)
    if fault == "sparse":
        quotes.pop(1)
    elif fault == "truncated":
        quotes.pop()
    elif fault == "duplicate":
        quotes.insert(1, quotes[0])
    else:
        quotes[0] = quotes[0].model_copy(update={"episode_id": "other"})
    with pytest.raises(AdmissionError):
        replay_quotes(data.model_copy(update={"quotes": tuple(quotes)}), settings())


def test_saved_pl_and_partial_fill_are_not_admissible():
    value = request().model_dump(mode="json")
    value["positions"][0]["entry_pl"] = 100
    with pytest.raises(ValidationError):
        ExitReplayInput.model_validate(value)
    del value["positions"][0]["entry_pl"]
    value["positions"][0]["fill_semantics"] = "partial"
    with pytest.raises(ValidationError):
        ExitReplayInput.model_validate(value)


def test_exit_receipt_adoption_and_zero_simulator_audit(tmp_path, monkeypatch):
    data = tmp_path / "input.json"
    policy = tmp_path / "settings.json"
    data.write_text(request().model_dump_json())
    policy.write_text(settings().model_dump_json())
    store = Store(tmp_path / "store")
    jid = create_exit_job(store, data, policy)
    with pytest.raises(JarvisError, match="receipt interruption"):
        run_exit_job(store, jid, fault="after_receipt")
    import jarvis.exit_replay as module

    monkeypatch.setattr(
        module, "replay_quotes", lambda *_: pytest.fail("re-executed settled operation")
    )
    ref = run_exit_job(store, jid)
    assert run_exit_job(store, jid) == ref
    assert verify_exit_job(store, jid)["external_calls"] == 0
    assert store.resource_totals(jid)["actual"]["tool_calls"] == 1
    assert not any(store.resource_totals(jid)["pending"].values())


def test_unknown_started_exit_is_not_resubmitted(tmp_path):
    data = tmp_path / "input.json"
    policy = tmp_path / "settings.json"
    data.write_text(request().model_dump_json())
    policy.write_text(settings().model_dump_json())
    store = Store(tmp_path / "store")
    jid = create_exit_job(store, data, policy)
    with pytest.raises(JarvisError, match="unknown exit"):
        run_exit_job(store, jid, fault="after_start")
    with pytest.raises(JarvisError, match="no automatic resubmission"):
        run_exit_job(store, jid)
    assert len(store.jobs(jid)) == 1


def test_missing_price_path_is_a_recorded_capability_outcome(tmp_path):
    data = tmp_path / "input.json"
    policy = tmp_path / "settings.json"
    value = request().model_dump(mode="json")
    value["quotes"].pop()
    data.write_text(json.dumps(value))
    policy.write_text(settings().model_dump_json())
    store = Store(tmp_path / "store")
    jid = create_exit_job(store, data, policy)
    result = store.artifacts.get(run_exit_job(store, jid))
    assert result["status"] == "REQUIRES_CAPABILITY"
    assert result["net_pl"] is None
    assert verify_exit_job(store, jid)["status"] == "VERIFIED"
