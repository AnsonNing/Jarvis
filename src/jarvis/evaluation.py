"""Fixed numerical evaluator, independently versioned from role responses."""

from pathlib import Path

from jarvis.contracts import Evaluation, EvaluationScope, Observation, ResearchTask, Verdict
from jarvis.identity import file_digest


def evaluator_identity() -> str:
    return file_digest(Path(__file__))


def evaluate(
    task: ResearchTask,
    observation: Observation,
    baseline: float | None,
    evidence_ref: str,
    *,
    formal: bool = False,
) -> Evaluation:
    value = observation.metrics.get(task.primary_metric)
    improvement = None
    acceptance: Verdict = "NOT_RUN"
    if observation.status != "SUCCEEDED":
        acceptance = "INCONCLUSIVE"
    elif value is not None and baseline is not None:
        improvement = (value - baseline) * (1 if task.metric_direction == "maximize" else -1)
        acceptance = (
            "PASS" if improvement >= task.acceptance.minimum_primary_improvement else "FAIL"
        )
    scope: EvaluationScope = "locked_synthetic" if formal else "development"
    if task.domain == "hermes":
        scope, acceptance = "dataset_replay", "INCONCLUSIVE"
    return Evaluation(
        evaluator_id=evaluator_identity(),
        action_id=observation.action_id,
        scope=scope,
        acceptance=acceptance,
        primary_metric=task.primary_metric,
        value=value,
        baseline=baseline,
        improvement=improvement,
        evidence_refs=(evidence_ref,),
        limitations=observation.limitations,
    )
