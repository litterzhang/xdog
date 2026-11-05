"""One group/session-owned metadata suggestion; no Agent history or output delivery."""
from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from xdog.agent.async_work import run_owned
from xdog.agent.session_name import suggest_name

if TYPE_CHECKING:
    from xdog.claw.core.runtime.group import GroupRuntime
    from xdog.claw.core.runtime.group_controller import GroupController
    from xdog.claw.core.runtime.session import AgentSession

logger = logging.getLogger(__name__)


def cancel(session: AgentSession) -> None:
    task, session._name_task = session._name_task, None
    if task is not None:
        session._runtime._retired_name_tasks.append(task)
        if not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel)


async def settle(runtime: GroupRuntime) -> None:
    tasks, runtime._retired_name_tasks = runtime._retired_name_tasks, []
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def close(session: AgentSession) -> None:
    cancel(session)
    await settle(session._runtime)


async def start(session: AgentSession, prompt: str, controller: GroupController,
                publish: Callable[[str, str, str], None]) -> None:
    runtime = session._runtime
    if (session._model_metadata.metadata is None or session.meta.label or session.meta.name_revision
            or session.agent.state.model.startswith('ext-')
            or runtime._closing or not prompt.strip() or prompt.lstrip().startswith('/')):
        return
    identifier, model, stream = session.meta.session_id, session.agent.state.model, session._name_stream
    def current() -> bool:
        return (not runtime._closing and runtime._active_session is session and session.meta.session_id == identifier
                and session.agent.state.model == model)
    try:
        await run_owned(runtime.rename_session, identifier, '', expected_name='', expected_revision=0, alive=current)
    except (ValueError, RuntimeError, OSError):
        logger.debug('Automatic session name admission unavailable')
        return
    revision = session.meta.name_revision

    async def generate() -> None:
        try:
            title = await suggest_name(stream, model, prompt)
            if not current():
                return
            from xdog.claw.core.runtime.context_controls import rename_session
            def alive() -> bool:
                return current() and session.meta.name_revision == revision
            await rename_session(runtime, controller, identifier, title, expected_name='',
                                 expected_revision=revision, alive=alive)
            if current():
                publish(runtime.group.id, identifier, title)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug('Automatic session name unavailable')
        finally:
            if session._name_task is asyncio.current_task():
                session._name_task = None

    session._name_task = asyncio.create_task(generate(), name='claw-session-name', context=contextvars.Context())
