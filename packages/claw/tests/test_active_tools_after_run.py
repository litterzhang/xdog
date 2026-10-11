"""Real Claw socket/worker/producer cleanup and idle durable active-tool selection."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent, KeyEventType


@pytest.mark.parametrize('ending', ['apply', 'deny', 'withdraw', 'producer-error', 'callback-error',
                                    'isError', 'inventory', 'storage'])
async def test_claw_selection_waits_for_actual_parent_producer_then_durable_cas(tmp_path, ending, monkeypatch):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/aftertools', root / 'aftertools')
    calls = []
    def stream(model, context, options):
        calls.append(context)
        message = (AssistantMessage(content=(ToolCall(id='select', name=next(tool.name for tool in context.tools
            if tool.name.startswith('ext_aftertools_select')), arguments={'names': ['current_time'],
                'fail': ending == 'callback-error', 'error': ending == 'isError'}),))
            if len(calls) == 1 else AssistantMessage(content=(TextContent(text='ANSWER'),)))
        return EventStream.empty(message)
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('aftertools',),
        extension_allow_tools=('aftertools',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    held, release = asyncio.Event(), asyncio.Event()
    original_event = session.extensions.event
    async def event(name, *args, **kwargs):
        await original_event(name, *args, **kwargs)
        if name == 'turn_end':
            held.set()
            await release.wait()
            if ending == 'producer-error':
                raise ValueError('Original producer finalization failed')
    session.extensions.event = event
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._extension_tools_after_run_supported
        ui._queue_request({'type': 'ping', 'extension_compaction_after_run': 0, 'extension_active_tools': 0})
        await wait(lambda: not ui._extension_compaction_after_run_supported
                   and not ui._extension_active_tools_supported)
        ui._editor.set_text('PRIVATE DRAFT')
        ui._handle_submit('original tool run')
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['operation'] == 'select_tools' and panel.dialog['after_run'] is True
        assert len(calls) == 1 and session.agent.active_tool_selection is None
        if ending != 'deny':
            panel.handle_input(KeyEvent('right'))
        panel.handle_input(KeyEvent('enter'))
        for _ in range(200):
            panel.render(100)
            if panel.form._visible:
                break
            panel.handle_input(KeyEvent('pagedown'))
        assert panel.form._visible
        panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
        assert session.agent.active_tool_selection is None
        panel.handle_input(KeyEvent('enter'))
        await asyncio.wait_for(held.wait(), 8)
        assert session.agent.active_tool_selection is None  # Agent idle is not outer producer settlement.
        if ending == 'withdraw':
            ui._queue_request({'type': 'ping', 'extension_active_tools_after_run': 0})
            await wait(lambda: not ui._extension_tools_after_run_supported)
        elif ending == 'inventory':
            session.agent.set_tools([tool for tool in session.agent.state.tools if tool.name != 'current_time'])
        elif ending == 'storage':
            def fail(*args, **kwargs):
                raise OSError('Owned active-tool durable save failed')
            monkeypatch.setattr(runtime.transcript_store, 'save_active_tools', fail)
        release.set()
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        if ending == 'apply':
            await wait(lambda: session.agent.active_tool_selection == ('current_time',))
            assert runtime.transcript_store.get_active_tools(session.meta.session_id) == ('current_time',)
            await wait(lambda: 'Extension tool selection: applied' in '\n'.join(ui._chat_log.render(140)))
        else:
            await asyncio.sleep(.1)
            ui._poll()
            assert session.agent.active_tool_selection is None
        assert len(calls) == (1 if ending == 'callback-error' else 2)
        assert not session.children.supervisor.active
    finally:
        release.set()
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
