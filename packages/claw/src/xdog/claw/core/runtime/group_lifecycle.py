"""Group-owned lifecycle operations; no routing, UI or extra execution loop."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from functools import partial
from typing import TYPE_CHECKING

from xdog.agent import StreamFn
from xdog.agent.async_work import run_owned
from xdog.claw.core.persistence import session_preparation

if TYPE_CHECKING:
    from xdog.claw.core.runtime.group import GroupRuntime
    from xdog.claw.core.runtime.session import AgentSession


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
        runtime.require_open()
        if (runtime._active_session is not previous or runtime._source_revision != generation
                or (runtime.get_new_session_id() or '') != expected_session):
            raise ValueError('Reset owner changed during preparation')
        draft = None
        if plan.meta is not None:
            from xdog.claw.core.runtime.context_handoff import prepare
            adapter = (previous.agent.stream_fn if previous is not None else runtime.stream_fn
                       if runtime.stream_fn is not None else await run_owned(session_preparation.default_stream))
            draft = await prepare(runtime, plan.meta, 'manual_reset', previous, adapter)
            runtime.require_open()
            if (runtime._active_session is not previous or runtime._source_revision != generation
                    or (runtime.get_new_session_id() or '') != expected_session):
                raise ValueError('Reset owner changed during handoff')
        runtime._drop_session()  # Retire on the Agent owner, never its IO worker.
        await run_owned(session_preparation.reset, runtime.transcript_store, runtime.group.id, plan.meta, draft)


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
            await settle(session.agent.aclose(), session.children.runtime.aclose())
        await settle(runtime.capabilities.aclose())
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


async def prepare_session(runtime: GroupRuntime, *, allow_reset: bool = True) -> AgentSession:
    """Owned preparation IO; publish/construct the Agent only on this loop."""
    from functools import partial

    from xdog.agent.async_work import run_owned
    from xdog.claw.core.persistence.session_preparation import check, default_stream, prepare
    from xdog.claw.core.runtime.session import AgentSession

    runtime.require_open()
    async with runtime._session_prepare_lock:
        runtime.require_open()
        previous = runtime._active_session
        generation = runtime._source_revision
        config = runtime.group.config
        children_active = previous is not None and previous.children.runtime.active
        plan = await run_owned(partial(check, runtime.transcript_store, runtime.group.id,
            config.daily_reset_hour, config.idle_reset_seconds, allow_reset=allow_reset and not children_active))
        runtime.require_open()
        if runtime._active_session is not previous or runtime._source_revision != generation:
            raise ValueError('Session preparation owner changed during metadata read')
        if previous is not None:
            if plan.meta is None or plan.meta.session_id != previous.meta.session_id:
                raise ValueError('Cached session owner changed during preparation')
            if not plan.reset_due:
                runtime._session_last_active = time.time()
                return previous
        generation = runtime._source_revision
        configured_stream = runtime.stream_fn
        adapter: StreamFn = configured_stream if configured_stream is not None else await run_owned(default_stream)
        runtime.require_open()
        if (runtime._source_revision != generation or runtime._active_session is not previous
                or runtime.stream_fn is not configured_stream):
            raise ValueError("Model adapter owner changed during preparation")
        draft = None
        if plan.reset_due and plan.meta is not None:
            from xdog.claw.core.runtime.context_handoff import prepare as prepare_handoff
            draft = await prepare_handoff(runtime, plan.meta, plan.reset_reason, previous, adapter)
            runtime.require_open()
            if (runtime._source_revision != generation or runtime._active_session is not previous
                    or runtime.stream_fn is not configured_stream):
                raise ValueError('Handoff owner changed before rotation')
        if previous is not None:
            runtime._drop_session()
        generation = runtime._source_revision
        data = await run_owned(partial(prepare, runtime.transcript_store, runtime.group.id,
            runtime.workspace_dir, runtime._configured_model, plan.meta, reset=plan.reset_due,
            skills=runtime.skill_repo,
            extension_root=runtime.extension_root, stream_fn=adapter, handoff=draft,
            reset_reason=plan.reset_reason or 'automatic_reset'))
        runtime.require_open()
        if (runtime._active_session is not None or runtime._source_revision != generation
                or runtime.stream_fn is not configured_stream):
            raise ValueError('Session preparation owner changed before publication')
        runtime.model = data.saved_model or runtime._configured_model
        runtime.context_window, runtime.max_prompt_tokens = runtime._configured_limits
        session = AgentSession(runtime=runtime, session_meta=data.meta, prepared=data, prepared_stream=adapter)
        runtime._active_session = session
        runtime._attach_capabilities(session)
        runtime._session_last_active = time.time()
        return session
