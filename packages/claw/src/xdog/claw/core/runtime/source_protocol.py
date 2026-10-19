"""One connection-owned source sender; coalesce reads without blocking controls."""
from __future__ import annotations

import asyncio
import base64
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.async_work import run_owned, wait_owned
from xdog.claw.core.runtime.transcript_source import SourceCapture, SourceSpool, prepare_source

logger = logging.getLogger(__name__)

SOURCE_BLOCK_BYTES = 64 * 1024


class SourceSender:
    def __init__(self, capture: Callable[[str, str], SourceCapture], current: Callable[[str], tuple[str, int]],
                 write: Callable[[dict[str, Any]], Awaitable[None]],
                 prepare: Callable[[SourceCapture, threading.Event], SourceSpool] | None = None) -> None:
        self.capture, self.current, self.write = capture, current, write
        self.prepare = prepare
        self._pending: tuple[str, str, str, Callable[[], SourceCapture] | None, dict[str, str]] | None = None
        self._active = ""
        self._active_scope: dict[str, str] = {}
        self._cancel = threading.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def request(self, request_id: str, group: str, session: str, *,
                capture: Callable[[], SourceCapture] | None = None, scope: dict[str, str] | None = None) -> None:
        if self._closed:
            return
        self._pending = request_id, group, session, capture, dict(scope or {})
        self._cancel.set()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._work())
            self._task.add_done_callback(self._done)

    @staticmethod
    def _done(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            # A readonly display failure cannot alter accepted input outcomes,
            # but its actual late cleanup error must remain observable.
            logger.warning("Source cleanup failed", exc_info=error)

    def cancel(self, request_id: str) -> None:
        if self._pending is not None and self._pending[0] == request_id:
            self._pending = None
        if self._active == request_id:
            self._cancel.set()

    def cancel_saved(self) -> None:
        if self._pending is not None and self._pending[4]:
            self._pending = None
        if self._active_scope:
            self._cancel.set()

    async def close(self) -> None:
        self._closed = True
        self._pending = None
        self._cancel.set()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _send(self, payload: dict[str, Any]) -> None:
        await asyncio.wait_for(self.write(payload), 3)

    async def _work(self) -> None:
        while self._pending is not None and not self._closed:
            request_id, group, session, override, scope = self._pending
            self._pending = None
            self._active = request_id
            self._active_scope = scope
            self._cancel = threading.Event()
            canceled = self._cancel
            spool: SourceSpool | None = None
            preparing: asyncio.Task[SourceSpool] | None = None
            envelope: dict[str, Any] = {"request_id": request_id, "group_id": group, "session_id": session, **scope}
            try:
                captured = override() if override is not None else self.capture(group, session)
                envelope["revision"] = captured.revision
                preparing = asyncio.create_task(asyncio.to_thread(self.prepare or prepare_source, captured, canceled))
                spool = await asyncio.shield(preparing)
                if canceled.is_set():
                    continue
                if self.current(group) != (session, captured.revision):
                    raise ValueError("Active session changed during source read")
                await self._send({"type": "source_begin", **envelope, "bytes": spool.size, "sha256": spool.sha256})
                index = 0
                while not canceled.is_set():
                    if self.current(group) != (session, captured.revision):
                        raise ValueError("Active session changed during source transfer")
                    block = await run_owned(spool.file.read, SOURCE_BLOCK_BYTES)
                    if self.current(group) != (session, captured.revision):
                        raise ValueError("Active session/context changed during source transfer")
                    if not block:
                        await self._send({"type": "source_end", **envelope, "chunks": index})
                        break
                    await self._send({"type": "source_chunk", **envelope, "index": index,
                                      "data": base64.b64encode(block).decode("ascii")})
                    index += 1
            except asyncio.CancelledError:
                canceled.set()
                raise
            except Exception as exc:
                if not canceled.is_set():
                    try:
                        await self._send({"type": "source_error", **envelope, "error": str(exc)})
                    except (OSError, TimeoutError):
                        pass
            finally:
                canceled.set()
                async def release_spool() -> None:
                    actual = spool
                    if preparing is not None and actual is None:
                        try:
                            actual = await preparing
                        except Exception:
                            return  # Preparation owns failed partial spools; body reports its error.
                    if actual is not None:
                        await run_owned(actual.file.close)

                # A late prepared spool is still ours. Join actual read/close
                # work despite repeated cancellation before clearing its scope.
                try:
                    await wait_owned(asyncio.create_task(release_spool()))
                finally:
                    self._active = ""
                    self._active_scope = {}
