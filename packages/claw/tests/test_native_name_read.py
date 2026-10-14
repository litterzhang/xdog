"""Native name reads use the gateway's committed owner, never the last client footer."""
from __future__ import annotations

import asyncio
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
