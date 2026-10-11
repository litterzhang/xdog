from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.components.extension_custom import ExtensionCustom
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import CURSOR_MARKER

FIXTURE = Path(__file__).parents[2] / 'agent/tests/fixtures/extensions'


async def start(tmp_path, calls, *, rich=False, plugin='custom', scroll=False, overlay=False):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_' + plugin + '_'))
            mode = 'scroll' if scroll else 'rich' if rich else ''
            if overlay:
                mode += ' overlay'
            result = AssistantMessage(content=(ToolCall(id='view', name=tool.name, arguments={'mode': mode}),))
        else:
            result = AssistantMessage(content=(TextContent(text='child done'),))
        return EventStream.empty(result)
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'custom.sock'), pid_file=str(tmp_path / 'custom.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=(plugin,), extension_dir=str(root),
        extension_allow_tools=(plugin,))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    return server, config


async def read_type(reader, kind):
    async with asyncio.timeout(6):
        while True:
            value = json.loads(await reader.readline())
            if value['type'] == kind:
                return value


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('rich', [False, True])
async def test_real_claw_custom_parent_child_transport_leases_sanitization_and_no_replay(tmp_path, child, cancel, rich):
    calls = []
    server, config = await start(tmp_path, calls, rich=rich)
    ui = ChatApp(config.socket_path)
    if rich:
        ui._tui._running = True
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    other_reader, other_writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
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
        await wait(lambda: ui._connection_ready)
        assert ui._extension_custom_supported
        ui._editor.set_text('parent draft')
        ui._editor._cursor = 3
        if child:
            ui._agents_command('start --inherit custom child')
        else:
            ui._extension_command('ext-custom-view', 'rich' if rich else '')
        await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionCustom)
                   and ui._extension_dialog_panel._painted > 0)
        panel = ui._extension_dialog_panel
        assert isinstance(panel, ExtensionCustom)
        item = panel.dialog
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
        request = {'type': 'child_extension_custom_request' if child else 'extension_custom_request',
                   'id': item['id'], 'group_id': 'main', 'session_id': ui._gateway_session_id,
                   'event': {'kind': 'resize', 'width': 80, 'height': 5, 'painted': -1,
                             'generation': panel._generation}}
        if child:
            request.update(child_id=item['child_id'], child_owner=item['child_owner'])
        other_writer.write((json.dumps({'type': 'ping', 'extension_custom': 1}) + '\n').encode())
        await other_writer.drain()
        await read_type(other_reader, 'pong')
        other_writer.write((json.dumps(request) + '\n').encode())
        await other_writer.drain()
        ack = await read_type(other_reader, request['type'].replace('_request', '_ack'))
        assert not ack['accepted']  # Stolen ID/token cannot adopt another connection's view.
        ui._queue_request({**request, 'session_id': 'foreign'})
        await asyncio.sleep(0.03)
        ui._poll()
        assert ui._extension_dialog_panel is panel
        ui._queue_request({'type': 'ping', 'group_id': 'main'})
        panel.handle_paste('first\nsecond')
        await wait(lambda: panel._frame is not None and 'first' in str(panel._frame['lines']))
        panel.render(80)
        panel.handle_input(KeyEvent('escape' if cancel else 'enter'))
        if child:
            session = server._orchestrator._runtimes['main']._active_session
            await wait(lambda: not session.children.supervisor.active and not ui._children_pending)
            result = await session.children.supervisor.control('wait', item['child_id'])
            assert result['status'] == 'completed' and len(calls) == 2
            assert ('"value": null' if cancel else '"selected": false') in str(result['messages'])
        else:
            await wait(lambda: ui._extension_pending is None)
            assert not calls
            text = '\n'.join(ui._chat_log.render(200))
            assert '"value": null' in text if cancel else '"selected": false' in text
        assert ui._extension_dialog_panel is None and ui._tui._focused is ui._editor
        assert not isinstance(ui._layout.fullscreen_view, ExtensionCustom)
        assert ui._editor.get_text() == 'parent draft' and ui._editor._cursor == 3
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        other_writer.close()
        await asyncio.gather(writer.wait_closed(), other_writer.wait_closed())
        await server.stop()


async def test_older_claw_viewer_custom_capability_cancels_without_sending_unknown_ui(tmp_path):
    calls = []
    server, config = await start(tmp_path, calls)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        writer.write(b'{"type":"extension_control","request_id":"old","group_id":"main",'
                     b'"session_id":"","action":"command","name":"ext-custom-view","args":""}\n')
        await writer.drain()
        seen = []
        async with asyncio.timeout(6):
            while True:
                value = json.loads(await reader.readline())
                seen.append(value)
                if value['type'] == 'extension_result':
                    break
        assert value['success'] and json.loads(value['output'])['value'] is None
        assert not calls and not any(frame['type'] == 'extension_dialog' for frame in seen)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('ending', ['disconnect', 'withdraw'])
@pytest.mark.parametrize('child', [False, True])
async def test_custom_view_disconnect_or_capability_withdrawal_cancels_owned_future(tmp_path, ending, child):
    calls = []
    server, config = await start(tmp_path, calls)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        writer.write(b'{"type":"ping","extension_custom":1}\n')
        await writer.drain()
        await read_type(reader, 'pong')
        if child:
            request = {'type': 'child_control', 'request_id': 'start', 'group_id': 'main', 'session_id': '',
                       'action': 'start', 'text': 'custom child', 'access': 'inherit'}
        else:
            request = {'type': 'extension_control', 'request_id': 'view', 'group_id': 'main', 'session_id': '',
                       'action': 'command', 'name': 'ext-custom-view', 'args': ''}
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        notice = await read_type(reader, 'child_extension_dialog' if child else 'extension_dialog')
        key = notice['dialog']['id']
        session = server._orchestrator._runtimes['main']._active_session
        if ending == 'disconnect':
            writer.close()
            await writer.wait_closed()
        else:
            writer.write(b'{"type":"ping","extension_custom":0}\n')
            await writer.drain()
        if child:
            result = await asyncio.wait_for(session.children.supervisor.control('wait', notice['child_id']), 6)
            assert result['status'] == 'completed' and '"value": null' in str(result['messages'])
            assert not session.children.extensions.contains(notice['child_id'], key, notice['child_owner'])
            assert len(calls) == 2
        else:
            if ending == 'withdraw':
                result = await read_type(reader, 'extension_result')
                assert result['success'] and json.loads(result['output'])['value'] is None
            async with asyncio.timeout(6):
                while session.extension_dialogs.contains(key):
                    await asyncio.sleep(0.005)
            assert not session.extension_dialogs.custom.views and not calls
        late_reader, late_writer = await asyncio.open_unix_connection(config.socket_path)
        try:
            late_writer.write((json.dumps({'type': 'extension_custom_request', 'id': key,
                'group_id': 'main', 'session_id': notice['session_id'], 'event': {}}) + '\n').encode())
            await late_writer.drain()
            assert not (await read_type(late_reader, 'extension_custom_ack'))['accepted']
        finally:
            late_writer.close()
            await late_writer.wait_closed()
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_actual_claw_parent_model_tool_custom_view_retains_run_owner_and_permission_gates(tmp_path):
    calls = []
    server, config = await start(tmp_path, calls)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        writer.write(b'{"type":"ping","extension_custom":1}\n')
        await writer.drain()
        await read_type(reader, 'pong')
        writer.write(b'{"type":"message","group_id":"main","run_id":"owned","content":"custom turn"}\n')
        await writer.drain()
        notice = await read_type(reader, 'extension_dialog')
        assert notice['run_id'] == 'owned'
        request = {'type': 'extension_custom_request', 'id': notice['dialog']['id'], 'group_id': 'main',
                   'session_id': notice['session_id'],
                   'event': {'kind': 'resize', 'width': 60, 'height': 3, 'painted': -1, 'generation': 1}}
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        frame = await read_type(reader, 'extension_custom_frame')
        assert frame['session_id'] == notice['session_id'] and frame['generation'] == 1
        request['event'] = {**request['event'], 'kind': 'key', 'key': 'c', 'ctrl': True, 'alt': False,
                            'shift': False, 'painted': frame['revision']}
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        assert not (await read_type(reader, 'extension_custom_ack'))['accepted']
        request['event'].update(key='enter', ctrl=False)
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        final = await read_type(reader, 'response')
        assert final['run_id'] == 'owned' and len(calls) == 2
        session = server._orchestrator._runtimes['main']._active_session
        assert '"selected": false' in str(session.agent.state.messages)
        assert not session.extension_dialogs.custom.views
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('withdraw', [False, True])
@pytest.mark.parametrize('overlay', [False, True], ids=['rich', 'overlay'])
async def test_claw_rich_capability_required_and_withdrawal_cancels_without_inline_downgrade(
    tmp_path, withdraw, overlay,
):
    calls = []
    server, config = await start(tmp_path, calls, plugin='layout' if overlay else 'custom')
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        writer.write((json.dumps({'type': 'ping', 'extension_custom': 1,
                                 'extension_custom_rich': 1 if overlay or withdraw else 0,
                                 'extension_custom_overlay': 1 if overlay and withdraw else 0}) + '\n').encode())
        await writer.drain()
        await read_type(reader, 'pong')
        writer.write((json.dumps({'type': 'extension_control', 'request_id': 'rich', 'group_id': 'main',
                                  'session_id': '', 'action': 'command',
                                  'name': 'ext-layout-view' if overlay else 'ext-custom-view',
                                  'args': 'overlay' if overlay else 'rich'}) + '\n').encode())
        await writer.drain()
        if withdraw:
            notice = await read_type(reader, 'extension_dialog')
            if overlay:
                assert notice['dialog']['overlay']['anchor'] == 'top-right' and not notice['dialog']['fullscreen']
            else:
                assert notice['dialog']['fullscreen']
                assert notice['dialog']['key_events'] == ['press', 'repeat', 'release']
            capability = 'extension_custom_overlay' if overlay else 'extension_custom_rich'
            writer.write((json.dumps({'type': 'ping', capability: 0}) + '\n').encode())
            await writer.drain()
        seen = []
        async with asyncio.timeout(6):
            while True:
                reply = json.loads(await reader.readline())
                seen.append(reply)
                if reply['type'] == 'extension_result':
                    break
        assert reply['success'] and json.loads(reply['output'])['value'] is None
        assert not calls and not any(item['type'] == 'extension_custom_frame' for item in seen)
        if not withdraw:
            assert not any(item['type'] == 'extension_dialog' for item in seen)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('scroll', [False, True], ids=['static', 'scroll'])
@pytest.mark.parametrize('overlay', [False, True], ids=['inline', 'overlay'])
async def test_actual_claw_structured_layout_component_cursor_nested_resize_and_original_owner(
    tmp_path, child, scroll, overlay,
):
    calls = []
    server, config = await start(tmp_path, calls, plugin='layout', scroll=scroll, overlay=overlay)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
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
        await wait(lambda: ui._connection_ready)
        ui._editor.set_text('parent draft')
        ui._editor._cursor = 3
        if child:
            ui._agents_command('start --inherit layout child')
        else:
            ui._extension_command('ext-layout-view', ('scroll ' if scroll else '') + ('overlay' if overlay else ''))
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
            await wait(lambda: not server._orchestrator._runtimes['main']._active_session.children.supervisor.active,
                       width=40)
        assert len(calls) == (2 if child else 0)
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('child', [False, True])
async def test_actual_claw_live_custom_repaints_without_input_under_original_connection(tmp_path, child):
    calls = []
    server, config = await start(tmp_path, calls, plugin='liveview')
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                if isinstance(ui._extension_dialog_panel, ExtensionCustom):
                    ui._extension_dialog_panel.render(80)
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        ui._editor.set_text('untouched parent')
        ui._editor._cursor = 4
        if child:
            ui._agents_command('start --inherit live child')
        else:
            ui._extension_command('ext-liveview-view', '')
        await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionCustom)
                   and 'LIVE-STATE=2;INPUTS=0' in str(ui._extension_dialog_panel._frame)
                   and ui._extension_dialog_panel._painted > 0)
        panel = ui._extension_dialog_panel
        assert panel._revision == 3
        panel.handle_input(KeyEvent('enter'))
        if child:
            session = server._orchestrator._runtimes['main']._active_session
            await wait(lambda: not session.children.supervisor.active and not ui._children_pending)
            result = await session.children.supervisor.control('wait', panel.dialog['child_id'])
            assert '"state": 2, "inputs": 1' in str(result['messages']) and len(calls) == 2
        else:
            await wait(lambda: ui._extension_pending is None)
            assert '"state": 2, "inputs": 1' in '\n'.join(ui._chat_log.render(200)) and not calls
        assert ui._extension_dialog_panel is None and ui._tui._focused is ui._editor
        assert ui._editor.get_text() == 'untouched parent' and ui._editor._cursor == 4
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('cancel', [False, True])
async def test_actual_claw_live_overlay_edit_controls_and_protected_host_focus(tmp_path, child, cancel):
    from xdog.tui.components.text import Text
    calls = []
    server, config = await start(tmp_path, calls, plugin='overlaymock')
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                ui._tui._composite_overlays(ui._layout.render(80), 80, 24)
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._extension_custom_live_overlay_supported
        ui._editor.set_text('parent draft')
        ui._editor._cursor = 6
        if child:
            ui._agents_command('start --inherit overlay child')
        else:
            ui._extension_command('ext-overlaymock-view', '')
        await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionCustom)
                   and ui._extension_dialog_panel._painted > 0)
        panel = ui._extension_dialog_panel
        session = server._orchestrator._runtimes['main']._active_session
        owner = session.children.extensions.owners[panel.dialog['child_id']] if child else session.extensions
        assert not panel.focused and ui._tui._focused is ui._editor
        ui._tui._dispatch_input(KeyEvent('!'))
        ui._tui._dispatch_paste('edited ')
        expected = 'parent!edited  draft'
        assert ui._editor.get_text() == expected
        sequence = 0
        async def control(action):
            nonlocal sequence
            assert await owner.command('ext-overlaymock-control', action) == 'QUEUED'
            await wait(lambda: panel._control_sequence > sequence)
            sequence = panel._control_sequence
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

        ui._handle_response({'type': 'extension_custom_control', 'id': panel.dialog['id'],
            'group_id': 'foreign', 'session_id': ui._gateway_session_id,
            'state': {'sequence': 999, 'hidden': True, 'capturing': False}})
        assert panel._control_sequence < 999
        await control('unfocus')
        await wait(lambda: panel._painted > 0 and not panel.focused)
        protected = Text('Host protected review')
        handle = ui._tui.show_overlay(protected)
        await control('focus')
        assert ui._tui._focused is protected and ui._custom_presentation.overlay_handle is None
        handle.hide()
        await wait(lambda: panel._painted > 0 and panel.focused)
        if cancel:
            await control('unfocus')
            await wait(lambda: panel._painted > 0 and not panel.focused)
            ui._tui._dispatch_input(KeyEvent('escape'))
        else:
            panel.handle_input(KeyEvent('enter'))
        if child:
            await wait(lambda: not session.children.supervisor.active and not ui._children_pending)
            result = await session.children.supervisor.control('wait', panel.dialog['child_id'])
            assert ('"value": null' if cancel else '"inputs": ["f2", "enter"]') in str(result['messages'])
            assert len(calls) == 2
        else:
            await wait(lambda: ui._extension_pending is None)
            assert ('"value": null' if cancel else '"inputs": ["f2", "enter"]') in '\n'.join(ui._chat_log.render(200))
            assert not calls
        assert ui._extension_dialog_panel is None and not ui._tui.has_overlay()
        assert ui._tui._focused is ui._editor and ui._editor.get_text() == expected
        assert ui._editor._cursor == 14
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('ending', ['disconnect', 'withdraw'])
@pytest.mark.parametrize('child', [False, True])
async def test_live_overlay_disconnect_or_specific_capability_withdrawal_cancels_owned_future(tmp_path, ending, child):
    calls = []
    server, config = await start(tmp_path, calls, plugin='overlaymock')
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        writer.write(b'{"type":"ping","extension_custom":1,"extension_custom_rich":1,'
                     b'"extension_custom_overlay":1,"extension_custom_live_overlay":1}\n')
        await writer.drain()
        await read_type(reader, 'pong')
        if child:
            request = {'type': 'child_control', 'request_id': 'start', 'group_id': 'main', 'session_id': '',
                       'action': 'start', 'text': 'custom child', 'access': 'inherit'}
        else:
            request = {'type': 'extension_control', 'request_id': 'view', 'group_id': 'main', 'session_id': '',
                       'action': 'command', 'name': 'ext-overlaymock-view', 'args': ''}
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        notice = await read_type(reader, 'child_extension_dialog' if child else 'extension_dialog')
        key = notice['dialog']['id']
        session = server._orchestrator._runtimes['main']._active_session
        if ending == 'disconnect':
            writer.close()
            await writer.wait_closed()
        else:
            writer.write(b'{"type":"ping","extension_custom_live_overlay":0}\n')
            await writer.drain()
        if child:
            result = await asyncio.wait_for(session.children.supervisor.control('wait', notice['child_id']), 6)
            assert result['status'] == 'completed' and '"value": null' in str(result['messages'])
            assert not session.children.extensions.contains(notice['child_id'], key, notice['child_owner'])
            assert len(calls) == 2
        else:
            if ending == 'withdraw':
                result = await read_type(reader, 'extension_result')
                assert result['success'] and json.loads(result['output'])['value'] is None
            async with asyncio.timeout(6):
                while session.extension_dialogs.contains(key):
                    await asyncio.sleep(0.005)
            assert not session.extension_dialogs.custom.views and not calls
        late_reader, late_writer = await asyncio.open_unix_connection(config.socket_path)
        try:
            late_writer.write((json.dumps({'type': 'extension_custom_request', 'id': key,
                'group_id': 'main', 'session_id': notice['session_id'], 'event': {}}) + '\n').encode())
            await late_writer.drain()
            assert not (await read_type(late_reader, 'extension_custom_ack'))['accepted']
        finally:
            late_writer.close()
            await late_writer.wait_closed()
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()




async def test_older_claw_viewer_static_overlay_support_cannot_downgrade_live_noncapture(tmp_path):
    calls = []
    server, config = await start(tmp_path, calls, plugin='overlaymock')
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        writer.write(b'{"type":"ping","extension_custom":1,"extension_custom_rich":1,'
                     b'"extension_custom_overlay":1}\n')
        await writer.drain()
        await read_type(reader, 'pong')
        writer.write(b'{"type":"extension_control","request_id":"old","group_id":"main",'
                     b'"session_id":"","action":"command","name":"ext-overlaymock-view","args":""}\n')
        await writer.drain()
        seen = []
        async with asyncio.timeout(6):
            while True:
                value = json.loads(await reader.readline())
                seen.append(value)
                if value['type'] == 'extension_result':
                    break
        assert value['success'] and json.loads(value['output'])['value'] is None
        assert not calls and not any(frame['type'] == 'extension_dialog' for frame in seen)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
