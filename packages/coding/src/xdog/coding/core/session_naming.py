"""Best-effort name metadata on the original Coding owner, never a conversation turn."""
from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import TYPE_CHECKING

from xdog.agent.async_work import run_owned
from xdog.agent.session_name import suggest_name

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession

logger = logging.getLogger(__name__)


def cancel(session: AgentSession) -> None:
    task, session._name_task = session._name_task, None
    if task is not None:
        session._retired_name_tasks.append(task)
        if not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel)


async def close(session: AgentSession) -> None:
    from xdog.agent.cross_loop import settle_on

    cancel(session)
    tasks, session._retired_name_tasks = session._retired_name_tasks, []
    async def join(task: asyncio.Task[None]) -> None:
        await asyncio.gather(task, return_exceptions=True)
    for task in tasks:
        if not task.get_loop().is_closed():
            await settle_on(task.get_loop(), join(task))


async def start(session: AgentSession, prompt: str) -> None:
    # Never leave background work on an unowned caller loop. Normal SDK/TUI
    # admission supplies the existing controller and its close receipt.
    owner = session.runtime.controller if session.runtime is not None else session._sdk_controller
    if owner is None:
        return
    # Auxiliary calls need the actual selected adapter/model, not a guessed provider.
    if (session.model_info is None or session.session_data.name or session.session_data.name_revision
            or session.model.startswith('ext-')
            or session._sdk_disposing or not prompt.strip() or prompt.lstrip().startswith('/')):
        return
    scope = session.session_id, session.session_data.active_branch
    from xdog.agent.extensions.providers import ExtensionProviderStream
    model = session.model
    adapter = session.agent.stream_fn
    stream = adapter.base if isinstance(adapter, ExtensionProviderStream) else adapter
    def current() -> bool:
        return (not session._sdk_disposing and (session.session_id, session.session_data.active_branch) == scope
                and session.model == model)
    try:
        await run_owned(session.rename_session, '', expected_name='', expected_revision=0, alive=current)
    except (ValueError, RuntimeError, OSError):
        logger.debug('Automatic session name admission unavailable')
        return
    revision = session.session_data.name_revision

    async def generate() -> None:
        try:
            title = await suggest_name(stream, model, prompt)
            if not current():
                return
            from xdog.coding.core.sessions.operations import run_control
            async def commit() -> None:
                def alive() -> bool:
                    return current() and session.session_data.name_revision == revision
                if alive():
                    await run_owned(session.rename_session, title, expected_name='',
                                    expected_revision=revision, alive=alive)
                    owner = session.runtime.controller if session.runtime is not None else session._sdk_controller
                    if owner is not None:
                        owner.publish('session_name', {'name': title})
            await run_control(session, commit, name='session-name')
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never include a model response, request text or provider exception in diagnostics.
            logger.debug('Automatic session name unavailable')
        finally:
            if session._name_task is asyncio.current_task():
                session._name_task = None

    # Do not inherit the executing turn's authority/control context into an auxiliary job.
    session._name_task = asyncio.create_task(generate(), name='coding-session-name', context=contextvars.Context())
