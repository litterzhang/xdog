"""Actual worker withdraws vision in turn-start; no app may admit that turn/image or request compaction."""
from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, ImageContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.types import UserInput
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='
ALIAS = 'ext-visionchange-local/vision'


@pytest.mark.parametrize('mode', ['sdk', 'print', 'coding-ui', 'claw'])
async def test_actual_turn_start_provider_withdrawal_refuses_image_before_model_history_and_compaction(tmp_path, mode):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/visionchange', root / 'visionchange')
    marker = tmp_path / 'called'
    calls, compactions = [], []
    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    async def compact():
        compactions.append(True)
    server = ui = None
    if mode != 'claw':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['visionchange'], 'extension_dir': str(root),
                                        'extension_settings': {'visionchange': {'called': str(marker)}}}))
        manager = SessionManager(tmp_path / 'sessions')
        owner = AgentSession(agent=Agent(base, config=AgentConfig(model='offline/model')),
            session_data=manager.create_session(model='offline/model', working_dir=tmp_path), session_manager=manager,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('deny'))
        await owner.initialize_extensions()
        owner.set_model(ALIAS)
        owner._maybe_compact = compact
        previous = owner.agent.state.messages
    else:
        config = ClawConfig(model=ALIAS, data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'vision.sock'), pid_file=str(tmp_path / 'vision.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('visionchange',), extension_dir=str(root),
            extension_settings={'visionchange': {'called': str(marker)}})
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(ALIAS, base)):
            await server.start()
        runtime = server._orchestrator._runtimes['main']
        owner = runtime.get_or_create_session(allow_reset=False)
        owner._maybe_compact = compact
        previous = owner.agent.state.messages
    try:
        assert owner.extensions.provider_model(ALIAS).supports_image_input
        image = ImageContent(data=PNG, mime_type='image/png')
        if mode == 'sdk':
            with pytest.raises(ValueError, match='image input'):
                await owner.send_message('Refuse stale vision', images=(image,))
        elif mode == 'print':
            from xdog.coding.modes.print_mode import run_print_mode
            assert await run_print_mode(owner, 'Refuse stale vision', images=(image,)) == 1
        elif mode == 'coding-ui':
            ui = InteractiveMode(owner)
            with pytest.raises(ValueError, match='image input'):
                await ui._async_agent_turn('Refuse stale vision',
                                          images=(attachment(base64.b64decode(PNG), 'test.png'),))
        else:
            result = await owner.run_turn(UserInput(group_id='main', content='Refuse stale vision', images=(image,)))
            assert 'image input' in result.error
        assert not calls and not marker.exists() and not compactions
        assert owner.agent.state.messages is previous
        assert not owner.extensions.provider_model(ALIAS).supports_image_input
    finally:
        if ui is not None:
            ui._request_exit()
            ui._session_events.close()
        if server is not None:
            await server.stop()
        elif ui is None:
            await owner.children.runtime.aclose()
            await owner.extensions.aclose()
            owner.dispose()
