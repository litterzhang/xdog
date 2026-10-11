"""Native name reads use the gateway's committed owner, never the last client footer."""
from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.persistence.transcript_store import SessionNameOwnerChanged, TranscriptStore
from xdog.claw.core.runtime.gateway import GatewayServer


@pytest.fixture
async def peers(tmp_path):
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'names.sock'), pid_file=str(tmp_path / 'names.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    seeded = TranscriptStore(tmp_path / 'data/groups/main/sessions', owner_group='main')
    meta = seeded.create_session('main')
    seeded.rename_session('main', meta.session_id, 'Original')
    server = GatewayServer(config)
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
        await server.start()
    clients = []
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                for ui, _, _ in clients:
                    ui._poll()
                await asyncio.sleep(0.003)
    for _ in range(2):
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
        task = asyncio.create_task(ui._run_connection(reader, writer))
        clients.append((ui, writer, task))
        await wait(lambda: ui._connection_ready)
    try:
        yield server, [ui for ui, _, _ in clients], calls, wait
    finally:
        for ui, writer, task in clients:
            ui._request_exit()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize("cached", [False, True])
async def test_native_read_sees_other_peers_rename_without_history_or_agent_mutation(peers, cached):
    server, (first, second), calls, wait = peers
    runtime = server._orchestrator._runtimes['main']
    original = runtime.get_or_create_session(allow_reset=False) if cached else runtime._active_session
    state = original.agent.state if original is not None else None
    second._editor.set_text('NAME-READ-DRAFT')
    epoch = second._output_epoch
    first._name_session('Other peer name')
    await wait(lambda: first._session_mutation_id is None)
    assert second._state['session_name'] == 'Original'  # Actual prior stale display, not a forged mock.
    second._handle_submit('/name')
    assert second._name_read is not None
    assert second._session_mutation_id is None and second._pending_command is None
    await wait(lambda: second._name_read is None)
    assert 'Session name: Other peer name' in str(second._chat_log.render(140))
    assert second._state['session_name'] == 'Other peer name'
    assert second._editor.get_text() == 'NAME-READ-DRAFT' and second._output_epoch == epoch
    assert runtime._active_session is original and not calls
    if original is not None:
        assert original.agent.state is state
    assert not second._history_loading


@pytest.mark.parametrize('settlement', ['commit', 'rollback', 'escape', 'withdraw', 'disconnect'])
async def test_read_waits_for_name_journal_owner_without_blocking_reader_or_borrowing_transient_metadata(
    peers, monkeypatch, settlement,
):
    server, (writer_ui, reader_ui), calls, wait = peers
    runtime = server._orchestrator._runtimes['main']
    store = runtime.transcript_store
    entered, release = threading.Event(), threading.Event()
    original = store._atomic
    def held_index(path, value, **kwargs):
        original(path, value, **kwargs)
        if path == store._index_file:
            entered.set()
            assert release.wait(8)
            if settlement == 'rollback':
                raise OSError('name commit failed after metadata publication')
    monkeypatch.setattr(store, '_atomic', held_index)
    writer_ui._name_session('Unsettled')
    try:
        await wait(entered.is_set)
        reader_ui._name_session('')
        await wait(lambda: reader_ui._name_read is not None)
        rid = reader_ui._name_read[0]
        await asyncio.sleep(0.03)
        reader_ui._poll()
        assert reader_ui._name_read is not None
        assert 'Session name: Unsettled' not in str(reader_ui._chat_log.render(140))
        # A separate metadata read job does not hold up this connection's ping reader.
        reader_ui._queue_request({'type': 'ping', 'group_id': 'main',
                                 'session_name_read': 0 if settlement == 'withdraw' else 1})
        if settlement == 'escape':
            reader_ui._handle_escape()
            assert reader_ui._name_read is None
        elif settlement == 'disconnect':
            reader_ui._handle_response({'type': 'internal_error', 'message': 'read connection lost'})
            assert reader_ui._name_read is None
        elif settlement == 'withdraw':
            await wait(lambda: not reader_ui._name_read_supported)
            assert reader_ui._name_read is None
        else:
            await wait(lambda: reader_ui._state['session_name'] == 'Unsettled')
            assert reader_ui._name_read is not None  # A pong's transient cache is NOT a name read receipt.
        release.set()
        await wait(lambda: writer_ui._session_mutation_id is None)
        if settlement in {'commit', 'rollback'}:
            await wait(lambda: reader_ui._name_read is None)
            expected = 'Original' if settlement == 'rollback' else 'Unsettled'
            assert f'Session name: {expected}' in str(reader_ui._chat_log.render(140))
            assert reader_ui._state['session_name'] == expected
        else:
            assert 'Session name: Unsettled' not in str(reader_ui._chat_log.render(140))
            reader_ui._handle_response({'type': 'session_result', 'action': 'name', 'request_id': rid,
                'group_id': 'main', 'session_id': reader_ui._gateway_session_id,
                'success': True, 'session_name': 'IGNORED-LATE'})
            assert 'IGNORED-LATE' not in str(reader_ui._chat_log.render(140))
        assert not calls and not reader_ui._history_loading
    finally:
        release.set()


async def test_name_read_stale_owner_marks_context_stale_without_activation_or_fallback(peers):
    server, (_, ui), calls, wait = peers
    runtime = server._orchestrator._runtimes['main']
    before = ui._gateway_session_id
    other = runtime.transcript_store.create_session('main')
    ui._name_session('')
    await wait(lambda: ui._name_read is None)
    assert ui._session_context_stale and ui._gateway_session_id == before
    assert runtime.transcript_store.get_active_session('main').session_id == other.session_id
    assert 'refresh /sessions' in str(ui._chat_log.render(140)) and not calls
    ui._handle_submit('Do not send against a mismatched context')
    assert 'Do not send against a mismatched context' in ui._editor.get_text() and not calls


@pytest.mark.parametrize('capability', [None, 0, True])
def test_old_or_boolean_capability_cannot_reuse_rename_support_or_cached_name(capability):
    ui = ChatApp('/tmp/unused-name-read.sock')
    try:
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_rename': 1,
            'session_id': 'owner', 'session_name': 'CACHED-ONLY',
            **({'session_name_read': capability} if capability is not None else {})})
        ui._name_session('')
        assert ui._name_read is None and ui._send_queue.empty()
        assert 'Session name: CACHED-ONLY' not in str(ui._chat_log.render(140))
    finally:
        ui._request_exit()


def test_foreign_noncanonical_and_superseded_name_read_receipts_preserve_active_turn_and_draft():
    ui = ChatApp('/tmp/unused-name-read-owner.sock')
    try:
        ui._handle_response({'type': 'pong', 'session_name_read': 1, 'session_controls': 1,
            'session_rename': 1, 'session_navigation_cancel': 1, 'session_id': 'owner', 'session_name': 'Known'})
        ui._active_run_id = 'model-turn'
        ui._set_waiting(True)
        started = ui._status_started
        ui._editor.set_text('KEPT-DRAFT')
        for extra in ({'group_id': 'foreign'}, {'session_id': 'foreign'}, {'session_name': '\x1bunsafe'},
                      {'session_name': ' untrimmed '}):
            ui._name_session('')
            request = ui._send_queue.get_nowait()
            ui._handle_response({'type': 'session_result', 'action': 'name', 'request_id': request['request_id'],
                'group_id': 'main', 'session_id': 'owner', 'success': True, 'session_name': 'Forged', **extra})
            assert ui._name_read is None and ui._state['session_name'] == 'Known'
            assert ui._active_run_id == 'model-turn' and ui._waiting and ui._status_started == started
        ui._name_session('')
        request = ui._send_queue.get_nowait()
        ui._name_session('')
        assert ui._send_queue.empty()  # Duplicate reads are not an unbounded job queue.
        ui._handle_escape()
        assert ui._send_queue.get_nowait()['type'] == 'session_control_cancel'
        assert ui._active_run_id == 'model-turn' and ui._name_read is None
        ui._handle_response({'type': 'session_result', 'action': 'name', 'request_id': request['request_id'],
            'group_id': 'main', 'session_id': 'owner', 'success': True, 'session_name': 'Late'})
        assert ui._state['session_name'] == 'Known' and ui._editor.get_text() == 'KEPT-DRAFT'
    finally:
        ui._request_exit()


def test_storage_name_read_refuses_foreign_and_invalid_persisted_metadata(tmp_path):
    store = TranscriptStore(tmp_path)
    owner = store.create_session('main')
    foreign = store.create_session('other')
    assert store.read_session_name('main', owner.session_id)['session_name'] == ''
    with pytest.raises(SessionNameOwnerChanged):
        store.read_session_name('main', foreign.session_id)
    store._index['main']['label'] = '\x1bmalformed'
    with pytest.raises(ValueError):
        store.read_session_name('main', owner.session_id)


async def test_real_active_turn_survives_metadata_read_cancellation_and_keeps_group_reservation(peers, monkeypatch):
    from xdog.ai.types import DoneEvent, StartEvent, TextContent

    server, (_, ui), calls, wait = peers
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    started, release = asyncio.Event(), asyncio.Event()
    turns = []
    def held_stream(*args):
        turns.append(args)
        async def generate():
            started.set()
            yield StartEvent(partial=AssistantMessage())
            await release.wait()
            message = AssistantMessage(content=(TextContent(text='Offline turn done'),))
            source.set_result(message)
            yield DoneEvent(message=message)
        source = EventStream(generate())
        return source
    session.agent.set_stream_fn(held_stream)
    try:
        ui._handle_submit('Hold offline model turn')
        await wait(lambda: started.is_set() and ui._active_run_id is not None)
        run, timer = ui._active_run_id, ui._status_started
        monkeypatch.setattr(runtime.transcript_store, 'read_session_name', lambda *args: None)
        ui._name_session('')
        await asyncio.sleep(0.03)
        assert ui._name_read is not None and session.agent.is_streaming
        ui._handle_escape()
        await asyncio.sleep(0.03)
        ui._poll()
        assert ui._name_read is None and ui._active_run_id == run and ui._status_started == timer
        assert server._orchestrator.is_group_running('main') and session.agent.is_streaming
        assert ui._aborting_run_id is None and len(turns) == 1 and not calls
        release.set()
        await wait(lambda: ui._active_run_id is None)
        assert len(turns) == 1 and not calls
    finally:
        release.set()


async def test_wire_name_read_capacity_cancellation_and_capability_do_not_steal_group_ownership(peers, monkeypatch):
    server, (_, ui), calls, _ = peers
    runtime = server._orchestrator._runtimes['main']
    monkeypatch.setattr(runtime.transcript_store, 'read_session_name', lambda *args: None)
    reader, writer = await asyncio.open_unix_connection(str(server._socket_path))
    async def send(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
    async def read(kind):
        async with asyncio.timeout(5):
            while True:
                value = json.loads(await reader.readline())
                if value['type'] == kind:
                    return value
    try:
        await send({'type': 'ping', 'group_id': 'main', 'session_name_read': True})
        assert (await read('pong'))['session_name_read'] == 0
        await send({'type': 'session_control', 'action': 'name', 'group_id': 'main',
                    'request_id': 'unnegotiated', 'expected_session': ui._gateway_session_id})
        assert not (await read('session_result'))['success']
        await send({'type': 'ping', 'group_id': 'main', 'session_name_read': 1})
        assert (await read('pong'))['session_name_read'] == 1
        for index in range(9):
            await send({'type': 'session_control', 'action': 'name', 'group_id': 'main',
                        'request_id': f'read-{index}', 'expected_session': ui._gateway_session_id})
        overflow = await read('session_result')
        assert overflow['request_id'] == 'read-8' and not overflow['success']
        assert not server._orchestrator.is_group_running('main')  # Reads never reserve execution.
        await send({'type': 'session_control_cancel', 'group_id': 'main', 'request_id': 'read-0',
                    'expected_session': ui._gateway_session_id})
        canceled = await read('session_result')
        assert canceled['request_id'] == 'read-0' and canceled['canceled']
        await send({'type': 'ping', 'group_id': 'main', 'session_name_read': 0})
        remaining = set()
        async with asyncio.timeout(5):
            while True:
                value = json.loads(await reader.readline())
                if value['type'] == 'session_result':
                    assert value['canceled'] and not value['success']
                    remaining.add(value['request_id'])
                elif value['type'] == 'pong':
                    assert value['session_name_read'] == 0
                    if len(remaining) == 7:
                        break
                if len(remaining) == 7:
                    break
        assert remaining == {f'read-{index}' for index in range(1, 8)} and not calls
    finally:
        writer.close()
        await writer.wait_closed()
