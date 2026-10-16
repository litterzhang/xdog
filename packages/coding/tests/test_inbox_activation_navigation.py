"""A retired startup attempt cannot poison the successor controller's durable inbox."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.coding.core.sessions.runtime import SessionRuntime


async def test_startup_activation_racing_published_navigation_does_not_break_successor(coding_session, monkeypatch):
    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start(setup_pending=True)
    inbox = runtime.inbox
    entered, release = asyncio.Event(), asyncio.Event()
    published, continue_navigation = threading.Event(), threading.Event()
    initialize, load_access = inbox._initialize, coding_session._load_access
    old = coding_session.session_id

    async def held_initialize():
        entered.set()
        await release.wait()
        await initialize()

    def held_load_access():
        load_access()
        if coding_session.session_id != old:
            published.set()
            assert continue_navigation.wait(5)

    monkeypatch.setattr(inbox, '_initialize', held_initialize)
    monkeypatch.setattr(coding_session, '_load_access', held_load_access)
    monkeypatch.setattr(coding_session, '_resolve_selected_model', lambda _: None)
    activation = asyncio.create_task(inbox.activate())
    navigation = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        async def navigate():
            return await asyncio.to_thread(coding_session.new_session)
        navigation = asyncio.wrap_future(runtime.controller.control(navigate, name='new'))
        async with asyncio.timeout(5):
            while not published.is_set():
                await asyncio.sleep(0.001)
        release.set()
        with pytest.raises(RuntimeError, match='context/generation/branch'):
            await activation
        assert runtime.controller._broken is None, 'A stale startup must not poison the new session owner'
        continue_navigation.set()
        new = await asyncio.wait_for(navigation, 5)
        assert new != old and runtime.inbox is not inbox
        assert runtime.inbox.session_id == new and runtime.inbox._enabled
        assert runtime.controller._broken is None and runtime.controller._inputs_ready
    finally:
        release.set()
        continue_navigation.set()
        await asyncio.gather(activation, return_exceptions=True)
        if navigation is not None:
            await asyncio.gather(navigation, return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


async def test_current_activation_storage_failure_still_breaks_owner(coding_session, monkeypatch):
    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start(setup_pending=True)
    async def fail():
        raise OSError('persistent storage failure')
    monkeypatch.setattr(runtime.inbox, '_initialize', fail)
    try:
        with pytest.raises(OSError, match='persistent storage'):
            await runtime.inbox.activate()
        assert isinstance(runtime.controller._broken, OSError)
        assert runtime.controller._inputs_ready  # Accepted inputs settle failed, not stranded/replayed.
    finally:
        await asyncio.wrap_future(runtime.close())
