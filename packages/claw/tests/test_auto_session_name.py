"""Real group metadata transactions and owner gates for automatic names."""
import asyncio
from dataclasses import replace

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, Model, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.group_controller import GroupController
from xdog.claw.core.runtime.session_naming import start
from xdog.claw.core.types import Group


@pytest.fixture
async def named_owner(tmp_path):
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    def stream(model, context, options):
        calls.append((model, context, options))
        message = AssistantMessage(content=(TextContent(text='修复 Eyes 恢复'),))
        async def events():
            entered.set()
            try:
                await release.wait()
                yield DoneEvent(message=message)
            finally:
                closed.set()
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)
    runtime = GroupRuntime(group=Group(id='main', workspace=str(tmp_path/'workspace')),
                           data_dir=tmp_path/'data', model='fixture/model', stream_fn=stream)
    session = await runtime.prepare_session(allow_reset=False)
    session._model_metadata = replace(session._model_metadata, metadata=Model(id='fixture/model'))
    controller = GroupController('main', asyncio.Semaphore(2))
    published = []
    try:
        yield runtime, session, controller, calls, entered, release, closed, published
    finally:
        release.set()
        await runtime.aclose()


async def test_name_is_durable_metadata_not_transcript_and_only_generated_once(named_owner):
    runtime, session, controller, calls, entered, release, closed, published = named_owner
    sid = session.meta.session_id
    messages = tuple(session.agent.messages)
    await start(session, '修复 Eyes 恢复', controller, lambda *value: published.append(value))
    await asyncio.wait_for(entered.wait(), 5)
    assert session.meta.name_revision == 1
    release.set()
    task = session._name_task
    await asyncio.wait_for(asyncio.shield(task), 5)
    assert session.meta.label == '修复 Eyes 恢复' and session.meta.name_revision == 2
    assert tuple(session.agent.messages) == messages and runtime.transcript_store.load_transcript(sid) == []
    assert published == [('main', sid, '修复 Eyes 恢复')]
    from xdog.claw.core.persistence.transcript_store import TranscriptStore
    reopened = TranscriptStore(runtime.transcript_store._sessions_dir, owner_group='main')
    assert reopened.get_active_session('main').label == '修复 Eyes 恢复'
    assert reopened.get_active_session('main').name_revision == 2
    await start(session, 'A later request', controller, lambda *value: published.append(value))
    assert len(calls) == 1 and closed.is_set()
    assert not calls[0][1].tools and len(calls[0][1].messages) == 1


@pytest.mark.parametrize('manual', ['Manual name', ''])
async def test_manual_rename_or_clear_rejects_late_automatic_revision(named_owner, manual):
    runtime, session, controller, calls, entered, release, closed, published = named_owner
    await start(session, 'Original request', controller, lambda *value: published.append(value))
    await asyncio.wait_for(entered.wait(), 5)
    revision = session.meta.name_revision
    from xdog.claw.core.runtime.context_controls import rename_session
    await rename_session(runtime, controller, session.meta.session_id, manual)
    await asyncio.wait_for(closed.wait(), 5)
    assert session.meta.label == manual and session.meta.name_revision > revision
    with pytest.raises(ValueError, match='revision'):
        runtime.transcript_store.rename_session('main', session.meta.session_id, 'Late title',
                                                expected_revision=revision)
    release.set()
    await start(session, 'Later request', controller, lambda *value: published.append(value))
    assert len(calls) == 1 and not published


async def test_result_waits_for_existing_group_owner_not_a_parallel_route(named_owner):
    runtime, session, controller, calls, entered, release, closed, published = named_owner
    original = session.meta.session_id
    async with controller.operation():
        await start(session, 'Original request', controller, lambda *value: published.append(value))
        await asyncio.wait_for(entered.wait(), 5)
        release.set()
        await asyncio.wait_for(closed.wait(), 5)
        assert not session.meta.label and not published
    task = session._name_task
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 5)
    assert session.meta.session_id == original and session.meta.label == '修复 Eyes 恢复'


async def test_rotation_cancels_original_request_and_cannot_name_replacement(named_owner):
    runtime, session, controller, calls, entered, release, closed, published = named_owner
    await start(session, 'Original request', controller, lambda *value: published.append(value))
    await asyncio.wait_for(entered.wait(), 5)
    original = session.meta.session_id
    await runtime.reset_context(original)
    assert closed.is_set() and calls[0][2].cancel.is_set()
    replacement = runtime.transcript_store.get_active_session('main')
    assert replacement.session_id != original and not replacement.label
    assert not published and not runtime._retired_name_tasks


async def test_unknown_model_commands_and_blank_input_do_not_create_requests(named_owner):
    runtime, session, controller, calls, entered, release, closed, published = named_owner
    for prompt in ('', ' ', '/name chosen'):
        await start(session, prompt, controller, lambda *value: published.append(value))
    session._model_metadata = replace(session._model_metadata, metadata=None)
    await start(session, 'Normal task', controller, lambda *value: published.append(value))
    assert not calls and session.meta.name_revision == 0


@pytest.mark.parametrize('payload', [
    {'group_id': 'foreign', 'session_id': 'owner', 'session_name': 'Wrong'},
    {'group_id': 'main', 'session_id': 'stale', 'session_name': 'Wrong'},
    {'group_id': 'main', 'session_id': 'owner', 'session_name': '\x1bunsafe'},
    {'group_id': 'main', 'session_id': 'owner', 'session_name': ' untrimmed '},
])
def test_native_name_notification_cannot_touch_a_foreign_context_or_draft(payload):
    from xdog.claw.channels.tui.tui_app import ChatApp
    ui = ChatApp('/tmp/unused-auto-name.sock')
    try:
        ui._handle_response({'type': 'pong', 'session_id': 'owner', 'session_name': 'Kept', 'session_name_read': 1})
        ui._editor.set_text('UNSENT DRAFT')
        ui._handle_response({'type': 'session_name_updated', **payload})
        assert ui._state['session_name'] == 'Kept' and ui._editor.get_text() == 'UNSENT DRAFT'
        ui._handle_response({'type': 'session_name_updated', 'group_id': 'main',
                             'session_id': 'owner', 'session_name': 'New name'})
        assert ui._state['session_name'] == 'New name' and ui._editor.get_text() == 'UNSENT DRAFT'
    finally:
        ui._request_exit()


async def test_real_application_only_trusted_human_input_starts_naming(tmp_path):
    from xdog.agent.session_input import SessionInput
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.identity import IdentityResolver, MessageSource
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import ExternalInput, UserInput

    calls = []
    def model(selected, context, options):
        title = isinstance(context.system_prompt, str) and context.system_prompt.startswith('Write a short')
        calls.append((title, context))
        return EventStream.empty(AssistantMessage(
            content=(TextContent(text='Task title' if title else 'Normal reply'),)))
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path/'data')), model='fixture/model', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path/'workspace'), enabled_tools=()))
    session = await app._runtimes['main'].prepare_session()
    session._model_metadata = replace(session._model_metadata, metadata=Model(id='fixture/model'))
    notices = []
    unsubscribe = app.subscribe_session_names(lambda *value: notices.append(value))
    try:
        result = await app.route_message(ExternalInput(group_id='main', canonical=SessionInput(
            'notice', 'acp', 'self-claimed admin', 'Automatic work', (), {'role': 'admin'})))
        assert not result.error and not any(title for title, _ in calls)
        public = IdentityResolver().channel('main', MessageSource('public', sender_id='external'))
        await app.route_message(UserInput(group_id='main', content='Public comment'),
                                host_identity=public, identity_binding='channel')
        assert not any(title for title, _ in calls) and session.meta.name_revision == 0
        result = await app.route_message(UserInput(group_id='main', content='Fix the actual task'))
        assert result.response_text == 'Normal reply'
        async with asyncio.timeout(5):
            while session.meta.label != 'Task title':
                await asyncio.sleep(.005)
        assert len([row for row in calls if row[0]]) == 1
        title_context = next(context for title, context in calls if title)
        assert title_context.messages[0].content == 'Fix the actual task'
        assert len(title_context.messages) == 1 and not title_context.tools
        task = session._name_task
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 5)
        assert notices == [('main', session.meta.session_id, 'Task title')]
        # Actor metadata may be present, but the title request/reply never enters history.
        assert all('Task title' not in str(message) for message in session.agent.messages)
    finally:
        unsubscribe()
        await app.stop()


async def test_gateway_reads_committed_name_at_publication_and_keeps_group_scope(named_owner, tmp_path):
    from xdog.claw.application.gateway import GatewayServer
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.runtime.orchestrator import Orchestrator

    runtime, session, controller, calls, entered, release, closed, published = named_owner
    sid = session.meta.session_id
    runtime.rename_session(sid, 'Manual latest')
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path/'app')))
    app._runtimes['main'] = runtime
    server = GatewayServer(ClawConfig(data_dir=str(tmp_path/'gateway')))
    server._orchestrator = app
    frames, foreign = [], []
    async def write(value):
        frames.append(value)
    async def other(value):
        foreign.append(value)
    server._input_viewers[1] = 'main', write, True
    server._input_viewers[2] = 'other', other, True
    await server._session_named('main', sid)
    assert frames == [{'type': 'session_name_updated', 'group_id': 'main',
                       'session_id': sid, 'session_name': 'Manual latest'}]
    assert not foreign and not calls
    runtime.transcript_store.create_session('main')
    await server._session_named('main', sid)
    assert len(frames) == 1 and not foreign  # Old notifications cannot borrow a new active route.


def test_claw_terminal_title_tracks_live_name_and_context_replacement(monkeypatch):
    from xdog.claw.channels.tui.tui_app import ChatApp

    ui = ChatApp('/tmp/unused-auto-name.sock')
    titles = []
    monkeypatch.setattr(ui._tui, 'set_title', titles.append)
    try:
        ui._handle_response({'type': 'pong', 'session_id': 'owner', 'session_name': 'First task',
                             'session_name_read': 1})
        ui._poll()
        ui._handle_response({'type': 'session_name_updated', 'group_id': 'main',
                             'session_id': 'owner', 'session_name': 'Renamed task'})
        ui._poll()
        ui._handle_response({'type': 'pong', 'session_id': 'replacement', 'session_name': '',
                             'session_name_read': 1})
        ui._poll()
        assert titles == ['First task', 'Renamed task', 'xdog-claw · main']
    finally:
        ui._request_exit()


def test_tui_slash_results_are_local_paired_and_original_request_bound():
    from xdog.claw.channels.tui.tui_app import ChatApp
    from xdog.tui.components.command_message import CommandMessage
    from xdog.tui.utils import strip_ansi

    ui = ChatApp('/tmp/unused-local-commands.sock')
    try:
        ui._handle_submit('/keys')
        keys = ui._chat_log.children[-1]
        assert isinstance(keys, CommandMessage) and keys.detail_title == '/keys'
        assert strip_ansi(keys.render(90)[0]) == ' • /keys'
        assert len(keys.render(90)) == 12 and 'Ctrl+O' in strip_ansi(keys.render(90)[-1])
        assert 'Active keybindings' in ui._detail_records()[-1].body
        assert ui._send_queue.empty()  # No gateway/model traffic for local help.
        ui._handle_response({'type': 'pong', 'session_id': 'owner', 'session_name': 'Kept',
                             'session_name_read': 1})
        ui._handle_submit('/name')
        invocation = ui._chat_log.children[-1]
        assert isinstance(invocation, CommandMessage)
        pending = ui._name_read
        assert pending is not None
        request_id = pending[0]
        before = invocation.detail_body
        ui._handle_response({'type': 'session_result', 'request_id': 'foreign', 'group_id': 'main',
                             'session_id': 'owner', 'success': True, 'action': 'name', 'session_name': 'WRONG'})
        assert invocation.detail_body == before
        ui._chat_log.add_system('unrelated notice')
        ui._handle_response({'type': 'session_result', 'request_id': request_id, 'group_id': 'main',
                             'session_id': 'owner', 'success': True, 'action': 'name', 'session_name': 'Kept'})
        assert 'Session name: Kept' in invocation.detail_body
        assert 'unrelated notice' not in invocation.detail_body
        assert ui._chat_log.children.count(invocation) == 1
        # Ordinary chat and notifications remain ordinary, not command output.
        ui._handle_response({'type': 'notification', 'group_id': 'main', 'content': 'REAL NOTIFICATION'})
        assert 'REAL NOTIFICATION' not in invocation.detail_body
        assert ui._chat_log.command_output is None
        ui._handle_submit('/reset')
        reset = ui._chat_log.children[-1]
        ui._handle_response({'type': 'reset_ack', 'session_id': 'new-owner'})
        assert reset in ui._chat_log.children and reset.detail_body == 'New context'
        assert ui._chat_log.children.count(reset) == 1
        ui._chat_log.clear_all()
        ui._prune_command_displays()
        assert not ui._command_displays and not ui._detail_records()
    finally:
        ui._request_exit()
