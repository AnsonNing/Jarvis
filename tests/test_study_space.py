import pytest
from pydantic import ValidationError

from jarvis.contracts import JarvisError
from jarvis.dream import DreamEngine
from jarvis.exploration import incumbent
from jarvis.pool import PoolBuilder
from jarvis.search_space import (
    CancelCondition,
    CancelRule,
    SearchSpace,
    SettingAxis,
    enumerate_settings,
    hermes_space,
)
from jarvis.storage import Store
from jarvis.study import PolicyRegistry, Study


def test_three_round_loop_gap_repair_plateau_and_actual_canary_rollback(tmp_path):
    store = Store(tmp_path)
    study = Study(store)
    sid = study.create()
    with pytest.raises(JarvisError, match="recorded round"):
        study.run(sid, fault="after_round")
    ref = study.run(sid)
    report = store.artifacts.get(ref)
    assert [r["status"] for r in report["rounds"]] == [
        "NO_PROMOTION",
        "APPROVED_FOR_RESEARCH",
        "NO_PROMOTION",
    ]
    assert report["rounds"][0]["pool_ref"] != report["rounds"][1]["pool_ref"]
    assert report["rounds"][1]["pool_ref"] == report["rounds"][2]["pool_ref"]
    assert store.artifacts.get(report["canary_ref"])["safe"] is False
    assert report["final_active_policy_id"] == incumbent().identity
    assert PolicyRegistry(store).active(report["registry_id"])[0].identity == incumbent().identity
    assert study.run(sid) == ref


def test_previously_opened_validation_family_cannot_be_reused(tmp_path):
    store = Store(tmp_path)
    builder = PoolBuilder(store)
    ref = builder.run(builder.create())
    engine = DreamEngine(store)
    a = store.artifacts.get(engine.run(engine.create(ref)))
    assert a["status"] == "APPROVED_FOR_RESEARCH"
    b = store.artifacts.get(engine.run(engine.create(ref)))
    assert b["status"] == "NO_PROMOTION"
    assert b["heldout"] is None
    assert "already exposed" in b["reason"]


def test_settings_matrix_and_real_backend_capability_boundaries():
    space = hermes_space()
    values = enumerate_settings(space, {"frozen_threshold_comparison", "frozen_cancel_comparison"})
    assert len(values) == 54
    assert all(v["status"] == "REQUIRES_CAPABILITY" for v in values)
    assert all("price_path" in v["missing_capabilities"] for v in values)
    assert all(v["execution_status"] == "NOT_RUN" for v in values)
    bounded = SearchSpace(axes=(SettingAxis(name="cancel_mode", values=("baseline", "disabled")),))
    assert all(
        v["status"] == "ADMITTED_FOR_DESIGN"
        for v in enumerate_settings(bounded, {"frozen_cancel_comparison"})
    )


def test_cancel_dsl_and_experiment_space_deny_code_and_unbounded_sweep():
    rule = CancelRule(
        combine="all",
        conditions=(CancelCondition(field="order_age_ms", operator="gt", value=500.0),),
    )
    value = enumerate_settings(
        SearchSpace(axes=(SettingAxis(name="cancel_rule", values=(rule,)),)), set()
    )
    assert value[0]["missing_capabilities"] == ["cancel_rule_execution", "order_event_stream"]
    with pytest.raises(ValidationError):
        CancelCondition(field="__import__('os')", operator="gt", value=1)
    with pytest.raises(ValidationError):
        SearchSpace(axes=hermes_space().axes, comparison="factorial", max_candidates=5)
    with pytest.raises(ValidationError):
        SettingAxis(name="trading_cutoff_delta", values=(1.0,))
