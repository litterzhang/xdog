"""Explicit private Unix ACP endpoint, owned by the existing Claw gateway."""
from __future__ import annotations

import asyncio
import logging
import os
import stat
import sys
from pathlib import Path

from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.transport import serve
from xdog.claw.acp.backend import ClawBackend, CompletionSink
from xdog.claw.core.runtime.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class ClawAcpServer:
    def __init__(self, path: Path, backend: ClawBackend) -> None:
        self.path, self.backend = path, backend
        self._server: asyncio.Server | None = None
        self._identity: tuple[int, int] | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    async def start(cls, orchestrator: Orchestrator, path: Path, inbox_path: Path,
                    *, on_complete: CompletionSink | None = None) -> ClawAcpServer:
        maximum = 103 if sys.platform == "darwin" else 107
        if not path.is_absolute() or "\x00" in str(path) or len(str(path).encode()) > maximum:
            raise ValueError("ACP socket must have a bounded absolute Unix path")
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory = path.parent.lstat()
        if (path.parent.resolve() != path.parent or not stat.S_ISDIR(directory.st_mode)
                or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700):
            raise ValueError("ACP endpoint directory must be owned and private (0700), without symlinks")
        if path.exists() or path.is_symlink():
            raise ValueError("ACP endpoint already exists; stop its owner/remove a confirmed stale socket explicitly")
        backend = await ClawBackend.open(orchestrator, inbox_path, on_complete=on_complete)
        result = cls(path, backend)
        try:
            result._server = await asyncio.start_unix_server(result._accept, path=str(path), limit=1024 * 1024)
            info = path.lstat()
            result._identity = info.st_dev, info.st_ino
            os.chmod(path, 0o600)
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
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self) -> None:
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
        await self.backend.close()
        try:
            info = self.path.lstat()
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self._identity:
                self.path.unlink()
        except FileNotFoundError:
            pass
