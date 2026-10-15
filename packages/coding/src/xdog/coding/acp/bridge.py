"""Standard stdio facade for an already-running session, with no new runtime."""

from __future__ import annotations

from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.transport import serve
from xdog.coding.acp.backend import CodingBackend


async def run_bridge(session_id: str) -> int:
    await serve(AcpAdapter(CodingBackend(fixed_session=session_id)))
    return 0
