from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


@pytest.mark.parametrize('lifecycle', [False, True])
async def test_coding_live_provider_add_replace_remove_catalog_caps_and_explicit_no_fallback(tmp_path, lifecycle):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['providerdynamic'], 'extension_dir': str(root),
        'extension_settings': {'providerdynamic': {'turn_replace': lifecycle}}}))
    calls = []

    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(base, config=AgentConfig(model='base/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('ask'))
    ui = InteractiveMode(session)
    alias = 'ext-providerdynamic-live/echo'

    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)

    async def command(name, args=''):
        ui._handle_slash_command(name, args)
        await wait(lambda: ui._command is None)

    try:
        await command('extensions')
        process = session.extensions._manager._clients['providerdynamic'].process
        generation = session.extensions.generation
        await command('ext-providerdynamic-enable')
        await command('model')
        assert alias in '\n'.join(ui._chat_log.render(120))
        await command('model', alias)
        assert session.model_info.context_window == session.agent.context_window == 48000
        assert session.model_info.supports_image_input
        ui._handle_submit('use dynamically declared provider')
        await wait(lambda: not ui._worker_active and not ui._is_busy)
        assert 'DYNAMIC-PROVIDER-ANSWER' in '\n'.join(ui._chat_log.render(120)) and not calls
        assert session.agent.context_window == session.context_window == (16000 if lifecycle else 48000)
        ui._editor.set_text('parent draft retained across provider metadata update')
        await command('ext-providerdynamic-replace')
        assert session.model == alias and session.agent.context_window == session.context_window == 16000
        assert session.model_info.supports_tool_calls is False and not session.model_info.supports_image_input
        with pytest.raises(ValueError, match='image input'):
            session.validate_image_inputs((('image/png', 10),))
        assert ui._editor.get_text() == 'parent draft retained across provider metadata update'
        ui._editor.set_text('')
        ui._handle_submit('use replaced text-only provider')
        await wait(lambda: not ui._worker_active and not ui._is_busy)
        assert 'TEXT-ONLY-NO-API-TOOLS' in '\n'.join(ui._chat_log.render(120))
        assert session.agent.state.tools and session.agent.supports_tool_calls is False
        await command('ext-providerdynamic-disable')
        assert session.model_info is None and session.model == alias
        ui._handle_submit('removed provider must not fall back')
        await wait(lambda: not ui._worker_active and not ui._is_busy)
        assert not calls and session.permissions.mode == 'ask' and session.model == alias
        assert store.load_session(session.session_id).model == alias
        assert session.extensions.generation == generation and process.returncode is None
        assert await session.extensions.command('ext-providerdynamic-count', '') == 'CALLS=2;CLOSED=2'
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
