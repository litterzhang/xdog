from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.supervision_config import ChildConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

ALIAS = 'ext-providerauth-local/echo'
SECRET = 'offline-claw-ui044-private-token'


@pytest.mark.parametrize('failure', [False, True])
async def test_claw_real_socket_ui_private_auth_saved_alias_and_transcript_separation(tmp_path, monkeypatch, failure):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    monkeypatch.setenv('XDOG_UI044_TEST_KEY', SECRET)

    def base(*args):
        pytest.fail('Auth-bound extension alias must never invoke native fallback')
        return EventStream.empty(AssistantMessage())

    config = ClawConfig(model='base/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'auth.sock'), pid_file=str(tmp_path / 'auth.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('providerauth',), extension_dir=str(root),
        extension_settings={'providerauth': {'mode': 'fail' if failure else ''}})
    server = GatewayServer(config)

    async def start():
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('base/model', base)):
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

    async def disconnect():
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()

    try:
        await wait(lambda: ui._connection_ready)
        ui._handle_submit('/model ' + ALIAS)
        await wait(lambda: ui._extension_model_pending is None)
        original = server._orchestrator._runtimes['main']
        assert original._active_session.agent.state.model == ALIAS
        assert original.transcript_store.get_model(original._active_session.meta.session_id) == ALIAS
        assert await original._active_session.extensions.command('ext-providerauth-count', '') == (
            'CALLS=0;RESOLUTIONS=0')
        # This stops/restarts only the test-owned temporary gateway, not a user service.
        await disconnect()
        server = GatewayServer(config)
        await start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
        await wait(lambda: ui._connection_ready)
        runtime = server._orchestrator._runtimes['main']
        session = runtime._active_session
        assert runtime.model == session.agent.state.model == ALIAS and ui._state['model'] == ALIAS
        assert await session.extensions.command('ext-providerauth-count', '') == 'CALLS=0;RESOLUTIONS=0'
        ui._handle_submit('explicit offline authenticated provider turn')
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        expected = 'Extension provider credentials unavailable' if failure else 'AUTHENTICATED-OFFLINE-ANSWER'
        visible = '\n'.join(ui._chat_log.render(140))
        assert expected in visible and SECRET not in visible
        entries = runtime.transcript_store.load_transcript(session.meta.session_id)
        assert entries[-1].get('stop_reason', 'stop') == ('error' if failure else 'stop')
        assert SECRET not in repr(entries) and SECRET not in repr(session.extensions._infos)
        assert await session.extensions.command('ext-providerauth-count', '') == (
            f'CALLS={0 if failure else 1};RESOLUTIONS=1')
        child = await session.children.supervisor.start('independent authenticated child',
            config=ChildConfig(model=ALIAS, max_model_calls=1).to_dict())
        record = await session.children.supervisor.control('wait', child)
        assert record['status'] == ('failed' if failure else 'completed') and SECRET not in repr(record)
        assert record['effective']['model'] == ALIAS and session.agent.state.model == ALIAS
        assert await session.extensions.command('ext-providerauth-count', '') == (
            f'CALLS={0 if failure else 1};RESOLUTIONS=1')
        for path in Path(config.data_dir).rglob('*'):
            if path.suffix in ('.json', '.jsonl'):
                assert SECRET not in path.read_text()
        ui._extension_command('extensions', 'unload providerauth')
        await wait(lambda: ui._extension_pending is None)
        ui._handle_submit('withdrawn alias cannot trigger auth or native fallback')
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        assert session.agent.state.model == ALIAS and not session.extensions.provider_models()
    finally:
        await disconnect()
