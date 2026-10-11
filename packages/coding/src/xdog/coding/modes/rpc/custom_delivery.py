"""Bounded cross-thread custom-frame delivery; no dropping into an interactive partial view."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

MAX_PENDING_FRAMES = 16
MAX_PENDING_FRAME_BYTES = 2 * 1024 * 1024


class CustomDelivery:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.pending = 0
        self.bytes = 0
        self.lock = threading.Lock()
        self.failures: set[tuple[str, ...]] = set()

    def schedule(self, callback: Callable[..., None], args: tuple[Any, ...], size: int) -> bool:
        """Reserve before scheduling; refusal leaves admission/cancellation with the host."""
        with self.lock:
            if (type(size) is not int or size < 0 or self.pending >= MAX_PENDING_FRAMES
                    or self.bytes + size > MAX_PENDING_FRAME_BYTES or self.loop.is_closed()):
                return False
            self.pending += 1
            self.bytes += size
        try:
            self.loop.call_soon_threadsafe(self._deliver, callback, args, size)
        except RuntimeError:
            self._release(size)
            return False
        return True

    def report_failure(self, key: tuple[str, ...], callback: Callable[..., None], args: tuple[Any, ...]) -> bool:
        """Bound and deduplicate small error callbacks independently of full frame capacity."""
        with self.lock:
            if key in self.failures or len(self.failures) >= 8 or self.loop.is_closed():
                return False
            self.failures.add(key)
        def deliver() -> None:
            try:
                callback(*args)
            finally:
                with self.lock:
                    self.failures.discard(key)
        try:
            self.loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            with self.lock:
                self.failures.discard(key)
            return False
        return True

    def _release(self, size: int) -> None:
        with self.lock:
            self.pending -= 1
            self.bytes -= size

    def _deliver(self, callback: Callable[..., None], args: tuple[Any, ...], size: int) -> None:
        try:
            callback(*args)
        finally:
            self._release(size)
