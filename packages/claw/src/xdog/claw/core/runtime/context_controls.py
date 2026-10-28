"""Owner-bound context controls; no application-wide runtime access."""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from xdog.agent.async_work import run_owned
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.group_controller import GroupController


async def navigation_entries(runtime: GroupRuntime, scope: str) -> list[dict[str, Any]]:
    return await run_owned(runtime.navigation_entries, scope)


async def navigate_session(runtime: GroupRuntime, controller: GroupController,
                           action: str, session_id: str, branch_id: str,
                           index: int, expected_session: str) -> None:
    if action != 'new':
        raise ValueError('Claw history is read-only; historical route activation is not supported')
    if session_id or branch_id or index != -1:
        raise ValueError('New context cannot select historical data')
    async with controller.operation(context_change=True,
                                    validate=lambda: runtime.require_current(expected_session)):
        await runtime.reset_context(expected_session)


async def read_session_name(runtime: GroupRuntime, controller: GroupController,
                            expected_session: str) -> dict[str, str]:
    # Metadata reads do not reserve the group, instantiate a session, read
    # history, or block the gateway loop waiting for a disk owner's lock.
    async with asyncio.timeout(10):
        while True:
            if not controller.context_changing:
                value = runtime.transcript_store.read_session_name(runtime.group.id, expected_session)
                if value is not None:
                    return value
            await asyncio.sleep(0.005)


async def rename_session(runtime: GroupRuntime, controller: GroupController,
                         expected_session: str, name: str, *,
                         expected_name: str | None = None, alive: Callable[[], bool] | None = None) -> str:
    async with controller.operation(context_change=True,
                                    validate=lambda: runtime.require_current(expected_session)):
        canceled = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(
            runtime.rename_session, expected_session, name,
            lambda: not canceled.is_set() and (alive is None or alive()),
            **({'expected_name': expected_name} if expected_name is not None else {})))
        # Once disk admission happens, settle publication before releasing group ownership.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                canceled.set()
        return task.result()
