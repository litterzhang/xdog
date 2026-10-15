"""Reserve session mutations while keeping protocol readers/approval responses live."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.sessions.controller import SessionController

T = TypeVar('T')


async def run_control(session: AgentSession, operation: Callable[[], Awaitable[T]], *, name: str) -> T:
    runtime = session.runtime
    controller = (runtime.controller if runtime is not None
                  else cast('SessionController | None', getattr(session, '_sdk_controller', None)))
    if controller is None or controller.owns_current_task:
        return await operation()
    receipt = controller.control(operation, name=name)
    pending = asyncio.wrap_future(receipt)
    while not pending.done():
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            controller.cancel_control(receipt)
    return pending.result()
