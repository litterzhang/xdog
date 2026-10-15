"""One coding turn implementation, independent of terminal or transport."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from xdog.agent.events import AgentEvent
from xdog.agent.session_input import SessionInput, encode_input
from xdog.ai.types import ImageContent
from xdog.coding.core.sessions.models import TurnResult

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


async def run_turn(
    session: AgentSession,
    value: SessionInput,
    *,
    emit: Callable[[AgentEvent], None],
    images: tuple[ImageContent, ...] = (),
) -> TurnResult:
    unsubscribe = session.agent.subscribe(emit)
    try:
        text = value.instruction if value.origin == "human" else json.dumps(encode_input(value).data)
        response = await session._execute_message(
            text, images=images, input_origin="user" if value.origin == "human" else "session_input"
        )
        error = session.agent.state.error or ""
        return TurnResult("failed" if error else "succeeded", response, error)
    finally:
        if not session.agent.is_idle:
            await session.agent.cancel_and_wait()
        unsubscribe()
        # The producer and all its cleanup must settle before the controller releases ownership.
        await session.agent.wait_for_idle()
