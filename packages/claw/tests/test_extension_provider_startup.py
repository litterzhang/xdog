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

ALIAS = 'ext-providermock-local/echo'


@pytest.mark.parametrize('saved', [False, True])
async def test_claw_configured_or_saved_alias_activates_before_ready_without_base_requests(tmp_path, saved):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []
    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='base/model' if saved else ALIAS, data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'startup.sock'), pid_file=str(tmp_path / 'startup.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('providermock',), extension_dir=str(root))
    server = GatewayServer(config)
    async def start():
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                   return_value=('base/model' if saved else ALIAS, base)):
            await server.start()
    await start()
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
        if saved:
            ui._handle_submit('/model ' + ALIAS)
            await wait(lambda: ui._extension_model_pending is None)
            initial = server._orchestrator._runtimes['main']
            assert initial._active_session.agent.state.model == ALIAS, '\n'.join(ui._chat_log.render(160))
            assert initial.transcript_store.get_model(initial._active_session.meta.session_id) == ALIAS
            ui._request_exit()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()
            server = GatewayServer(config)
            await start()
            ui = ChatApp(config.socket_path)
            reader, writer = await asyncio.open_unix_connection(config.socket_path)
            task = asyncio.create_task(ui._run_connection(reader, writer))
            await wait(lambda: ui._connection_ready)
        runtime = server._orchestrator._runtimes['main']
        assert runtime.model == runtime._active_session.agent.state.model == ALIAS
        assert runtime._active_session.agent.context_window == 32000
        assert ui._state['model'] == ALIAS
        assert await runtime._active_session.extensions.command('ext-providermock-count', '') == '0'
        ui._handle_submit('startup provider')
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        assert 'EXT-PROVIDER-ANSWER' in '\n'.join(ui._chat_log.render(120)) and not calls
        assert await runtime._active_session.extensions.command('ext-providermock-count', '') == '1'
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_bad_configured_alias_does_not_start_gateway_or_leave_activated_workers(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    config = ClawConfig(model='ext-providermock-local/missing', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'bad.sock'), pid_file=str(tmp_path / 'bad.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('providermock',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model,
               lambda *args: EventStream.empty(AssistantMessage()))):
        with pytest.raises(ValueError, match='not active'):
            await server.start()
    assert not Path(config.socket_path).exists()
    owner = server._orchestrator._runtimes['main']._active_session.extensions
    assert owner._closed and not owner._manager._clients
