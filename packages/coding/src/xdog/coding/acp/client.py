"""Attach to a discovered live owner; never read its history as a writable copy."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from acp import PROTOCOL_VERSION, connect_to_agent
from acp.interfaces import Client
from acp.schema import DeniedOutcome, RequestPermissionResponse, TextContentBlock
from xdog.agent.acp.contracts import UpdateSink
from xdog.coding.core.sessions.models import TurnResult
from xdog.coding.core.sessions.ownership import OwnerRecord, OwnerRegistry, runtime_directory


class AcpAttachment:
    def __init__(self, record: OwnerRecord) -> None:
        self.record = record
        self.sink: UpdateSink | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.conn: Any = None

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if session_id != self.record.session_id:
            raise ValueError("Unexpected session output")
        if self.sink is not None:
            await self.sink(update)

    async def request_permission(self, **kwargs: Any) -> RequestPermissionResponse:
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def connect(self, sink: UpdateSink | None = None) -> None:
        self.sink = sink
        reader, self.writer = await asyncio.open_unix_connection(self.record.address, limit=1024 * 1024)
        self.conn = connect_to_agent(cast(Client, self), self.writer, reader)
        try:
            await self.conn.initialize(protocol_version=PROTOCOL_VERSION)
            await self.conn.load_session(session_id=self.record.session_id, cwd=str(self.record.cwd), mcp_servers=[])
            info = await self.conn.ext_method("xdog/session_info", {"session_id": self.record.session_id})
            if info.get("generation") != self.record.generation or info.get("session_id") != self.record.session_id:
                raise RuntimeError("Session owner generation changed")
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
            self.writer = None


async def send_prompt(session_id: str, text: str) -> TurnResult:
    from xdog.coding.config import get_sessions_dir

    registry = OwnerRegistry(runtime_directory(get_sessions_dir()))
    attachment = AcpAttachment(registry.lookup(session_id))
    parts = []

    async def output(update: Any) -> None:
        content = getattr(update, "content", None)
        if isinstance(content, TextContentBlock):
            parts.append(content.text)

    try:
        await attachment.connect(output)
        response = await attachment.conn.prompt(
            session_id=session_id, prompt=[TextContentBlock(type="text", text=text)]
        )
        from xdog.ai.types import AssistantMessage, TextContent

        return TurnResult(
            "cancelled" if response.stop_reason == "cancelled" else "succeeded",
            AssistantMessage(content=(TextContent(text="".join(parts)),)),
        )
    finally:
        await attachment.close()
