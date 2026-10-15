"""Reserve session mutations while keeping protocol readers/approval responses live."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession

T = TypeVar('T')


async def run_control(session: AgentSession, operation: Callable[[], Awaitable[T]], *, name: str) -> T:
    runtime = session.runtime
    if runtime is None or runtime.controller.owns_current_task:
        return await operation()
    receipt = runtime.controller.control(operation, name=name)
    pending = asyncio.wrap_future(receipt)
    while not pending.done():
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            runtime.controller.cancel_control(receipt)
    return pending.result()
