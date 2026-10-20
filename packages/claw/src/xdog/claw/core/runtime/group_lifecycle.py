"""Group-owned lifecycle operations; no routing, UI or extra execution loop."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from functools import partial
from typing import TYPE_CHECKING

from xdog.agent.async_work import run_owned
from xdog.claw.core.persistence import session_preparation

if TYPE_CHECKING:
    from xdog.claw.core.runtime.group import GroupRuntime


async def reset_context(runtime: GroupRuntime, expected_session: str) -> None:
    """Rotate on the original owner loop after detached persistence checks."""
    async with runtime._session_prepare_lock:
        runtime.require_open()
        if (runtime.get_new_session_id() or '') != expected_session:
            raise ValueError('Active context changed; refresh before creating a new context')
        previous = runtime._active_session
        if previous is not None:
            previous._children_idle()
            if previous.agent.is_streaming:
                raise RuntimeError('Context reset requires an idle agent')
        generation = runtime._source_revision
        config = runtime.group.config
        plan = await run_owned(partial(session_preparation.check, runtime.transcript_store, runtime.group.id,
            config.daily_reset_hour, config.idle_reset_seconds, allow_reset=False))
        if (runtime._active_session is not previous or runtime._source_revision != generation
                or (runtime.get_new_session_id() or '') != expected_session):
            raise ValueError('Reset owner changed during preparation')
        runtime._drop_session()  # Retire on the Agent owner, never its IO worker.
        await run_owned(session_preparation.reset, runtime.transcript_store, runtime.group.id, plan.meta)


async def close(runtime: GroupRuntime) -> None:
    """Settle every owned resource, then report failures without rerunning cleanup."""
    errors: list[BaseException] = []

    async def settle(*work: Awaitable[None]) -> None:
        errors.extend(result for result in await asyncio.gather(*work, return_exceptions=True)
                      if isinstance(result, BaseException))

    # Closing fences new publication; wait for any already-owned preparation.
    async with runtime._session_prepare_lock:
        try:
            await runtime.settle_capabilities()
        except BaseException as exc:
            errors.append(exc)
        session = runtime._active_session
        owners = tuple(runtime._retired_extensions)
        if session is not None:
            owners += (session.extensions,)
        if session is not None:
            await settle(session.agent.aclose(), session.children.supervisor.aclose())
        if runtime.goal_host is not None:
            await settle(runtime.goal_host.aclose())
        await settle(*(owner.aclose() for owner in owners))
        await settle(runtime.mcp.aclose())
        if runtime.memory is not None:
            await settle(runtime.memory.close())
        if session is not None:
            try:
                session.dispose()
            except BaseException as exc:
                errors.append(exc)
        runtime._retired_extensions.clear()
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup('Group resource cleanup failed', errors)
