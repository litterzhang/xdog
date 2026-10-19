"""Bounded read-only host views, granted to an actual configured extension owner."""
from __future__ import annotations

import json
import math
import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

METHODS = frozenset({"xdog/command", "xdog/event"})
Reader = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def identifier(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", value) is None:
        raise ValueError("View identifiers must be 1-32 ASCII letters/digits/underscore")
    return value


def _json(value: Any, limit: int) -> Any:
    stack = [(value, 0)]
    count = size = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 32 or count > limit:
            raise ValueError("View data exceeds depth/capacity bounds")
        if type(item) is dict:
            if len(item) * 2 > limit:
                raise ValueError("View data exceeds node capacity")
            if any(type(key) is not str for key in item):
                raise ValueError("View JSON keys must be strings")
            stack.extend((child, depth + 1) for pair in item.items() for child in pair)
        elif type(item) is list:
            if len(item) > limit:
                raise ValueError("View data exceeds node capacity")
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            size += len(item.encode("utf-8"))
        elif item is None or type(item) in {bool, int}:
            pass
        elif type(item) is float and math.isfinite(item):
            pass
        else:
            raise ValueError("Views require finite JSON data, not runtime objects")
        if size > limit or len(stack) > limit:
            raise ValueError("View data exceeds encoded capacity")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError("View data exceeds encoded capacity")
    return json.loads(encoded)


def request(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"name", "query"} or type(value["query"]) is not dict:
        raise ValueError("View requests contain only a name and JSON query, never host routing")
    return {"name": identifier(value["name"]), "query": _json(value["query"], 4096)}


def answer(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError("View snapshot must be a JSON object or unavailable")
    result: dict[str, Any] = _json(value, 65536)
    return result


@dataclass(frozen=True)
class _Binding:
    read: Reader
    valid: Callable[[], bool]


class ViewRegistry:
    """One ExtensionRuntime's grants; revocation cannot affect business lifetime."""
    def __init__(self) -> None:
        self._bindings: dict[tuple[str, str], _Binding] = {}
        self._lock = threading.Lock()
        self._closed = False

    def bind(self, extension_id: str, name: str, read: Reader, *, valid: Callable[[], bool]) -> Callable[[], None]:
        key = identifier(extension_id), identifier(name)
        if not callable(read) or not callable(valid):
            raise ValueError("View requires an explicit host read/owner validity port")
        binding = _Binding(read, valid)
        with self._lock:
            if self._closed or key in self._bindings:
                raise ValueError("View registry closed or binding already owned")
            self._bindings[key] = binding

        def remove() -> None:
            with self._lock:
                if self._bindings.get(key) is binding:
                    self._bindings.pop(key)
        return remove

    def has(self, extension_id: str) -> bool:
        with self._lock:
            return not self._closed and any(key[0] == extension_id for key in self._bindings)

    def token(self, extension_id: str, view: str) -> object | None:
        """Opaque binding identity for owned hint routing, never sent to workers."""
        with self._lock:
            return None if self._closed else self._bindings.get((extension_id, view))

    def is_current(self, extension_id: str, view: str, token: object) -> bool:
        current = self.token(extension_id, view)
        return current is token and isinstance(current, _Binding) and current.valid() is True

    async def read(self, extension_id: str, name: str, query: dict[str, Any]) -> dict[str, Any] | None:
        packet = request({"name": name, "query": query})
        key = identifier(extension_id), packet["name"]
        with self._lock:
            binding = None if self._closed else self._bindings.get(key)
        if binding is None or binding.valid() is not True:
            return None
        value = await binding.read(packet["query"])
        with self._lock:
            current = None if self._closed else self._bindings.get(key)
        if current is not binding or binding.valid() is not True:
            return None
        return answer(value)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._bindings.clear()
