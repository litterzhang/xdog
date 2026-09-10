"""Immutable event snapshots crossing into a UI thread."""
from __future__ import annotations

import queue
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from types import MappingProxyType
from typing import Any


class _FrozenList(tuple[Any, ...]):
    """Preserve a source list's shape for UI-local reconstruction."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        if not getattr(value, "__dataclass_params__").frozen:
            raise TypeError("UI events require frozen dataclasses")
        snapshot = {field.name: _freeze(getattr(value, field.name)) for field in fields(value) if field.init}
        return replace(value, **snapshot)
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return value
    raise TypeError(f"Unsupported UI event payload: {type(value).__name__}")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, _FrozenList):
        return [_thaw(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_thaw(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        owned = {field.name: _thaw(getattr(value, field.name)) for field in fields(value) if field.init}
        return replace(value, **owned)
    return value


def thaw_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Create an owned payload for existing UI components and extension hooks."""
    return {key: _thaw(value) for key, value in event.items()}


class EventQueue(queue.Queue[Mapping[str, Any]]):
    """Snapshot before publishing; producers cannot mutate an enqueued event."""

    def put(self, item: Mapping[str, Any], block: bool = True, timeout: float | None = None) -> None:
        super().put(_freeze(item), block, timeout)
