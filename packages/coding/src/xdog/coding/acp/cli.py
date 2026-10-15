"""ACP mode dispatch happens before constructing any coding session."""

from __future__ import annotations

from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.transport import serve
from xdog.coding.acp.backend import CodingBackend
from xdog.coding.acp.bridge import run_bridge


async def run_acp(session_id: str | None = None) -> int:
    if session_id is not None:
        return await run_bridge(session_id)
    await serve(AcpAdapter(CodingBackend()))
    return 0
