"""Trusted adapter interface. A provider selects IDs, never raw shell commands."""

from typing import Any, Protocol

from jarvis.contracts import ActionSpec, Observation, ResearchState, ResearchTask


class DomainAdapter(Protocol):
    adapter_id: str

    def identity(self) -> dict[str, str]: ...

    def validate(self, task: ResearchTask) -> None: ...

    def frontier(self, task: ResearchTask, state: ResearchState) -> tuple[ActionSpec, ...]: ...

    def execute(self, action: ActionSpec) -> Observation: ...

    def formal(self, candidate: str) -> Observation | None: ...

    def local_manifest(self) -> dict[str, Any]: ...
