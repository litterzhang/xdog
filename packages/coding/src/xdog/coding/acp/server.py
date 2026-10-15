"""Private ACP Unix endpoint for a live session owner, not an Eyes event host."""

from __future__ import annotations

import asyncio
import os
import stat
from typing import TYPE_CHECKING

from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.transport import serve
from xdog.coding.acp.backend import CodingBackend

if TYPE_CHECKING:
    from xdog.coding.core.sessions.runtime import SessionRuntime


class LocalServer:
    def __init__(self, runtime: SessionRuntime) -> None:
        self.runtime = runtime
        self.record = runtime.record
        self.identity: tuple[int, int] | None = None
        self.server: asyncio.AbstractServer | None = None
        self.closing = False
        self.writers: set[asyncio.StreamWriter] = set()
        self.tasks: set[asyncio.Task[None]] = set()

    async def accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.closing:
            writer.close()
            await writer.wait_closed()
            return
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        self.writers.add(writer)
        adapter = AcpAdapter(
            CodingBackend(self.runtime.router, self.runtime.registry, fixed_session=self.record.session_id)
        )
        try:
            await serve(adapter, reader, writer)
        finally:
            self.tasks.discard(task)
            self.writers.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def close(self) -> None:
        self.closing = True
        if self.server is not None:
            self.server.close()
        for writer in tuple(self.writers):
            writer.close()
        if self.server is not None:
            # Python >=3.12 waits for client transports too; close them first.
            await self.server.wait_closed()
            self.server = None
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        path = self.record.address
        if path.exists():
            info = path.lstat()
            if (stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
                    and (info.st_dev, info.st_ino) == self.identity):
                path.unlink()
        self.runtime.registry.remove(self.record)


async def serve_local(runtime: SessionRuntime) -> LocalServer:
    if runtime.session._lease is None or runtime.session._lease._fd < 0:
        raise RuntimeError("Cannot serve a session without its writer lease")
    path = runtime.record.address
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Refusing to replace an unowned runtime socket")
        # Our exclusive lease proves no conforming live owner can own this path.
        path.unlink()
    result = LocalServer(runtime)
    try:
        result.server = await asyncio.start_unix_server(result.accept, path=str(path), limit=1024 * 1024)
        os.chmod(path, 0o600)
        info = path.lstat()
        result.identity = info.st_dev, info.st_ino
        runtime.registry.publish(runtime.record)
        runtime.server = result
        return result
    except BaseException:
        await result.close()
        raise
