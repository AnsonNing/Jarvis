"""Memoization within ONE read-only audit; never retained across audit requests."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from jarvis.storage import Store

_current: ContextVar[dict[tuple[str, str, str], dict[str, Any]] | None] = ContextVar(
    "lab_audit", default=None
)


@contextmanager
def audit_scope() -> Iterator[None]:
    token = _current.set({}) if _current.get() is None else None
    try:
        yield
    finally:
        if token is not None:
            _current.reset(token)


def audited(
    store: Store, kind: str, owner: str, loader: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    cache = _current.get()
    if cache is None:
        return loader()
    key = (str(store.root), kind, owner)
    if key not in cache:
        cache[key] = loader()
    return cache[key]
