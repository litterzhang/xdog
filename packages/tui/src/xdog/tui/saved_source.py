"""Presentation-only saved-source identity and one coalescing record-read worker."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from xdog.tui.transcript_browser import TranscriptRecord


class SavedSourceWorker:
    def __init__(self, publish: Callable[[dict[str, Any]], None]) -> None:
        self.publish = publish
        self._condition = threading.Condition()
        self._pending: tuple[str, str, Callable[[], tuple[TranscriptRecord, ...]]] | None = None
        self._serial = 0
        self._closed = False
        self._thread: threading.Thread | None = None

    def request(self, identifier: str, context: str, load: Callable[[], tuple[TranscriptRecord, ...]]) -> None:
        with self._condition:
            if self._closed:
                return
            self._serial += 1
            self._pending = identifier, context, load
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, daemon=True, name='xdog-saved-source')
                self._thread.start()
            self._condition.notify()

    def invalidate(self) -> None:
        with self._condition:
            self._serial += 1
            self._pending = None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._serial += 1
            self._pending = None
            self._condition.notify_all()

    def _work(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                assert self._pending is not None
                identifier, context, load = self._pending
                serial = self._serial
                self._pending = None
            result: dict[str, Any] = {'type': 'saved_source_ready', 'id': identifier, 'context': context}
            try:
                result['records'] = load()
            except Exception:
                result.update(type='saved_source_failed', error='Saved source unavailable; no live-context fallback')
            with self._condition:
                if not self._closed and serial == self._serial:
                    self.publish(result)
