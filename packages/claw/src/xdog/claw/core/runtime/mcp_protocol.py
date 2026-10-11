"""Connection-owned, coalesced MCP previews; remote reads never block controls."""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from typing import Any, BinaryIO

from xdog.agent.mcp.completion import McpCompletion
from xdog.agent.mcp.context import McpDocument, McpEntry
from xdog.claw.channels.tui.mcp_data import spool_mcp_data


class McpSender:
    def __init__(self, fetch: Callable[[dict[str, Any]], Awaitable[tuple[McpEntry, ...] | McpDocument | McpCompletion]],
                 current: Callable[[str], tuple[str, int]],
                 write: Callable[[dict[str, Any]], Awaitable[None]], *,
                 content_types: Callable[[], bool] | None = None) -> None:
        self.fetch, self.current, self.write = fetch, current, write
        self.content_types = content_types or (lambda: True)
        self._pending: dict[str, Any] | None = None
        self._active = ""
        self._task: asyncio.Task[None] | None = None
        self._operation: asyncio.Task[None] | None = None
        self._closed = False

    def request(self, request: dict[str, Any]) -> None:
        if self._closed:
            return
        self._pending = {**request, "_content_types": self.content_types()}
        if self._operation is not None and not self._operation.cancelling():
            self._operation.cancel()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._work())

    def cancel(self, identifier: str) -> None:
        if self._pending is not None and self._pending["request_id"] == identifier:
            self._pending = None
        if self._active == identifier and self._operation is not None:
            # Cancel only once: repeated cancellation must not interrupt file settlement.
            if not self._operation.cancelling():
                self._operation.cancel()

    async def close(self) -> None:
        self._closed = True
        self._pending = None
        self.cancel(self._active)
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _work(self) -> None:
        while self._pending is not None and not self._closed:
            request, self._pending = self._pending, None
            self._active = str(request["request_id"])
            self._operation = asyncio.create_task(self._transfer(request))
            await asyncio.gather(self._operation, return_exceptions=True)
            self._operation = None
            self._active = ""

    async def _send(self, payload: dict[str, Any]) -> None:
        await asyncio.wait_for(self.write(payload), 3)

    async def _transfer(self, request: dict[str, Any]) -> None:
        identifier, group, session = request["request_id"], request["group_id"], request["session_id"]
        envelope: dict[str, Any] = {"request_id": identifier, "group_id": group, "session_id": session}
        preparing: asyncio.Task[tuple[BinaryIO, int, str]] | None = None
        reading: asyncio.Task[bytes] | None = None
        file: BinaryIO | None = None
        try:
            lease = self.current(group)
            envelope["revision"] = lease[1]
            if lease[0] != session:
                raise ValueError("Session changed before MCP read")
            typed = request["_content_types"]
            result = await self.fetch(request)
            if self.content_types() != typed:
                raise ValueError("MCP content format capability changed during read; no replay")
            preparing = asyncio.create_task(asyncio.to_thread(spool_mcp_data, result, content_types=typed))
            file, size, checksum = await asyncio.shield(preparing)
            if self.current(group) != lease or self.content_types() != typed:
                raise ValueError("Session/context/content format changed during MCP read")
            await self._send({"type": "mcp_data_begin", **envelope, "bytes": size, "sha256": checksum})
            index = 0
            assert file is not None
            while True:
                if self.current(group) != lease or self.content_types() != typed:
                    raise ValueError("Session/context/content format changed during MCP transfer")
                reading = asyncio.create_task(asyncio.to_thread(file.read, 64 * 1024))
                block = await asyncio.shield(reading)
                reading = None
                if self.current(group) != lease or self.content_types() != typed:
                    raise ValueError("Session/context/content format changed during MCP transfer")
                if not block:
                    await self._send({"type": "mcp_data_end", **envelope, "chunks": index})
                    break
                await self._send({"type": "mcp_data_chunk", **envelope, "index": index,
                                  "data": base64.b64encode(block).decode("ascii")})
                index += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                await self._send({"type": "mcp_data_error", **envelope, "error": str(exc)})
            except (OSError, TimeoutError):
                pass
        finally:
            # Threads cannot be force-canceled. Retain their one file owner until
            # creation/read settles, even on browser close or connection teardown.
            if preparing is not None and file is None:
                try:
                    file, _size, _checksum = await asyncio.shield(preparing)
                except Exception:
                    pass
            if reading is not None:
                await asyncio.gather(reading, return_exceptions=True)
            if file is not None:
                file.close()
