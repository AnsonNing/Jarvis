"""Reviewable wrapper; registry dispatch uses the pinned trusted builtin."""

from jarvis.trading_diagnostics import DiagnosticInput, inventory


def run(payload):
    return inventory(DiagnosticInput.model_validate(payload)).model_dump(mode="json")
