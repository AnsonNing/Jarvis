"""Trusted, replayable selection/confirmation gates for exposed research data."""

from typing import Any

from jarvis.autonomy_contracts import AutonomyContract, BacktestAction, setting_dict
from jarvis.candidates import compare_results, select_finalist
from jarvis.identity import digest
from jarvis.storage import Store


def configuration(
    store: Store, contract: AutonomyContract, ref: str, settings: dict[str, Any]
) -> str:
    candidate = store.artifacts.get(ref)
    full = {
        **{s.name: s.baseline for s in contract.settings},
        **candidate["settings"],
        **settings,
    }
    # Normalize the declared numeric types; 30 and 30.0 are the same setting.
    for spec in contract.settings:
        if spec.name in full and spec.kind in {"int", "float"}:
            full[spec.name] = float(full[spec.name])
    return digest({"candidate_ref": ref, "settings": full})


def frozen_candidate(store: Store, owner: str) -> dict[str, Any] | None:
    events = [
        e["payload"]
        for e in store.events(owner)
        if e["event_type"] == "CONFIRMATION_CANDIDATE_FROZEN"
    ]
    if not events:
        return None
    event = next(
        e for e in store.events(owner) if e["event_type"] == "CONFIRMATION_CANDIDATE_FROZEN"
    )
    return {**events[0], "freeze_sequence": event["sequence"]}


def staged_outcome(
    store: Store, owner: str, contract: AutonomyContract, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    assert contract.stages is not None
    stages = contract.stages
    base_key = configuration(store, contract, contract.baseline_candidate_ref, {})
    periods: dict[str, Any] = {}
    for period in contract.periods:
        available = [r for r in rows if r["period"] == period]
        baseline = next(
            (
                r
                for r in available
                if configuration(store, contract, r["candidate_ref"], r["settings"]) == base_key
            ),
            None,
        )
        others = [
            r
            for r in available
            if configuration(store, contract, r["candidate_ref"], r["settings"]) != base_key
        ]
        stage = (
            "selection"
            if period == stages.selection
            else ("confirmation" if period in stages.confirmation else "screening")
        )
        item: dict[str, Any] = {"stage": stage, "status": "NO_BASELINE_OBSERVATION"}
        if baseline:
            item.update(
                baseline_result_ref=baseline["result_ref"], baseline_metrics=baseline["metrics"]
            )
            item["status"] = "NO_CANDIDATES"
            if others:
                comparison = compare_results(contract, baseline, others)
                item.update(
                    status="COMPARED",
                    comparison=comparison,
                    selection_finalist=select_finalist(contract, comparison),
                )
        periods[period] = item
    selected = periods[stages.selection]
    winner = selected.get("selection_finalist", {"status": "NO_PASSING_CANDIDATE"})
    checks: list[dict[str, Any]] = []
    frozen = frozen_candidate(store, owner)
    final = dict(winner)
    gate = "NO_SELECTION_CANDIDATE"
    selected_settings = {}
    if winner["status"] == "SELECTED":
        row = next(r for r in rows if r["result_ref"] == winner["result_ref"])
        selected_settings = row["settings"]
        key = configuration(store, contract, row["candidate_ref"], row["settings"])
        gate = "PASS"
        for period in stages.confirmation:
            item = periods[period]
            matching = [
                r
                for r in item.get("comparison", {}).get("rows", [])
                if configuration(
                    store,
                    contract,
                    r["candidate_ref"],
                    next(o["settings"] for o in rows if o["result_ref"] == r["result_ref"]),
                )
                == key
            ]
            # A result obtained before the trusted freeze cannot become confirmation.
            eligible = set()
            if frozen and frozen["configuration_id"] == key:
                for event in store.events(owner):
                    if (
                        event["event_type"] == "ARTIFACTS_REGISTERED"
                        and event["sequence"] > frozen["freeze_sequence"]
                    ):
                        eligible.update(event["payload"]["observations"])
            matched = [r for r in matching if r["result_ref"] in eligible]
            verdict = "NOT_RUN"
            if matched:
                verdict = next(
                    p["verdict"]
                    for p in matched[-1]["profiles"]
                    if p["profile"] == winner["profile"]
                )
            checks.append(
                {
                    "period": period,
                    "verdict": verdict,
                    "result_ref": matched[-1]["result_ref"] if matched else None,
                }
            )
        if any(c["verdict"] == "NOT_RUN" for c in checks):
            gate = "AWAITING_CONFIRMATION"
        elif any(c["verdict"] != "PASS" for c in checks):
            gate = "CONFIRMATION_FAILED"
        if gate != "PASS":
            final = {"status": gate, "selection_result_ref": winner["result_ref"]}
    return {
        "status": selected["status"],
        "baseline_result_ref": selected.get("baseline_result_ref"),
        "baseline_metrics": selected.get("baseline_metrics"),
        "comparison": selected.get("comparison"),
        "candidates_tried": len(selected.get("comparison", {}).get("rows", [])),
        "selection_period": stages.selection,
        "selection_finalist": winner,
        "selection_settings": selected_settings,
        "finalist": final,
        "stage_gate": gate,
        "stages": periods,
        "confirmation": checks,
        "independent_validation": "NOT_RUN",
    }


def admit_staged_backtest(
    store: Store,
    owner: str,
    contract: AutonomyContract,
    action: BacktestAction,
    outcome: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a freeze receipt only for the first admitted confirmation candidate."""
    if contract.stages is None:
        return None
    stages = contract.stages
    frozen = frozen_candidate(store, owner)
    if frozen and any(x.period not in stages.confirmation for x in action.experiments):
        raise ValueError("selection is frozen; further tuning requires a new session")
    confirms = [e for e in action.experiments if e.period in stages.confirmation]
    if not confirms:
        return None
    winner = outcome.get("selection_finalist", {})
    if winner.get("status") != "SELECTED":
        raise ValueError("confirmation requires a passing candidate on the selection period")
    winner_row = next(
        r for r in outcome["comparison"]["rows"] if r["result_ref"] == winner["result_ref"]
    )
    # The session outcome also retains the complete selected observation's settings.
    selected_settings = outcome["selection_settings"]
    key = configuration(store, contract, winner_row["candidate_ref"], selected_settings)
    base_key = configuration(store, contract, contract.baseline_candidate_ref, {})
    for experiment in confirms:
        experiment_key = configuration(
            store, contract, experiment.candidate_ref, setting_dict(experiment.settings)
        )
        if experiment_key not in {base_key, key}:
            raise ValueError(
                "confirmation accepts only the frozen candidate/settings and matched baseline"
            )
    if frozen and frozen["configuration_id"] != key:
        raise ValueError("selection changed after confirmation freeze")
    # Even a baseline-only confirmation begins the stage and freezes selection.
    return (
        {
            "configuration_id": key,
            "candidate_ref": winner_row["candidate_ref"],
            "settings": selected_settings,
            "selection_result_ref": winner["result_ref"],
            "profile": winner["profile"],
        }
        if frozen is None
        else None
    )
