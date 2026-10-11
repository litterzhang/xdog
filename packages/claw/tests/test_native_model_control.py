from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.helpers import stream_fn_from_provider
from xdog.ai.types import AssistantMessage, Model, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


class Provider:
    def __init__(self):
        self.calls, self.lookups = [], []
        self.rows = (Model(id='offline/first', context_window=48000, max_prompt_tokens=30000, max_tokens=2048),
                     Model(id='offline/second', context_window=64000, max_prompt_tokens=40000, max_tokens=1024,
                           reasoning=True, supported_efforts=('low', 'high'), supports_tool_calls=False,
                           headers={'Authorization': 'MUST-NOT-LEAK'}, base_url='https://private.invalid/token=SECRET'))
    def models(self):
        return self.rows
    def model(self, name):
        self.lookups.append(name)
        return next((row for row in self.rows if row.id == name or row.id.split('/')[1] == name), None)
    def stream(self, model, context, options):
        self.calls.append((model, context, options))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='NATIVE-MODEL-ANSWER'),)))


async def start(config, provider):
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('offline/first', stream_fn_from_provider(provider))):
        await server.start()
    return server


async def test_actual_native_catalog_selection_uses_only_configured_adapter_persists_and_resumes(tmp_path):
    provider = Provider()
    config = ClawConfig(model='offline/first', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'native.sock'), pid_file=str(tmp_path / 'native.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    server = await start(config, provider)
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
        ui._editor.set_text('native model draft')
        ui._handle_submit('/model')
        await wait(lambda: ui._extension_model_pending is None)
        rendered = '\n'.join(ui._chat_log.render(160))
        assert 'offline/first' in rendered and 'offline/second' in rendered
        assert 'MUST-NOT-LEAK' not in rendered and 'private.invalid' not in rendered and not provider.calls
        ui._handle_submit('/model second')  # Exact canonical resolution on the same configured adapter.
        await wait(lambda: ui._extension_model_pending is None)
        runtime = server._orchestrator._runtimes['main']
        session = runtime._active_session
        key = session.meta.session_id
        assert session.agent.state.model == runtime.model == 'offline/second'
        assert session.agent.context_window == 64000 and session.agent.max_prompt_tokens == 40000
        assert session.agent.supports_tool_calls is False
        assert runtime.transcript_store.get_model(key) == 'offline/second'
        assert ui._editor.get_text() == 'native model draft'
        ui._handle_submit('/model inactive/model')
        await wait(lambda: ui._extension_model_pending is None)
        assert session.agent.state.model == 'offline/second' and not provider.calls
        assert runtime.transcript_store.get_model(key) == 'offline/second'
        ui._handle_submit('/thinking high')
        await wait(lambda: ui._reasoning_pending is None)
        assert session.agent.options.thinking == 'high' and not provider.calls
        payload = await server._orchestrator.extension_models('main', key)
        assert 'MUST-NOT-LEAK' not in json.dumps(payload) and 'SECRET' not in json.dumps(payload)
        ui._handle_submit('use selected native model')
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        assert len(provider.calls) == 1 and provider.calls[0][0] == 'offline/second'
        assert provider.calls[0][2].thinking == 'high' and not provider.calls[0][1].tools
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
    server = await start(config, provider)
    try:
        runtime = server._orchestrator._runtimes['main']
        assert runtime._active_session.meta.session_id == key
        assert runtime._active_session.agent.state.model == 'offline/second'
        assert runtime._active_session.agent.options.thinking == 'high' and len(provider.calls) == 1
        assert runtime._active_session.agent.supports_tool_calls is False
        # New sessions start from their own configured choice, not another conversation's model setting.
        await server._orchestrator.navigate_session('main', 'new', '', '', -1, key)
        fresh = runtime.get_or_create_session(allow_reset=False)
        assert fresh.agent.state.model == 'offline/first' and fresh.agent.options.thinking is None
        assert len(provider.calls) == 1
    finally:
        await server.stop()


async def test_saved_native_model_unavailable_on_actual_adapter_fails_before_gateway_ready_without_fallback(tmp_path):
    provider = Provider()
    config = ClawConfig(model='offline/first', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gone.sock'), pid_file=str(tmp_path / 'gone.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    server = await start(config, provider)
    runtime = server._orchestrator._runtimes['main']
    key = runtime.get_or_create_session(allow_reset=False).meta.session_id
    await server._orchestrator.extension_models('main', key, 'offline/second')
    await server.stop()
    provider.rows = (replace(provider.rows[0], name='first'),)
    with pytest.raises(ValueError, match='Saved native model unavailable'):
        await start(config, provider)
    assert not provider.calls and not Path(config.socket_path).exists()


@pytest.mark.parametrize('after_commit', [False, True])
async def test_canceled_native_model_setting_settles_disk_without_replay_and_preserves_commit_consistency(
        tmp_path, monkeypatch, after_commit):
    import threading
    provider = Provider()
    config = ClawConfig(model='offline/first', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'cancel.sock'), pid_file=str(tmp_path / 'cancel.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    server = await start(config, provider)
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    key = session.meta.session_id
    entered, release, calls = threading.Event(), threading.Event(), []
    original = runtime.transcript_store.save_model
    def delayed(*args):
        calls.append(args)
        if after_commit:
            original(*args)
        entered.set()
        release.wait(3)
        if not after_commit:
            original(*args)
    monkeypatch.setattr(runtime.transcript_store, 'save_model', delayed)
    task = asyncio.create_task(server._orchestrator.extension_models('main', key, 'offline/second'))
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
        assert len(calls) == 1 and not provider.calls
        assert session.agent.state.model == ('offline/second' if after_commit else 'offline/first')
        assert runtime.transcript_store.get_model(key) == ('offline/second' if after_commit else '')
    finally:
        release.set()
        await server.stop()


def test_legacy_ui_native_model_command_is_not_an_agent_turn_or_an_implicit_model_fallback():
    ui = ChatApp('/tmp/unused-native-model.sock')
    try:
        ui._editor.set_text('preserved draft')
        ui._handle_submit('/model offline/second')
        assert not ui._active_run_id and ui._extension_model_pending is None
        assert ui._editor.get_text() == 'preserved draft'
        assert 'Updated native-model gateway required' in '\n'.join(ui._chat_log.render(120))
    finally:
        ui._request_exit()
