"""Actual native-adapter metadata guards retained images in all three application paths."""
from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, ImageContent, Model, TextContent, UserMessage, VisionLimits
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
IMAGE = ImageContent(data=PNG, mime_type='image/png')


class Adapter:
    def __init__(self):
        self.profile = Model(id='configured/vision', input=('text', 'image'), context_window=128000,
            vision_limits=VisionLimits(max_prompt_images=1, max_prompt_image_size=1024,
                                       supported_media_types=('image/png',)))
        self.calls = []
    def resolve_model(self, name):
        return self.profile if name == self.profile.id else None
    def models(self):
        return (self.profile,)
    def __call__(self, model, context, options):
        self.calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='SHOULD-NOT-BE-CALLED'),)))


@pytest.mark.parametrize('mode', ['sdk', 'print', 'coding-ui', 'claw'])
async def test_retained_plus_new_image_exceeds_actual_model_context_without_pruning_or_request(tmp_path, mode):
    adapter = Adapter()
    server = ui = None
    if mode != 'claw':
        store = SessionManager(tmp_path / 'sessions')
        owner = AgentSession(agent=Agent(adapter, config=AgentConfig(model=adapter.profile.id)),
            session_data=store.create_session(working_dir=tmp_path, model=adapter.profile.id), session_manager=store,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        owner.model_info = adapter.profile
    else:
        config = ClawConfig(model=adapter.profile.id, data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'image.sock'), pid_file=str(tmp_path / 'image.pid'),
            groups=(GroupDef(id='main', is_main=True),))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                   return_value=(adapter.profile.id, adapter)):
            await server.start()
        runtime = server._orchestrator._runtimes['main']
        owner = runtime.get_or_create_session(allow_reset=False)
        runtime.image_model_info = lambda: adapter.profile  # SAME executing adapter, never a global borrowed model.
        owner._restored = True
    owner.agent.replace_messages([UserMessage(content=(IMAGE,)),
                                  AssistantMessage(content=(TextContent(text='Previous answer'),))])
    try:
        if mode == 'sdk':
            with pytest.raises(RuntimeError, match='image-count'):
                await owner.send_message('New image', images=(IMAGE,))
        elif mode == 'print':
            from xdog.coding.modes.print_mode import run_print_mode
            assert await run_print_mode(owner, 'New image', images=(IMAGE,)) == 1
        elif mode == 'coding-ui':
            ui = InteractiveMode(owner)
            await ui._async_agent_turn('New image', images=(attachment(base64.b64decode(PNG), 'image.png'),))
            assert 'image-count' in owner.agent.state.error
        else:
            result = await owner.run_turn(UserInput(group_id='main', content='New image', images=(IMAGE,)))
            assert 'image-count' in result.error
        assert not adapter.calls
        retained = [part for message in owner.agent.messages
                    for part in (() if isinstance(message.content, str) else message.content)
                    if isinstance(part, ImageContent)]
        assert retained == [IMAGE, IMAGE]  # No image pruned to make the request appear valid.
        assert not owner.agent.is_streaming
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
