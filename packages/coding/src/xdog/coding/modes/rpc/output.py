"""Bounded, ordered native POSIX RPC output; custom sinks retain synchronous compatibility."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from collections import deque
from typing import Any

MAX_OUTPUT_BYTES = 64 * 1024 * 1024
TRANSIENT_BACKLOG_BYTES = 1024 * 1024
_TRANSIENT_TYPES = frozenset({
    "transient_event", "child_transient_event", "extension_contribution", "child_contribution"})


class RpcOutput:
    def __init__(self) -> None:
        self.fd: int | None = None
        self.was_blocking = True
        self.queue: deque[tuple[bytes, asyncio.Future[None] | None]] = deque()
        self.bytes = 0
        self.wake = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.failure: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.error: OSError | None = None
        self.closed = False
        self.transient_dropped: dict[str, int] = {}

    def open(self) -> None:
        if os.name != 'posix':
            return
        try:
            fd = sys.stdout.fileno()
            mode = os.fstat(fd).st_mode
            if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode) or stat.S_ISCHR(mode)):
                return
            blocking = os.get_blocking(fd)
            os.set_blocking(fd, False)
        except (AttributeError, OSError, ValueError):
            return  # Custom test/embed sinks or unsupported platform: existing compatibility, not native I/O proof.
        self.fd, self.was_blocking = fd, blocking
        self.task = asyncio.create_task(self._write())

    def emit(self, event: dict[str, Any]) -> None:
        self._enqueue(event, None)

    async def send(self, event: dict[str, Any]) -> None:
        if self.fd is None:
            self.emit(event)
            await asyncio.sleep(0)
            return
        receipt: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._enqueue(event, receipt)
        # Canceling the source producer cannot drop a half-written JSON line.
        try:
            await asyncio.shield(receipt)
        except asyncio.CancelledError:
            receipt.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            raise

    def _enqueue(self, event: dict[str, Any], receipt: asyncio.Future[None] | None) -> None:
        if self.closed or self.error is not None:
            raise self.error or OSError('RPC output closed')
        transient = event.get('type') in _TRANSIENT_TYPES and receipt is None
        if transient and self.fd is not None:
            kind = str(event['type'])
            if self.bytes >= min(TRANSIENT_BACKLOG_BYTES, MAX_OUTPUT_BYTES // 2):
                self.transient_dropped[kind] = min(self.transient_dropped.get(kind, 0) + 1, 2**53 - 1)
                return  # Presentation events cannot monopolize control/approval output memory.
            missed = self.transient_dropped.get(kind, 0)
            if missed:
                event = {**event, 'limited': True, 'transport_dropped': missed}
        line = json.dumps(event, ensure_ascii=False) + '\n'
        if self.fd is None:
            sys.stdout.write(line)
            sys.stdout.flush()
            return
        data = line.encode('utf-8')
        if transient and self.bytes + len(data) > min(TRANSIENT_BACKLOG_BYTES, MAX_OUTPUT_BYTES // 2):
            kind = str(event['type'])
            self.transient_dropped[kind] = min(self.transient_dropped.get(kind, 0) + 1, 2**53 - 1)
            return
        if self.bytes + len(data) > MAX_OUTPUT_BYTES:
            raise OSError('RPC output backlog exceeded; frame refused, no truncation')
        if transient:
            self.transient_dropped.pop(str(event["type"]), None)
        self.queue.append((data, receipt))
        self.bytes += len(data)
        self.wake.set()

    async def _writable(self) -> None:
        assert self.fd is not None
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[None] = loop.create_future()
        def available() -> None:
            if not ready.done():
                ready.set_result(None)
        loop.add_writer(self.fd, available)
        try:
            await ready
        finally:
            loop.remove_writer(self.fd)

    async def _write(self) -> None:
        assert self.fd is not None
        try:
            while True:
                while self.queue:
                    data, receipt = self.queue[0]
                    view = memoryview(data)
                    while view:
                        try:
                            sent = os.write(self.fd, view)
                        except BlockingIOError:
                            await self._writable()
                            continue
                        if sent <= 0:
                            raise OSError('RPC output peer closed')
                        view = view[sent:]
                    self.queue.popleft()
                    self.bytes -= len(data)
                    if receipt is not None and not receipt.done():
                        receipt.set_result(None)
                    await asyncio.sleep(0)
                self.wake.clear()
                await self.wake.wait()
        except (OSError, NotImplementedError) as exc:
            self.error = exc if isinstance(exc, OSError) else OSError('RPC output polling unavailable')
            if not self.failure.done():
                self.failure.set_result(None)
        finally:
            while self.queue:
                _data, receipt = self.queue.popleft()
                if receipt is not None and not receipt.done():
                    receipt.set_exception(self.error or OSError('RPC output closed before frame settled'))
            self.bytes = 0

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.task is not None:
            try:
                async with asyncio.timeout(1):
                    while self.queue and not self.task.done():
                        await asyncio.sleep(0.005)
            except TimeoutError:
                pass  # A stalled/closed peer cannot indefinitely own shutdown.
            if not self.task.done():
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.fd is not None:
            try:
                os.set_blocking(self.fd, self.was_blocking)
            except OSError:
                pass
        if not self.failure.done():
            self.failure.cancel()
