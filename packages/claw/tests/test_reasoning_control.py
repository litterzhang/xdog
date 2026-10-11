from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.runtime.gateway import GatewayServer

ALIAS = 'ext-providercaps-local/gpt-5.1/code:preview'


async def make_server(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    config = ClawConfig(model=ALIAS, data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'reasoning.sock'), pid_file=str(tmp_path / 'reasoning.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('providercaps',), extension_dir=str(root))
    calls = []
    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(ALIAS, base)):
        await server.start()
    return server, config, calls


async def test_actual_claw_thinking_inspection_supported_mutation_rejection_and_saved_restart_without_turn(tmp_path):
    server, config, calls = await make_server(tmp_path)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._reasoning_supported
        session = server._orchestrator._runtimes['main']._active_session
        key = session.meta.session_id
        ui._editor.set_text('preserved reasoning draft')
        ui._handle_submit('/thinking')
        await wait(lambda: ui._reasoning_pending is None)
        assert 'available: off, low, high' in '\n'.join(ui._chat_log.render(120))
        assert session._store.get_reasoning(key, ALIAS) is None
        ui._handle_submit('/thinking high')
        await wait(lambda: ui._reasoning_pending is None)
        assert session.agent.options.thinking == 'high' and ui._state['thinking'] == 'high'
        assert session._store.get_reasoning(key, ALIAS) == 'high'
        ui._handle_submit('/thinking xhigh')
        await wait(lambda: ui._reasoning_pending is None)
        assert session.agent.options.thinking == 'high' and session._store.get_reasoning(key, ALIAS) == 'high'
        assert 'not advertised' in '\n'.join(ui._chat_log.render(120))
        assert not calls and not session.agent.messages
        assert ui._editor.get_text() == 'preserved reasoning draft'
        assert await session.extensions.command('ext-providercaps-count', '') == 'CALLS=0'
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
    # Owned offline restart restores only the matching session/model preference, never a provider turn.
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=(ALIAS, lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    try:
        restored = server._orchestrator._runtimes['main']._active_session
        assert restored.meta.session_id == key and restored.agent.options.thinking == 'high'
        assert await restored.extensions.command('ext-providercaps-count', '') == 'CALLS=0'
    finally:
        await server.stop()


async def test_unnegotiated_foreign_and_busy_reasoning_control_cannot_change_owned_session(tmp_path):
    server, config, _ = await make_server(tmp_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    session = server._orchestrator._runtimes['main']._active_session
    key = session.meta.session_id
    async def request(message):
        writer.write((json.dumps(message) + '\n').encode())
        await writer.drain()
        async with asyncio.timeout(5):
            while True:
                reply = json.loads(await reader.readline())
                if reply.get('request_id') == message.get('request_id'):
                    return reply
    try:
        frame = {'type': 'reasoning_control', 'group_id': 'main', 'session_id': key, 'thinking': 'high'}
        assert not (await request({**frame, 'request_id': 'unnegotiated'}))['success']
        writer.write(b'{"type":"ping","group_id":"main","reasoning_controls":1}\n')
        await writer.drain()
        assert not (await request({**frame, 'request_id': 'foreign', 'session_id': 'foreign'}))['success']
        assert server._orchestrator.try_reserve_group('main')
        try:
            assert not (await request({**frame, 'request_id': 'busy'}))['success']
        finally:
            server._orchestrator.release_group_reservation('main')
        assert session.agent.options.thinking is None and session._store.get_reasoning(key, ALIAS) is None
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_canceled_precommit_wait_settles_owned_disk_without_late_setting_or_replay(tmp_path, monkeypatch):
    server, _, _ = await make_server(tmp_path)
    session = server._orchestrator._runtimes['main']._active_session
    entered, release = threading.Event(), threading.Event()
    original = session._store.save_reasoning
    calls = []
    def delayed(*args):
        calls.append(args)
        entered.set()
        release.wait(3)
        return original(*args)
    monkeypatch.setattr(session._store, 'save_reasoning', delayed)
    task = asyncio.create_task(server._orchestrator.reasoning('main', session.meta.session_id, 'high'))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.003)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1 and session.agent.options.thinking is None
        assert session._store.get_reasoning(session.meta.session_id, ALIAS) is None
    finally:
        release.set()
        await server.stop()


def test_persisted_preferences_are_atomic_bounded_model_bound_and_not_symlink_targets(tmp_path):
    store = TranscriptStore(tmp_path)
    store.save_reasoning('session', ALIAS, 'high')
    assert store.get_reasoning('session', ALIAS) == 'high'
    assert store.get_reasoning('session', 'another/model') is None
    with pytest.raises(ValueError):
        store.save_reasoning('../foreign', ALIAS, 'high')
    with pytest.raises(ValueError):
        store.save_reasoning('session', ALIAS, 'high', lambda: False)
    assert store.get_reasoning('session', ALIAS) == 'high'
    link = tmp_path / 'linked.thinking.json'
    link.symlink_to(tmp_path / 'session.thinking.json')
    with pytest.raises(ValueError):
        store.save_reasoning('linked', ALIAS, 'low')
    with pytest.raises(ValueError):
        store.get_reasoning('linked', ALIAS)
    assert not list(tmp_path.glob('.thinking-*'))


async def test_lost_postcommit_ack_preserves_live_persisted_consistency_without_replaying_write(tmp_path, monkeypatch):
    server, _, _ = await make_server(tmp_path)
    session = server._orchestrator._runtimes['main']._active_session
    entered, release = threading.Event(), threading.Event()
    original = session._store.save_reasoning
    calls = []
    def delayed_ack(*args):
        calls.append(args)
        original(*args)
        entered.set()  # Atomic persistence was admitted; acknowledgement has not returned.
        release.wait(3)
    monkeypatch.setattr(session._store, 'save_reasoning', delayed_ack)
    task = asyncio.create_task(server._orchestrator.reasoning('main', session.meta.session_id, 'high'))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.003)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1 and session.agent.options.thinking == 'high'
        assert session._store.get_reasoning(session.meta.session_id, ALIAS) == 'high'
        assert not session.agent.messages
        assert await session.extensions.command('ext-providercaps-count', '') == 'CALLS=0'
    finally:
        release.set()
        await server.stop()


def test_ui_stale_legacy_invalid_metadata_does_not_route_settings_as_agent_input():
    ui = ChatApp('/tmp/unused-thinking.sock')
    try:
        ui._editor.set_text('draft')
        ui._handle_submit('/thinking high')  # Capability is absent: do not send it as a conversation.
        assert not ui._reasoning_pending and not ui._active_run_id and ui._editor.get_text() == 'draft'
        ui._state['model'] = ALIAS
        ui._gateway_session_id = 'current'
        ui._reasoning_pending = 'new'
        ui._handle_response({'type': 'reasoning_result', 'request_id': 'old', 'group_id': 'main', 'success': True,
            'session_id': 'current', 'model': ALIAS, 'thinking': 'high', 'levels': ['off', 'high']})
        assert ui._reasoning_pending == 'new' and ui._state.get('thinking') is None
        ui._handle_response({'type': 'reasoning_result', 'request_id': 'new', 'group_id': 'main', 'success': True,
            'session_id': 'current', 'model': ALIAS, 'thinking': [], 'levels': [{}]})
        assert ui._reasoning_pending is None and ui._state.get('thinking') is None
    finally:
        ui._request_exit()
