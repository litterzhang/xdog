"""One coding turn implementation, independent of terminal or transport."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from xdog.agent.events import AgentEvent
from xdog.coding.core.sessions.models import TurnResult

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.sessions.batches import TurnBatch




async def run_batch(session: AgentSession, batch: TurnBatch, *, emit: Callable[[AgentEvent], None]) -> TurnResult:
    """One stream shared by all members; controller owns per-message result settlement."""
    unsubscribe = session.agent.subscribe(emit)
    try:
        response = await session._execute_messages(batch.items, batch_id=batch.id)
        error = session.agent.state.error or ''
        return TurnResult('failed' if error else 'succeeded', response, error, batch_id=batch.id)
    finally:
        if not session.agent.is_idle:
            await session.agent.cancel_and_wait()
        unsubscribe()
        await session.agent.wait_for_idle()
