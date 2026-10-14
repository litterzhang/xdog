"""UI-037 renderer edges through both real host/worker paths (no live model)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.components.extension_custom import ExtensionCustom
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import CURSOR_MARKER
from xdog.tui.utils import string_width, strip_ansi

PLUGIN = r'''
from xdog.tui import Component, Container
from xdog.tui.tui import CURSOR_MARKER
from xdog.tui.components.custom_layout import (
    CustomChild, CustomColumn, CustomComponent, CustomRow, CustomText, custom_frame,
)

class Cached(Component):
    def __init__(self):
        self.prefix, self.cache, self.invalidations = 'x', {}, 0
    def render(self, width):
        return self.cache.setdefault(width, [self.prefix + '👩' + CURSOR_MARKER + '\x1b[31m\u200d💻'])
    def invalidate(self):
        self.cache.clear()
        self.invalidations += 1

def setup(api):
    async def command(args, context):
        cached, container = Cached(), Container()
        container.add_child(cached)
        nested = 'none'
        def render(width, height):
            return custom_frame(CustomColumn((
                CustomChild(CustomRow((
                    CustomChild(CustomText(('👩\x1b[32m\u200d💻') * 3, wrap=True), size=2),
                    CustomChild(CustomComponent(container)),
                ), gap=1), size=3),
                CustomChild(CustomText(f'EDGE-{cached.invalidations}-{nested}'), size=1),
            )), width, height)
        async def on_input(event):
            nonlocal nested
            if event['kind'] == 'key' and event['key'] == 'u':
                cached.prefix = 'y'
                container.invalidate()
            elif event['kind'] == 'key' and event['key'] == 'f2':
                nested = await api.input('Renderer nested question', default='retained') or 'cancel'
            elif event['kind'] == 'key' and event['key'] == 'enter':
                return {'done': True, 'value': {'invalidations': cached.invalidations, 'nested': nested}}
        return str(await api.custom('Renderer contract', render, on_input,
            fullscreen=args == 'fullscreen',
            overlay={'width': '75%', 'max_height': 12, 'anchor': 'top-right'} if args == 'overlay' else None))
    api.command('view', 'Renderer contract', command)
    async def tool(params, context):
        return await command(params.get('mode', ''), context)
    api.tool('view', 'Renderer contract, not approval', {'type': 'object'}, tool)
'''


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('child', [False])
@pytest.mark.parametrize('presentation', ['inline'])
async def test_actual_owned_renderer_graphemes_cursor_cache_nested_and_resize(tmp_path, host, child, presentation):
    root = tmp_path / 'extensions'
    plugin = root / 'renderedge'
    plugin.mkdir(parents=True)
    (plugin / 'extension.yaml').write_text('name: renderedge\nentry_point: main\n')
    (plugin / 'main.py').write_text(PLUGIN)
    calls = []

    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_renderedge_'))
            result = AssistantMessage(content=(ToolCall(id='edge', name=tool.name,
                                                       arguments={'mode': presentation}),))
        else:
            result = AssistantMessage(content=(TextContent(text='child renderer done'),))
        return EventStream.empty(result)

    session = server = writer = task = None
    if host == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['renderedge'], 'extension_dir': str(root)}))
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('allow-all' if child else 'ask'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'renderer.sock'), pid_file=str(tmp_path / 'renderer.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('renderedge',), extension_dir=str(root),
            extension_allow_tools=('renderedge',))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    if presentation == 'fullscreen':
        ui._tui._running = True  # Physical buffer restoration has separate Linux acceptance tests.

    async def wait(predicate, width=80):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                panel = ui._extension_dialog_panel
                if isinstance(panel, ExtensionCustom):
                    ui._layout.set_height(24)
                    if panel.overlay:
                        ui._tui._composite_overlays(ui._layout.render(width), width, 24)
                    elif panel.fullscreen:
                        ui._layout.render(width)
                    else:
                        panel.set_render_budget(10)
                        panel.render(width)
                await asyncio.sleep(0.003)

    def assert_frame(panel):
        assert panel._frame['cursor'] == [0, 4]
        rows = [strip_ansi(line) for line in panel._frame['lines']]
        assert all(line.startswith('👩\u200d💻') for line in rows[:3])
        assert all(string_width(line) <= panel._geometry[0] for line in panel._frame['lines'])
        assert all(CURSOR_MARKER not in line for line in panel._frame['lines'])
        rendered = panel.render(panel._geometry[0])
        assert ''.join(rendered).count(CURSOR_MARKER) == 1
        cursor_line = next(line for line in rendered if CURSOR_MARKER in line)
        assert string_width(cursor_line.split(CURSOR_MARKER)[0]) == 4

    try:
        if host == 'claw':
            await wait(lambda: ui._connection_ready)
        ui._editor.set_text('preserved parent draft')
        ui._editor._cursor = 3
        if session is not None:
            if child:
                await session.children.supervisor.start('renderer child', access='inherit')
            else:
                ui._handle_slash_command('ext-renderedge-view', presentation)
        elif child:
            ui._agents_command('start --inherit renderer child')
        else:
            ui._extension_command('ext-renderedge-view', presentation)
        await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionCustom)
                   and ui._extension_dialog_panel._painted > 0)
        panel = ui._extension_dialog_panel
        assert_frame(panel)
        assert 'EDGE-0-none' in ''.join(panel._frame['lines'])
        panel.handle_input(KeyEvent('u'))
        await wait(lambda: panel._frame is not None and 'EDGE-1-none' in ''.join(panel._frame['lines']))
        assert_frame(panel)
        assert 'y👩\u200d💻' in strip_ansi(panel._frame['lines'][0])
        panel.handle_input(KeyEvent('f2'))
        await wait(lambda: ui._extension_dialog_panel is not None and ui._extension_dialog_panel is not panel)
        nested = ui._extension_dialog_panel
        nested._input.set_text('answer')
        nested.render(80)
        nested.handle_input(KeyEvent('enter'))
        await wait(lambda: ui._extension_dialog_panel is panel and panel._painted > 0)
        assert_frame(panel)
        assert 'EDGE-1-answer' in ''.join(panel._frame['lines'])
        old_generation = panel._generation
        panel.render(32)
        assert panel._generation > old_generation and panel._painted == -1
        expected_width = 24 if presentation == 'overlay' else 32
        await wait(lambda: panel._painted > 0 and panel._geometry[0] == expected_width, width=32)
        assert_frame(panel)
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: ui._extension_dialog_panel is None, width=32)
        if child:
            supervisor = (session.children.supervisor if session is not None
                          else server._orchestrator._runtimes['main']._active_session.children.supervisor)
            await wait(lambda: not supervisor.active, width=32)
        assert len(calls) == (2 if child else 0)
        assert ui._editor.get_text() == 'preserved parent draft' and ui._editor._cursor == 3
        assert not ui._tui.has_overlay()
    finally:
        ui._request_exit()
        if session is not None:
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        if server is not None:
            await server.stop()
