from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager, PermissionRequest
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.components.extension_custom import ExtensionCustom
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import CURSOR_MARKER

FIXTURE = Path(__file__).parents[2] / 'agent/tests/fixtures/extensions'


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('rich', [False, True])
async def test_real_coding_custom_parent_child_ownership_draft_permission_and_no_replay(tmp_path, child, cancel, rich):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['custom'], 'extension_dir': str(root)}))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_custom_'))
            result = AssistantMessage(content=(ToolCall(id='view', name=tool.name,
                                                       arguments={'mode': 'rich' if rich else ''}),))
        else:
            result = AssistantMessage(content=(TextContent(text='child done'),))
        return EventStream.empty(result)
    store = SessionManager(tmp_path / 'sessions')
    permissions = PermissionManager('allow-all' if child else 'ask')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=permissions)
    ui = InteractiveMode(session)
    if rich:
        ui._tui._running = True  # Virtual terminal; physical alternate-buffer behavior is tested separately.
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                if isinstance(ui._extension_dialog_panel, ExtensionCustom):
                    current = ui._extension_dialog_panel
                    if rich:
                        ui._layout.set_height(30)
                        ui._layout.render(80)
                        if current._details:
                            current.handle_input(KeyEvent('end'))
                            assert 'INTRO-TAIL' in '\n'.join(current.render(80))
                            current.handle_input(KeyEvent('f1'))
                    current.render(80)
                await asyncio.sleep(0.003)
    try:
        ui._editor.set_text('parent draft')
        ui._editor._cursor = 3
        if child:
            identifier = await session.children.supervisor.start('custom child', access='inherit')
        else:
            ui._handle_slash_command('ext-custom-view', 'rich' if rich else '')
        await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionCustom)
                   and ui._extension_dialog_panel._painted > 0)
        panel = ui._extension_dialog_panel
        assert isinstance(panel, ExtensionCustom)
        old = {'type': 'extension_custom_frame', 'id': panel.dialog['id'], 'revision': 999,
               'extension_session': 'foreign', 'lines': ['foreign']}
        ui._handle_ui_event(old)
        assert panel._revision < 999
        if child:
            assert panel.dialog['child_id'] == identifier
            assert not session.children.extensions.request_custom(identifier, panel.dialog['id'], 'foreign', {})
            assert not session.children.extensions.request_custom('foreign', panel.dialog['id'], ui._child_ui_lease, {})
        else:
            ui._show_permission_request(PermissionRequest('separate', 'bash', {}, 'Host approval'))
            ui._poll()
            assert ui._tui._focused is ui._permission_prompt and panel._painted == -1
            ui._permission_prompt._on_decision('deny')
            await wait(lambda: panel._painted > 0)
        if rich:
            assert panel.fullscreen and panel.wants_key_release and ui._layout.fullscreen_view is panel
            panel.handle_input(KeyEvent('q', event_type=KeyEventType.REPEAT))
            await wait(lambda: panel._frame is not None and 'repeat:q' in str(panel._frame['lines']))
            panel.render(80)
            panel.handle_input(KeyEvent('q', event_type=KeyEventType.RELEASE))
            await wait(lambda: panel._frame is not None and 'release:q' in str(panel._frame['lines']))
            panel.render(80)
        raw = '\n'.join(panel.render(80))
        assert '\x1b]52' not in raw and '\x1b[2J' not in raw and raw.count(CURSOR_MARKER) == 1
        panel.handle_paste('first\nsecond')
        await wait(lambda: panel._frame is not None and 'first' in str(panel._frame['lines']))
        panel.render(80)
        panel.handle_input(KeyEvent('escape' if cancel else 'enter'))
        if child:
            await wait(lambda: not session.children.supervisor.active)
            result = await session.children.supervisor.control('wait', identifier)
            assert result['status'] == 'completed' and len(calls) == 2
            assert ('"value": null' if cancel else '"selected": false') in str(result['messages'])
        else:
            await wait(lambda: ui._command is None)
            assert not calls
            text = '\n'.join(ui._chat_log.render(200))
            assert '"value": null' in text if cancel else '"selected": false' in text
        assert ui._extension_dialog_panel is None and ui._tui._focused is ui._editor
        assert not isinstance(ui._layout.fullscreen_view, ExtensionCustom)
        assert ui._editor.get_text() == 'parent draft' and ui._editor._cursor == 3
        assert session.permissions.mode == ('allow-all' if child else 'ask')
        assert not session.agent.state.messages
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('scroll', [False, True], ids=['static', 'scroll'])
@pytest.mark.parametrize('overlay', [False, True], ids=['inline', 'overlay'])
async def test_actual_coding_structured_layout_component_cursor_nested_resize_and_original_worker(
    tmp_path, child, scroll, overlay,
):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['layout'], 'extension_dir': str(root)}))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_layout_'))
            mode = ('scroll ' if scroll else '') + ('overlay' if overlay else '')
            result = AssistantMessage(content=(ToolCall(id='layout', name=tool.name, arguments={'mode': mode}),))
        else:
            result = AssistantMessage(content=(TextContent(text='done'),))
        return EventStream.empty(result)
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('allow-all' if child else 'ask'))
    ui = InteractiveMode(session)
    async def wait(predicate, width=80):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                if isinstance(ui._extension_dialog_panel, ExtensionCustom):
                    if ui._extension_dialog_panel.overlay:
                        ui._tui._composite_overlays(ui._layout.render(width), width, 24)
                    else:
                        ui._extension_dialog_panel.set_render_budget(12)
                        ui._extension_dialog_panel.render(width)
                await asyncio.sleep(0.003)
    try:
        ui._editor.set_text('parent draft')
        ui._editor._cursor = 3
        if child:
            await session.children.supervisor.start('layout child', access='inherit')
        else:
            ui._handle_slash_command('ext-layout-view', ('scroll ' if scroll else '') + ('overlay' if overlay else ''))
        await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionCustom)
                   and ui._extension_dialog_panel._painted > 0)
        panel = ui._extension_dialog_panel
        if overlay:
            assert panel.overlay and ui._tui.has_overlay() and not ui._custom_presentation.blocked()
            assert ui._layout.details is None and not ui._tui.fullscreen
            assert panel._geometry[0] == 60
        panel.handle_paste('界e\u0301')
        await wait(lambda: panel._frame is not None and '界e\u0301' in ''.join(panel._frame['lines']))
        raw = '\n'.join(panel.render(panel._geometry[0] if overlay else 80))
        assert 'STRUCTURED-LAYOUT' in raw and 'Info' in raw and raw.count(CURSOR_MARKER) == 1
        assert '\x1b]52' not in raw
        if scroll:
            assert 'ROW-0000' in raw
            panel.handle_input(KeyEvent('pagedown'))
            await wait(lambda: panel._frame is not None and 'ROW-0020' in ''.join(panel._frame['lines']))
            panel.render(panel._geometry[0] if overlay else 80)
            panel.handle_input(KeyEvent('f3'))
            await wait(lambda: panel._frame is not None and 'PAN-0020' in ''.join(panel._frame['lines']))
            panel.render(panel._geometry[0] if overlay else 80)
        panel.handle_input(KeyEvent('f5'))
        await wait(lambda: panel._frame is not None and 'LAYER-POPUP' in ''.join(panel._frame['lines']))
        assert panel._frame['cursor'] is None
        panel.render(panel._geometry[0] if overlay else 80)
        panel.handle_paste('ignored while popup')
        await wait(lambda: panel._inflight == 0)
        panel.render(panel._geometry[0] if overlay else 80)
        panel.handle_input(KeyEvent('f5'))
        await wait(lambda: panel._frame is not None and 'LAYER-POPUP' not in ''.join(panel._frame['lines'])
                   and panel._frame['cursor'] is not None)
        assert '界e\u0301' in ''.join(panel._frame['lines'])
        panel.render(panel._geometry[0] if overlay else 80)
        panel.handle_input(KeyEvent('f2'))
        await wait(lambda: ui._extension_dialog_panel is not None and ui._extension_dialog_panel is not panel)
        nested = ui._extension_dialog_panel
        nested._input.set_text('nested answer')
        nested.render(80)
        nested.handle_input(KeyEvent('enter'))
        await wait(lambda: ui._extension_dialog_panel is panel and panel._painted > 0)
        assert '界e\u0301' in ''.join(panel._frame['lines'])
        if scroll:
            assert 'PAN-0020' in ''.join(panel._frame['lines'])
        ui._tui._composite_overlays(ui._layout.render(40), 40, 24) if overlay else panel.render(40)
        panel.poll()
        await wait(lambda: panel._painted > 0 and panel._geometry[0] == (30 if overlay else 40), width=40)
        assert panel._frame['cursor'][1] < 40
        if scroll:
            assert 'PAN-0020' in ''.join(panel._frame['lines'])
            panel.handle_input(KeyEvent('pageup'))
            await wait(lambda: panel._frame is not None and 'PAN-0000' in ''.join(panel._frame['lines']), width=40)
            panel.render(panel._geometry[0] if overlay else 40)
            panel.handle_input(KeyEvent('f4'))
            await wait(lambda: panel._frame is not None and 'ROW-0000' in ''.join(panel._frame['lines']), width=40)
            panel.render(panel._geometry[0] if overlay else 40)
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: ui._extension_dialog_panel is None, width=40)
        assert not ui._tui.has_overlay()
        assert ui._tui._focused is (ui._child_panel or ui._editor)
        assert ui._editor.get_text() == 'parent draft' and ui._editor._cursor == 3
        if child:
            await wait(lambda: not session.children.supervisor.active, width=40)
        assert len(calls) == (2 if child else 0)
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()


@pytest.mark.parametrize('child', [False, True])
async def test_actual_coding_live_custom_repaints_without_input_and_preserves_parent(tmp_path, child):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE / 'liveview', root / 'liveview')
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['liveview'], 'extension_dir': str(root)}))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_liveview_'))
            result = AssistantMessage(content=(ToolCall(id='view', name=tool.name, arguments={}),))
        else:
            result = AssistantMessage(content=(TextContent(text='done'),))
        return EventStream.empty(result)
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('allow-all' if child else 'ask'))
    ui = InteractiveMode(session)
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                if isinstance(ui._extension_dialog_panel, ExtensionCustom):
                    ui._extension_dialog_panel.render(80)
                await asyncio.sleep(0.003)
    try:
        ui._editor.set_text('untouched parent')
        ui._editor._cursor = 4
        if child:
            identifier = await session.children.supervisor.start('live child', access='inherit')
        else:
            ui._handle_slash_command('ext-liveview-view', '')
        await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionCustom)
                   and 'LIVE-STATE=2;INPUTS=0' in str(ui._extension_dialog_panel._frame)
                   and ui._extension_dialog_panel._painted > 0)
        panel = ui._extension_dialog_panel
        assert panel._revision == 3  # Initial actual resize plus two coalesced background updates.
        panel.handle_input(KeyEvent('enter'))
        if child:
            await wait(lambda: not session.children.supervisor.active)
            result = await session.children.supervisor.control('wait', identifier)
            assert '"state": 2, "inputs": 1' in str(result['messages']) and len(calls) == 2
        else:
            await wait(lambda: ui._command is None)
            assert '"state": 2, "inputs": 1' in '\n'.join(ui._chat_log.render(200)) and not calls
        assert ui._extension_dialog_panel is None and ui._tui._focused is ui._editor
        assert ui._editor.get_text() == 'untouched parent' and ui._editor._cursor == 4
        assert not session.extension_dialogs.custom.views and not session.agent.state.messages
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('cancel', [False, True])
async def test_actual_coding_live_overlay_parent_edit_hide_show_capture_and_host_approval(tmp_path, child, cancel):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE / 'overlaymock', root / 'overlaymock')
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['overlaymock'], 'extension_dir': str(root)}))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_overlaymock_'))
            result = AssistantMessage(content=(ToolCall(id='view', name=tool.name, arguments={}),))
        else:
            result = AssistantMessage(content=(TextContent(text='done'),))
        return EventStream.empty(result)
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('allow-all' if child else 'ask'))
    ui = InteractiveMode(session)
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                ui._tui._composite_overlays(ui._layout.render(80), 80, 24)
                await asyncio.sleep(0.003)
    try:
        ui._editor.set_text('parent draft')
        ui._editor._cursor = 6
        if child:
            identifier = await session.children.supervisor.start('overlay child', access='inherit')
        else:
            ui._handle_slash_command('ext-overlaymock-view', '')
        await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionCustom)
                   and ui._extension_dialog_panel._painted > 0)
        panel = ui._extension_dialog_panel
        owner = session.children.extensions.owners[identifier] if child else session.extensions
        assert not panel.focused and ui._tui._focused is ui._editor
        assert CURSOR_MARKER not in '\n'.join(panel.render(44))
        ui._tui._dispatch_input(KeyEvent('!'))
        ui._tui._dispatch_paste('edited ')
        expected = 'parent!edited  draft'
        assert ui._editor.get_text() == expected
        async def control(action):
            assert await owner.command('ext-overlaymock-control', action) == 'QUEUED'
            await wait(lambda: panel.dialog.get('overlay_state', {}).get('sequence', 0) > control.sequence)
            control.sequence = panel._control_sequence
        control.sequence = 0
        await control('hide')
        assert ui._custom_presentation.overlay_handle.is_hidden() and ui._tui._focused is ui._editor
        await control('show')
        await wait(lambda: panel._painted > 0)
        assert ui._tui._focused is ui._editor and 'INPUTS=0' in str(panel._frame)
        await control('focus')
        await wait(lambda: panel._painted > 0 and panel.focused)
        assert ui._tui._focused is panel
        panel.handle_input(KeyEvent('f2'))
        await wait(lambda: ui._extension_dialog_panel is not panel
                   and ui._extension_dialog_panel.dialog['kind'] == 'input')
        nested = ui._extension_dialog_panel
        await control('unfocus')  # Cached parent state cannot steal its nested question's focus.
        assert ui._tui._focused is nested
        nested.handle_input(KeyEvent('escape'))
        await wait(lambda: ui._extension_dialog_panel is panel and panel._painted > 0 and not panel.focused)
        assert ui._tui._focused is ui._editor
        await control('focus')
        await wait(lambda: panel._painted > 0 and panel.focused)

        # A forged session cannot release/capture the original view.
        ui._handle_ui_event({'type': 'extension_custom_control', 'id': panel.dialog['id'],
            'extension_session': 'foreign', 'state': {'sequence': 999, 'hidden': True, 'capturing': False}})
        assert panel._control_sequence < 999
        await control('unfocus')
        await wait(lambda: panel._painted > 0 and not panel.focused)
        assert ui._tui._focused is ui._editor
        ui._show_permission_request(PermissionRequest('protected', 'bash', {}, 'Host approval'))
        await control('focus')
        assert ui._tui._focused is ui._permission_prompt and ui._custom_presentation.overlay_handle is None
        ui._permission_prompt._on_decision('deny')
        await wait(lambda: panel._painted > 0 and panel.focused)
        if cancel:
            await control('unfocus')
            await wait(lambda: panel._painted > 0 and not panel.focused)
            ui._tui._dispatch_input(KeyEvent('escape'))
        else:
            panel.handle_input(KeyEvent('enter'))
        if child:
            await wait(lambda: not session.children.supervisor.active)
            result = await session.children.supervisor.control('wait', identifier)
            assert ('"value": null' if cancel else '"inputs": ["f2", "enter"]') in str(result['messages'])
            assert len(calls) == 2
        else:
            await wait(lambda: ui._command is None)
            assert ('"value": null' if cancel else '"inputs": ["f2", "enter"]') in '\n'.join(ui._chat_log.render(200))
            assert not calls
        assert ui._extension_dialog_panel is None and not ui._tui.has_overlay()
        assert ui._tui._focused is ui._editor and ui._editor.get_text() == expected
        assert ui._editor._cursor == 14 and not session.agent.state.messages
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()
