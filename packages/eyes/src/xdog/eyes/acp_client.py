"""An opt-in official-SDK ACP client; no harness imports or tool execution."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Literal, cast

from xdog.eyes.connections import ConnectionDef, StdioConnection, UnixConnection, validate_endpoint

try:
    from acp import PROTOCOL_VERSION, connect_to_agent, spawn_agent_process
    from acp import RequestError as RequestError
    from acp.client.connection import ClientSideConnection
    from acp.interfaces import Client
    from acp.schema import DeniedOutcome, Implementation, PromptResponse, RequestPermissionResponse, TextContentBlock
except ImportError as exc:
    raise ImportError("Eyes ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc


class AcpClient:
    def __init__(
        self, connection: ConnectionDef, session_id: str, cwd: Path, *,
        delivery: Literal["prompt", "enqueue"] = "prompt",
    ) -> None:
        self.connection, self.session_id, self.cwd = connection, session_id, cwd.resolve()
        self.delivery = delivery
        self.conn: ClientSideConnection | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.owner_generation: str | None = None
        self.inbox_id: str | None = None
        self.branch_id: str | None = None
        self.extensions: dict[str, int] = {}
        self._reader: asyncio.StreamReader | None = None
        self.updates = 0
        self._stack = AsyncExitStack()
        self._close_task: asyncio.Task[None] | None = None
        self._opened = False

    @property
    def connected(self) -> bool:
        return (self.conn is not None and self._close_task is None
                and (self._reader is None or not self._reader.at_eof())
                and (self.process is None or self.process.returncode is None))

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
        try:
            async with asyncio.timeout(10):
                if isinstance(self.connection, UnixConnection):
                    await asyncio.to_thread(validate_endpoint, self.connection)
                    reader, writer = await asyncio.open_unix_connection(self.connection.path, limit=1024 * 1024)
                    self._reader = reader
                    self._stack.push_async_callback(self._close_writer, writer)
                    self.conn = connect_to_agent(cast(Client, self), writer, reader)
                    self._stack.push_async_callback(self.conn.close)
                elif isinstance(self.connection, StdioConnection):
                    self.conn, self.process = await self._stack.enter_async_context(
                        spawn_agent_process(
                            cast(Client, self), self.connection.command[0], *self.connection.command[1:],
                            cwd=self.cwd, env=dict(os.environ),
                            transport_kwargs={"stderr": asyncio.subprocess.DEVNULL, "limit": 1024 * 1024},
                        )
                    )
                    self._reader = self.process.stdout if self.process is not None else None
                else:
                    raise ValueError("Unsupported ACP connection")
                assert self.conn is not None
                response = await self.conn.initialize(
                    protocol_version=PROTOCOL_VERSION, client_info=Implementation(name="xdog-eyes", version="2.4.6")
                )
                if response.protocol_version != PROTOCOL_VERSION:
                    raise ValueError("Unsupported ACP protocol version")
                meta = response.field_meta or {}
                namespace = meta.get("xdog", {})
                advertised = namespace.get("extensions", {}) if isinstance(namespace, dict) else {}
                if isinstance(advertised, dict):
                    self.extensions = {
                        key: 1
                        for key in ("session_info", "resume", "enqueue", "inbox_status")
                        if type(advertised.get(key)) is int and advertised[key] == 1
                    }
                if self.delivery == 'enqueue':
                    if not {'enqueue', 'inbox_status'} <= self.extensions.keys():
                        raise ValueError('ACP target must advertise durable enqueue/inbox_status v1')
                    scope = await self.inbox_status()
                    self.inbox_id, self.branch_id = scope['inbox_id'], scope['branch_id']
                else:
                    if response.agent_capabilities is None or response.agent_capabilities.load_session is not True:
                        raise ValueError("ACP agent must support explicit session load")
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

    async def inbox_status(self, message_id: str | None = None) -> dict[str, Any]:
        if self.conn is None or 'inbox_status' not in self.extensions:
            raise RuntimeError('Durable inbox status is not negotiated')
        params = {'session_id': self.session_id, 'cwd': str(self.cwd)}
        if message_id is not None:
            params['message_id'] = message_id
        result = await self.conn.ext_method('xdog/inbox_status', params)
        branch, namespace, cwd = result.get('branch_id'), result.get('inbox_id'), result.get('cwd')
        if (result.get('session_id') != self.session_id or not isinstance(cwd, str)
                or not Path(cwd).is_absolute() or Path(cwd).resolve() != self.cwd
                or not isinstance(branch, str) or '\x00' in branch or len(branch.encode()) > 256
                or not isinstance(namespace, str) or not namespace.strip() or '\x00' in namespace
                or len(namespace.encode()) > 256 or not isinstance(result.get('messages'), list)):
            raise ValueError('Invalid durable ACP inbox scope')
        if self.inbox_id is not None and (self.inbox_id != namespace or self.branch_id != branch):
            raise ValueError('ACP inbox namespace/branch changed; explicit rebaseline required')
        return result

    async def enqueue(self, parameters: dict[str, Any]) -> dict[str, Any]:
        if self.conn is None or 'enqueue' not in self.extensions:
            raise RuntimeError('Durable enqueue is not negotiated')
        result = await self.conn.ext_method('xdog/enqueue', parameters)
        states = {'pending', 'claimed', 'pending_review', 'approved', 'running',
                  'succeeded', 'failed', 'cancelled', 'interrupted'}
        if (result.get('session_id') != self.session_id or result.get('message_id') != parameters['message_id']
                or result.get('branch_id') != self.branch_id or result.get('status') not in {'queued', 'duplicate'}
                or result.get('message_state') not in states):
            raise ValueError('Invalid durable enqueue receipt; acceptance is unknown')
        return result

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

    @staticmethod
    async def _close_writer(writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            async with asyncio.timeout(5):
                await writer.wait_closed()
        except (OSError, TimeoutError):
            pass

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
