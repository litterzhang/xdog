"""Ordinary turn history commits once, off-loop, before producer completion."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream


async def test_turn_commits_history_once_off_loop_and_publishes_after_commit(coding_session, monkeypatch):
    runtime = coding_session.enable_runtime(bound=True)
    runtime.start(own_thread=True, setup_pending=True)
    await asyncio.wrap_future(runtime.controller._async(runtime.activate_inputs))
    coding_session.agent.set_stream_fn(lambda *_: EventStream.empty(
        AssistantMessage(content=(TextContent(text='committed answer'),))))
    entered, release = threading.Event(), threading.Event()
    actual_save = coding_session.session_manager.save_session
    calls = []
    def save(snapshot, **kwargs):
        if len(snapshot.messages) >= 2:
            calls.append(threading.current_thread())
            entered.set()
            assert release.wait(6)
        return actual_save(snapshot, **kwargs)
    monkeypatch.setattr(coding_session.session_manager, 'save_session', save)
    original = list(coding_session.session_data.messages)
    turn = asyncio.create_task(coding_session.send_message('hello'))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert calls == [calls[0]] and calls[0] is not runtime.controller._thread
        assert coding_session.session_data.messages == original, 'Published uncommitted live history'
        await asyncio.wait_for(asyncio.wrap_future(runtime.controller._async(lambda: asyncio.sleep(0))), .5)
        assert not turn.done()
        release.set()
        await turn
        assert len(calls) == 1, 'AgentEnd and producer end must not save the same history twice'
        saved = coding_session.session_manager.load_session(coding_session.session_id)
        assert len(saved.messages) == len(coding_session.session_data.messages) == 2
        assert saved.summary == 'committed answer'
    finally:
        release.set()
        await asyncio.gather(turn, return_exceptions=True)
        await coding_session.aclose()


async def test_failed_history_commit_preserves_original_published_state_and_fences_next_turn(
        coding_session, monkeypatch):
    actual_save = coding_session.session_manager.save_session
    calls = []
    def save(snapshot, **kwargs):
        if snapshot.messages:
            calls.append(True)
            raise OSError('disk unavailable')
        return actual_save(snapshot, **kwargs)
    monkeypatch.setattr(coding_session.session_manager, 'save_session', save)
    try:
        with pytest.raises(OSError, match='disk unavailable'):
            await coding_session.send_message('executed tail')
        assert not coding_session.session_data.messages
        assert coding_session.agent.messages, 'Do not delete the uncertain executed tail'
        with pytest.raises(RuntimeError, match='persistence|history'):
            await coding_session.send_message('must not implicitly replay or overwrite')
        assert calls == [True]
    finally:
        await coding_session.aclose()


async def test_extension_entry_commit_stages_immutable_original_owner_before_publication(tmp_path, monkeypatch):
    from packages.coding.tests.extension_session_helpers import session as extension_session
    session, _ = extension_session(tmp_path, extra=('contextual',))
    runtime = session.enable_runtime(bound=True)
    runtime.start(own_thread=True, setup_pending=True)
    await asyncio.wrap_future(runtime.controller.control(session.initialize_extensions, name='initialize'))
    data = session.session_data
    entries = session.extensions.entries
    original_save = session.session_manager.save_session
    observed = []
    def save(snapshot, **kwargs):
        if not observed and snapshot.extension_entries.get('contextual'):
            observed.append(snapshot is data)
            assert data.extension_entries == {}, 'Published entries before their actual commit'
        return original_save(snapshot, **kwargs)
    monkeypatch.setattr(session.session_manager, 'save_session', save)
    try:
        await asyncio.to_thread(entries.append, 'contextual', [{'kind': 'note', 'data': {'marker': 'original'}}])
        assert observed == [False], 'Callback persistence leaked its mutable owner into disk IO'
        assert data.extension_entries['contextual'][0]['data']['marker'] == 'original'
        await asyncio.to_thread(session.new_session)
        with pytest.raises(RuntimeError, match='closed|owner'):
            await asyncio.to_thread(entries.append, 'contextual', [{'kind': 'note', 'data': 'stale'}])
        saved = session.session_manager.load_session(session.session_id)
        assert not saved.extension_entries.get('contextual')
    finally:
        await session.aclose()


async def test_restored_branch_binds_successor_extension_entries_to_its_exact_branch(tmp_path):
    from packages.coding.tests.extension_session_helpers import session as extension_session
    session, _ = extension_session(tmp_path, extra=('contextual',))
    runtime = session.enable_runtime(bound=True)
    runtime.start(own_thread=True, setup_pending=True)
    await asyncio.wrap_future(runtime.controller.control(session.initialize_extensions, name='initialize'))
    try:
        branch = await asyncio.to_thread(session.create_branch)
        assert await asyncio.to_thread(session.restore_branch, branch)
        entries = session.extensions.entries
        await asyncio.to_thread(entries.append, 'contextual', [{'kind': 'note', 'data': 'successor'}])
        saved = session.session_manager.load_session(session.session_id)
        assert saved.active_branch == branch
        assert saved.extension_entries['contextual'][0]['data'] == 'successor'
    finally:
        await session.aclose()
