"""Automatic naming is metadata on the real original session controller."""
import asyncio

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, Model, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.sessions.runtime import SessionRuntime


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.005)


def model(session, *, release=None, failed=False):
    calls, started, closed = [], asyncio.Event(), asyncio.Event()
    def stream(selected, context, options):
        title = isinstance(context.system_prompt, str) and context.system_prompt.startswith('Write a short')
        calls.append((title, context, options))
        message = AssistantMessage(content=(TextContent(text='bad\nname' if failed else
                                    '修复 Eyes 恢复' if title else 'Normal answer'),))
        if not title:
            return EventStream.empty(message)
        async def events():
            started.set()
            try:
                if release is not None:
                    await release.wait()
                yield DoneEvent(message=message)
            finally:
                closed.set()
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)
    session.agent.set_stream_fn(stream)
    session.model_info = Model(id=session.model)
    return calls, started, closed


async def test_first_human_turn_names_once_offline_resume_and_no_extra_history(coding_session):
    session = coding_session
    calls, _, _ = model(session)
    owner = SessionRuntime(session, bound=True)
    owner.start()
    try:
        response = await session.send_message('修复 Eyes 恢复')
        assert response.content[0].text == 'Normal answer'
        await until(lambda: session.session_data.name == '修复 Eyes 恢复')
        saved = session.session_manager.load_session(session.session_id)
        assert saved.name == session.session_data.name and saved.name_revision == 2
        assert len(saved.messages) == 2
        assert len([row for row in calls if row[0]]) == 1
        await session.send_message('继续工作')
        assert len([row for row in calls if row[0]]) == 1
        assert len(session.messages) == 4
    finally:
        await asyncio.wrap_future(owner.close())


@pytest.mark.parametrize('manual', ['Manual name', ''])
async def test_manual_name_or_clear_cancels_pending_result_and_retains_history(coding_session, manual):
    session = coding_session
    release = asyncio.Event()
    calls, started, closed = model(session, release=release)
    owner = SessionRuntime(session, bound=True)
    owner.start()
    try:
        await session.send_message('Original request')
        await asyncio.wait_for(started.wait(), 5)
        from xdog.coding.core.sessions.operations import run_control
        async def rename():
            session.rename_session(manual)
        await run_control(session, rename, name='manual-name')
        await asyncio.wait_for(closed.wait(), 5)
        release.set()
        await session.send_message('Second request')
        assert session.session_data.name == manual
        assert session.session_manager.load_session(session.session_id).name == manual
        assert len([row for row in calls if row[0]]) == 1
        assert len(session.messages) == 4
    finally:
        release.set()
        await asyncio.wrap_future(owner.close())


async def test_invalid_result_is_nonfatal_and_attempt_is_not_replayed(coding_session):
    session = coding_session
    calls, started, closed = model(session, failed=True)
    owner = SessionRuntime(session, bound=True)
    owner.start()
    try:
        assert await session.send_message('Original request') is not None
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.wait_for(closed.wait(), 5)
        await until(lambda: session._name_task is None)
        assert not session.session_data.name and session.session_data.name_revision == 1
        await session.send_message('Another request')
        assert len([row for row in calls if row[0]]) == 1
        assert session.session_manager.load_session(session.session_id).name_revision == 1
    finally:
        await asyncio.wrap_future(owner.close())


async def test_automatic_input_and_unknown_model_do_not_create_auxiliary_requests(coding_session):
    session = coding_session
    calls, _, _ = model(session)
    owner = SessionRuntime(session, bound=True)
    owner.start()
    try:
        await session.send_message('Untrusted automatic instruction', input_origin='system')
        assert not any(row[0] for row in calls) and session.session_data.name_revision == 0
        session.model_info = None
        await session.send_message('Human request without model metadata')
        assert not any(row[0] for row in calls)
    finally:
        await asyncio.wrap_future(owner.close())


async def test_close_cancels_and_joins_original_background_request(coding_session):
    session = coding_session
    calls, started, closed = model(session, release=asyncio.Event())
    owner = SessionRuntime(session, bound=True)
    owner.start()
    await session.send_message('Original request')
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.wrap_future(owner.close())
    assert closed.is_set() and calls[-1][2].cancel.is_set()
    assert not session._name_task and not session._retired_name_tasks


async def test_ready_title_waits_for_next_turn_commit_without_overwriting_history(coding_session):
    session = coding_session
    title_release, second_release = asyncio.Event(), asyncio.Event()
    title_started, second_started = asyncio.Event(), asyncio.Event()
    def stream(selected, context, options):
        title = isinstance(context.system_prompt, str) and context.system_prompt.startswith('Write a short')
        second = not title and any('Second request' in str(m.content) for m in context.messages)
        message = AssistantMessage(content=(TextContent(text='Task title' if title else 'Normal reply'),))
        if not title and not second:
            return EventStream.empty(message)
        async def events():
            (title_started if title else second_started).set()
            await (title_release if title else second_release).wait()
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)
    session.agent.set_stream_fn(stream)
    session.model_info = Model(id=session.model)
    owner = SessionRuntime(session, bound=True)
    owner.start()
    second = None
    try:
        await session.send_message('First request')
        await asyncio.wait_for(title_started.wait(), 5)
        second = asyncio.create_task(session.send_message('Second request'))
        await asyncio.wait_for(second_started.wait(), 5)
        title_release.set()
        assert not session.session_data.name
        second_release.set()
        await second
        await until(lambda: session.session_data.name == 'Task title')
        saved = session.session_manager.load_session(session.session_id)
        assert len(saved.messages) == 4 and len(session.messages) == 4
        assert 'Second request' in str(saved.messages[2].content)
    finally:
        title_release.set()
        second_release.set()
        if second is not None:
            await asyncio.gather(second, return_exceptions=True)
        await asyncio.wrap_future(owner.close())


async def test_unowned_internal_execution_cannot_start_background_work(coding_session):
    calls, _, _ = model(coding_session)
    from xdog.coding.core.session_naming import start
    await start(coding_session, 'No host controller owns this caller loop')
    assert not calls and coding_session._name_task is None
    assert coding_session.session_data.name_revision == 0


def test_coding_terminal_title_tracks_current_name_and_clear(coding_session, monkeypatch):
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    ui = InteractiveMode(coding_session)
    titles = []
    monkeypatch.setattr(ui._tui, 'set_title', titles.append)
    try:
        ui._poll()
        coding_session.session_data.name = '修复会话标题'
        ui._poll()
        coding_session.session_data.name = ''
        ui._poll()
        assert titles == ['xdog-coding', '修复会话标题', 'xdog-coding']
    finally:
        ui._request_exit()
        ui._session_events.close()
