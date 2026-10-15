"""An opt-in official-SDK stdio client; no harness imports or tool execution."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, cast

try:
    from acp import PROTOCOL_VERSION, spawn_agent_process
    from acp import RequestError as RequestError
    from acp.client.connection import ClientSideConnection
    from acp.interfaces import Client
    from acp.schema import DeniedOutcome, Implementation, PromptResponse, RequestPermissionResponse, TextContentBlock
except ImportError as exc:
    raise ImportError("Eyes ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc


class AcpClient:
    def __init__(self, command: tuple[str, ...], session_id: str, cwd: Path) -> None:
        self.command, self.session_id, self.cwd = command, session_id, cwd.resolve()
        self.conn: ClientSideConnection | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.owner_generation: str | None = None
        self.extensions: dict[str, int] = {}
        self.updates = 0
        self._stack = AsyncExitStack()
        self._close_task: asyncio.Task[None] | None = None
        self._opened = False

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if session_id != self.session_id:
            raise ValueError("Unexpected ACP session update")
        self.updates += 1  # Progress is not completion; never retain an unbounded response stream.

    async def request_permission(self, **kwargs: Any) -> RequestPermissionResponse:
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def connect(self) -> None:
        if self._opened or self._close_task is not None:
            raise RuntimeError("ACP connections cannot be automatically reattached")
        self._opened = True
        if not self.command or any(not arg or "\x00" in arg for arg in self.command):
            raise ValueError("ACP command requires nonempty argv without NUL")
        try:
            async with asyncio.timeout(10):
                self.conn, self.process = await self._stack.enter_async_context(
                    spawn_agent_process(
                        cast(Client, self),
                        self.command[0],
                        *self.command[1:],
                        cwd=self.cwd,
                        env=dict(os.environ),
                        transport_kwargs={"stderr": asyncio.subprocess.DEVNULL, "limit": 1024 * 1024},
                    )
                )
                assert self.conn is not None
                response = await self.conn.initialize(
                    protocol_version=PROTOCOL_VERSION, client_info=Implementation(name="xdog-eyes", version="2.4.1")
                )
                if response.protocol_version != PROTOCOL_VERSION:
                    raise ValueError("Unsupported ACP protocol version")
                if response.agent_capabilities is None or response.agent_capabilities.load_session is not True:
                    raise ValueError("ACP agent must support explicit session load")
                meta = response.field_meta or {}
                namespace = meta.get("xdog", {})
                advertised = namespace.get("extensions", {}) if isinstance(namespace, dict) else {}
                if isinstance(advertised, dict):
                    self.extensions = {
                        key: 1
                        for key in ("session_info", "resume")
                        if type(advertised.get(key)) is int and advertised[key] == 1
                    }
                await self.conn.load_session(session_id=self.session_id, cwd=str(self.cwd), mcp_servers=[])
                if "session_info" in self.extensions:
                    self.owner_generation = await self.generation()
        except BaseException:
            await self.close()
            raise

    async def generation(self) -> str:
        if self.conn is None or "session_info" not in self.extensions:
            raise RuntimeError("Owner generation is not negotiated")
        info = await self.conn.ext_method("xdog/session_info", {"session_id": self.session_id})
        generation, cwd = info.get("generation"), info.get("cwd")
        if (
            info.get("session_id") != self.session_id
            or not isinstance(generation, str)
            or not generation
            or "\x00" in generation
            or len(generation) > 256
            or not isinstance(cwd, str)
            or not Path(cwd).is_absolute()
            or Path(cwd).resolve() != self.cwd
        ):
            raise ValueError("Invalid ACP owner identity")
        return generation

    async def prompt(self, parameters: dict[str, Any]) -> PromptResponse:
        if self.conn is None:
            raise RuntimeError("ACP connection is not active")
        # The SDK wraps kwargs in _meta. Passing _meta=... here would incorrectly
        # nest it; only our source-neutral namespace is passed as a kwarg.
        return await self.conn.prompt(
            session_id=self.session_id,
            prompt=[TextContentBlock.model_validate(item) for item in parameters["prompt"]],
            **parameters["_meta"],
        )

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stack.aclose())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self.conn = None
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError
