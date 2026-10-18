"""Preparation-only research inventory: no provider, worker, quota or backend calls."""

from pathlib import Path
from typing import Any

from jarvis.contracts import JarvisError
from jarvis.identity import digest
from jarvis.iris import setting_capabilities
from jarvis.iris_admission import attach_design, model_status, prepare_baseline, prepare_environment
from jarvis.iris_cases import read_object
from jarvis.iris_design import design_capabilities


def research_readiness(
    folder: Path,
    python: Path,
    *,
    design_root: Path | None = None,
    development_use_path: Path | None = None,
    states: tuple[Path, ...] = (),
) -> dict[str, Any]:
    tracks, blockers = [], []
    for side in ("long", "short"):
        for window in ("smoke", "full"):
            try:
                env = prepare_environment(
                    folder,
                    side,
                    "08",
                    python,
                    window=window,
                    development_use_path=development_use_path,
                )
                if design_root:
                    env = attach_design(env, design_root / (side + ".json"))
                proof = prepare_baseline(
                    folder,
                    side,
                    "08",
                    window=window,
                    development_use_path=development_use_path,
                )
                tracks.append(
                    {
                        "side": side,
                        "window": window,
                        "environment_id": digest(env),
                        "model_lineage": env.model_lineage,
                        "candidate_sha256": env.template_sha256,
                        "baseline": proof.model_dump(mode="json"),
                        "settings": setting_capabilities(env),
                        "rules": design_capabilities(env),
                        "baseline_accounting": "NOT_RECOMPUTED_BY_READINESS",
                        "period": env.period.model_dump(mode="json"),
                    }
                )
            except (JarvisError, OSError, ValueError, KeyError) as exc:
                blockers.append({"side": side, "window": window, "reason": str(exc)})
    controls = [{"path": str(p), "status": read_object(p).get("status")} for p in states]
    return {
        "kind": "IrisResearchReadiness",
        "schema_version": 1,
        "status": "PREPARED_NOT_STARTED" if not blockers else "NEEDS_INPUT",
        "execution_authorized": False,
        "model_acceptance": model_status(folder),
        "development_use": "OWNER_BOUND_WITH_LIMITATIONS" if development_use_path else "STRICT",
        "tracks": tracks,
        "blockers": blockers,
        "existing_controls": controls,
        "new_llm_calls": 0,
        "new_remote_submissions": 0,
        "new_studies": 0,
        "research_order": [
            "fixed-policy strategy baseline",
            "distinct history collection",
            "Dream policy comparison",
        ],
        "before_execution": [
            "fresh owner instruction to start",
            "confirmed quota evidence",
            "local baseline accounting audit",
            "compile admission",
            "one integration candidate per side",
        ],
        "before_policy_promotion": [
            "at least ten distinct replay-valid histories across three independent task families",
            "development/heldout family isolation",
            "one frozen finalist",
            "matched-budget fresh Q0 comparison",
            "subsequent canary and rollback",
        ],
        "missing_capabilities": [
            "full-precision runtime input/score proof",
            "complete order/cancel acknowledgement ledger",
            "postfill quote path and explicit fill/fee units",
            "independent unseen heldout/fresh/canary tasks",
        ],
    }
