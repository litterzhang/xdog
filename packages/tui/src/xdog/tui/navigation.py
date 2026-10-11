"""Immutable, paginated navigation views with local-worker/remote-feed adapters."""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class NavigationEntry:
    id: str
    title: str
    detail: str = ""
    kind: str = "session"
    session_id: str = ""
    branch_id: str = ""
    index: int = -1
    depth: int = 0
    active: bool = False


@dataclass(frozen=True, slots=True)
class NavigationSnapshot:
    version: int = 0
    serial: int = 0
    scope: str = "sessions"
    query: str = ""
    offset: int = 0
    total: int = 0
    entries: tuple[NavigationEntry, ...] = ()
    busy: bool = False
    error: str = ""


NavigationFetch = Callable[[str], tuple[NavigationEntry, ...]]


def navigation_page(entries: tuple[NavigationEntry, ...], scope: str, query: str,
                    offset: int, *, limit: int = 50) -> NavigationSnapshot:
    matches = tuple(entry for entry in entries if query.casefold() in
                    f"{entry.id} {entry.title} {entry.detail}".casefold())
    offset = min(max(0, offset), max(0, len(matches) - 1))
    return NavigationSnapshot(scope=scope, query=query, offset=offset, total=len(matches),
                              entries=matches[offset:offset + limit])


class NavigationFeed:
    def __init__(self, send: Callable[[int, str, str, int], None] | None = None) -> None:
        self._lock = threading.RLock()
        self._snapshot = NavigationSnapshot()
        self._serial = 0
        self._closed = False
        self._send = send

    def snapshot(self) -> NavigationSnapshot:
        with self._lock:
            return self._snapshot

    def request(self, scope: str, query: str = "", offset: int = 0) -> int:
        with self._lock:
            if self._closed:
                return self._serial
            self._serial += 1
            serial = self._serial
            changed = (scope, query, offset) != (self._snapshot.scope, self._snapshot.query, self._snapshot.offset)
            self._snapshot = replace(self._snapshot, version=self._snapshot.version + 1, serial=serial,
                                     scope=scope, query=query, offset=offset, busy=True, error="",
                                     entries=() if changed else self._snapshot.entries)
        if self._send is not None:
            self._send(serial, scope, query, offset)
        return serial

    def publish(self, serial: int, value: NavigationSnapshot) -> None:
        with self._lock:
            if not self._closed and serial == self._serial:
                self._snapshot = replace(value, version=self._snapshot.version + 1, serial=serial, busy=False)

    def close(self) -> None:
        with self._lock:
            self._closed = True


class NavigationWorker(NavigationFeed):
    def __init__(self, fetch: NavigationFetch) -> None:
        super().__init__()
        self._fetch = fetch
        self._condition = threading.Condition(self._lock)
        self._pending: tuple[int, str, str, int] | None = None
        self._thread: threading.Thread | None = None

    def request(self, scope: str, query: str = "", offset: int = 0) -> int:
        serial = super().request(scope, query, offset)
        with self._condition:
            if not self._closed:
                self._pending = (serial, scope, query, offset)
                if self._thread is None:
                    self._thread = threading.Thread(target=self._work, name="xdog-session-browser", daemon=True)
                    self._thread.start()
                self._condition.notify()
        return serial

    def _work(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                request, self._pending = self._pending, None
            assert request is not None
            serial, scope, query, offset = request
            try:
                value = navigation_page(self._fetch(scope), scope, query, offset)
            except Exception as exc:
                value = NavigationSnapshot(scope=scope, query=query, error=str(exc))
            self.publish(serial, value)

    def close(self) -> None:
        super().close()
        with self._condition:
            self._condition.notify()
