"""Explicit private Unix ACP endpoint, owned by the existing Claw gateway."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.transport import serve
from xdog.agent.async_work import run_owned, wait_owned
from xdog.claw.acp.backend import ClawBackend
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.runtime.unix_endpoint import BoundEndpoint, bind_private, close, listen

logger = logging.getLogger(__name__)


class ClawAcpServer:
    def __init__(self, path: Path, backend: ClawBackend) -> None:
        self.path, self.backend = path, backend
        self._server: asyncio.Server | None = None
        self._endpoint: BoundEndpoint | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self._release_endpoint: Callable[[], None] = lambda: None

    @classmethod
    async def start(cls, orchestrator: Orchestrator, path: Path, *, recover_stale: bool = False) -> ClawAcpServer:
        maximum = 107  # Current Linux Unix-domain path limit; no non-Linux transport adapter.
        if not path.is_absolute() or "\x00" in str(path) or len(str(path).encode()) > maximum:
            raise ValueError("ACP socket must have a bounded absolute Unix path")
        acquiring = asyncio.create_task(asyncio.to_thread(bind_private, path, recover_stale=True)
                                        if recover_stale else asyncio.to_thread(bind_private, path))
        try:
            endpoint = await wait_owned(acquiring)
        except BaseException:
            if acquiring.done() and not acquiring.cancelled() and acquiring.exception() is None:
                await run_owned(close, acquiring.result())
            raise
        try:
            backend = await ClawBackend.open(orchestrator)
        except BaseException:
            await run_owned(close, endpoint)
            raise
        result = cls(path, backend)
        result._endpoint = endpoint
        try:
            result._server = await listen(result._accept, endpoint, limit=1024 * 1024)
            result._release_endpoint = orchestrator.register_acp_endpoint(
                result, path, valid=lambda: not result._closing)
            return result
        except BaseException:
            await result.close()
            raise

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closing or len(self._tasks) >= 64:
            writer.close()
            return
        self._writers.add(writer)
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await serve(AcpAdapter(self.backend), reader, writer)
        except Exception:
            logger.debug("Claw ACP peer ended", exc_info=True)
        finally:
            self._writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        from xdog.agent.async_work import settle_cleanup
        async def retire() -> None:
            self._release_endpoint()
        await settle_cleanup(retire, self._close_transport, message='Private ACP retirement failed')

    async def _close_transport(self) -> None:
        if self._server is not None:
            self._server.close()
        for writer in tuple(self._writers):
            writer.close()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        try:
            await self.backend.close()
        finally:
            if self._endpoint is not None:
                endpoint, self._endpoint = self._endpoint, None
                await run_owned(close, endpoint)
