"""Reviewable wrapper; registry dispatch uses the pinned trusted builtin."""

from jarvis.trading_diagnostics import DiagnosticInput, score_distribution


def run(payload):
    return score_distribution(DiagnosticInput.model_validate(payload)).model_dump(mode="json")
