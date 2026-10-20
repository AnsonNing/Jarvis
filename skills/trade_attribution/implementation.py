"""Reviewable wrapper; registry dispatch uses the pinned trusted builtin."""

from jarvis.trading_diagnostics import DiagnosticInput, attribution


def run(payload):
    return attribution(DiagnosticInput.model_validate(payload)).model_dump(mode="json")
