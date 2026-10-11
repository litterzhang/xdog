from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


@pytest.mark.parametrize('model_id', ['echo', 'failure', 'aborted'])
async def test_claw_real_provider_catalog_selection_stream_and_owned_no_fallback_on_unload(tmp_path, model_id):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []
    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='base/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'provider.sock'), pid_file=str(tmp_path / 'provider.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('providermock',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('base/model', base)):
        await server.start()
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
        assert ui._extension_models_supported
        ui._handle_submit('/model')
        await wait(lambda: ui._extension_model_pending is None)
        assert 'ext-providermock-local/echo' in '\n'.join(ui._chat_log.render(120))
        alias = 'ext-providermock-local/' + model_id
        ui._handle_submit('/model ' + alias)
        await wait(lambda: ui._extension_model_pending is None)
        runtime = server._orchestrator._runtimes['main']
        assert runtime._active_session.agent.state.model == alias
        assert runtime._active_session.agent.context_window == 32000
        ui._handle_submit('use selected provider')
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        text = '\n'.join(ui._chat_log.render(120))
        failure = model_id in {'failure', 'aborted'}
        assert ('EXT-PROVIDER-PARTIAL' if failure else 'EXT-PROVIDER-ANSWER') in text and not calls
        if failure:
            diagnostic = 'Provider canceled' if model_id == 'aborted' else 'Provider unavailable'
            assert ('Canceled: ' if model_id == 'aborted' else 'Error: ') + diagnostic in text
            assert text.count(diagnostic) == 1 and not ui._chat_log._streaming_runs
            session = runtime._active_session
            entries = runtime.transcript_store.load_transcript(session.meta.session_id)
            assert entries[-1]['error_message'] == diagnostic and entries[-1]['response_id'] == 'owned-failure'
            assert entries[-1]['stop_reason'] == ('aborted' if model_id == 'aborted' else 'error')
            assert entries[-1]['usage']['input'] == 7 and entries[-1]['usage']['output'] == 3
            assert await session.extensions.command('ext-providermock-closed', '') == '1'
        assert await runtime._active_session.extensions.command('ext-providermock-count', '') == '1'
        ui._extension_command('extensions', 'unload providermock')
        await wait(lambda: ui._extension_pending is None)
        ui._handle_submit('do not use base after unload')
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        assert not calls
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('model_id', ['echo', 'failure', 'aborted'])
async def test_claw_child_provider_override_keeps_parent_model_and_uses_an_independent_worker(tmp_path, model_id):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []
    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='base/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'child-provider.sock'), pid_file=str(tmp_path / 'child-provider.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('providermock',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('base/model', base)):
        await server.start()
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
        ui._handle_submit('/model')
        await wait(lambda: ui._extension_model_pending is None)
        ui._agents_command('start --model ext-providermock-local/' + model_id + ' independent provider child')
        await wait(lambda: not ui._children_pending)
        session = server._orchestrator._runtimes['main']._active_session
        child = session.children.supervisor.snapshot()['children'][0]['id']
        record = await session.children.supervisor.control('wait', child)
        expected = 'canceled' if model_id == 'aborted' else 'failed' if model_id == 'failure' else 'completed'
        assert record['status'] == expected
        assert ('EXT-PROVIDER-ANSWER' if model_id == 'echo' else 'EXT-PROVIDER-PARTIAL') in record['result']
        assert record['effective']['model'] == 'ext-providermock-local/' + model_id and record['calls'] == 1
        assert not calls and session.agent.state.model == 'base/model'
        assert await session.extensions.command('ext-providermock-count', '') == '0'
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
