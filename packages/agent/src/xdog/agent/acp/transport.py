"""Use the SDK transport, closing only this connection's resources."""

from __future__ import annotations

import asyncio

try:
    import acp
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
from xdog.agent.acp.adapter import AcpAdapter


async def serve(
    adapter: AcpAdapter, reader: asyncio.StreamReader | None = None, writer: asyncio.StreamWriter | None = None
) -> None:
    try:
        await acp.run_agent(adapter, writer, reader, stdio_buffer_limit_bytes=1024 * 1024)
    finally:
        await adapter.aclose()
