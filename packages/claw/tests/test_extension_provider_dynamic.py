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


@pytest.mark.parametrize('lifecycle', [False, True])
async def test_claw_live_provider_add_replace_remove_catalog_caps_and_explicit_no_fallback(tmp_path, lifecycle):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []

    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    config = ClawConfig(model='base/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'dynamic.sock'), pid_file=str(tmp_path / 'dynamic.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('providerdynamic',), extension_dir=str(root),
        extension_settings={'providerdynamic': {'turn_replace': lifecycle}})
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('base/model', base)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    alias = 'ext-providerdynamic-live/echo'

    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)

    async def command(name):
        ui._extension_command(name, '')
        await wait(lambda: ui._extension_pending is None)

    try:
        await wait(lambda: ui._connection_ready)
        await command('ext-providerdynamic-enable')
        ui._handle_submit('/model')
        await wait(lambda: ui._extension_model_pending is None)
        assert alias in '\n'.join(ui._chat_log.render(120))
        ui._handle_submit('/model ' + alias)
        await wait(lambda: ui._extension_model_pending is None)
        runtime = server._orchestrator._runtimes['main']
        session = runtime._active_session
        process = session.extensions._manager._clients['providerdynamic'].process
        generation = session.extensions.generation
        assert session.agent.context_window == runtime.context_window == ui._context_window == 48000
        ui._handle_submit('use dynamically declared provider')
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        assert 'DYNAMIC-PROVIDER-ANSWER' in '\n'.join(ui._chat_log.render(120)) and not calls
        assert session.agent.context_window == runtime.context_window == (16000 if lifecycle else 48000)
        ui._editor.set_text('parent draft retained across provider metadata update')
        await command('ext-providerdynamic-replace')
        assert session.agent.state.model == alias
        assert session.agent.context_window == runtime.context_window == ui._context_window == 16000
        assert session.extensions.provider_model(alias).supports_tool_calls is False
        assert not session.extensions.provider_model(alias).supports_image_input
        assert ui._editor.get_text() == 'parent draft retained across provider metadata update'
        ui._editor.set_text('')
        ui._handle_submit('use replaced text-only provider')
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        assert 'TEXT-ONLY-NO-API-TOOLS' in '\n'.join(ui._chat_log.render(120))
        assert session.agent.state.tools and session.agent.supports_tool_calls is False
        await command('ext-providerdynamic-disable')
        assert session.extensions.provider_model(alias) is None and session.agent.state.model == alias
        assert ui._context_window == 0
        ui._handle_submit('removed provider must not fall back')
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        assert not calls and session.agent.state.model == alias
        assert runtime.transcript_store.get_extension_model(session.meta.session_id) == alias
        assert session.extensions.generation == generation and process.returncode is None
        assert await session.extensions.command('ext-providerdynamic-count', '') == 'CALLS=2;CLOSED=2'
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_model_display_limits_ignore_foreign_model_and_invalid_or_legacy_metadata():
    ui = ChatApp('/tmp/unused-provider-dynamic.sock')
    try:
        ui._state['model'] = 'ext-providerdynamic-live/echo'
        ui._context_window = 16000
        ui._accept_extension_model_limits({'model': 'foreign/model', 'model_context_window': 48000})
        assert ui._context_window == 16000 and ui._state['model'] == 'ext-providerdynamic-live/echo'
        for value in (True, -1, 2**32, '48000', None):
            ui._accept_extension_model_limits({'model': ui._state['model'], 'model_context_window': value})
            assert ui._context_window == 16000
        ui._accept_extension_model_limits({'model': ui._state['model']})  # Older sender: no guessed limits.
        assert ui._context_window == 16000
    finally:
        ui._request_exit()


def test_model_context_and_usage_remain_visible_at_default_width_with_long_aliases():
    from xdog.tui.utils import strip_ansi, visible_width

    ui = ChatApp('/tmp/unused-provider-dynamic.sock')
    try:
        ui._state.update(model='ext-' + 'provider' * 8 + '/echo', session_id='long-session-identity',
                         group_id='long-group-identity', connection_status='connected', activity_status='idle')
        ui._context_window = 16000
        ui._render_status()
        rows = ui._footer.render(80)
        assert len(rows) == 1 and visible_width(rows[0]) <= 80
        assert '16k' in strip_ansi(rows[0]) and '↑0' in strip_ansi(rows[0]) and '↓0' in strip_ansi(rows[0])
    finally:
        ui._request_exit()
