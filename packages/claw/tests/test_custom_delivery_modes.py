"""Real staged Claw native custom delivery and canonical continuation receipts."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.claw.tests.test_context_messages import answer, close, wait


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('mode', ['idle', 'trigger', 'active', 'legacy', 'callback', 'resumed'])
async def test_claw_original_custom_delivery_uses_no_user_substitute(tmp_path, delivery, mode):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/customdelivery',
                    root / 'customdelivery')
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if mode == 'active' and len(calls) == 1:
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_customdelivery_'))
            result = AssistantMessage(content=(ToolCall(id='custom', name=tool.name,
                arguments={'delivery': delivery, 'trigger': True}),))
        else:
            result = AssistantMessage(content=(TextContent(text='CLAW-CUSTOM-DONE'),))
        return EventStream.empty(result)
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
        extensions=('customdelivery',), extension_dir=str(root), extension_allow_tools=('customdelivery',))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    try:
        await wait(ui, lambda: ui._connection_ready)
        ui._editor.set_text('RETAINED PRIVATE DRAFT')
        if mode == 'legacy':
            previous = ui._extension_context_messages_generation
            ui._queue_request({'type': 'ping', 'extension_context_delivery_modes': 0})
            await wait(ui, lambda: ui._extension_context_messages_generation != previous)
        if mode == 'resumed':
            await session.context_messages.persist((UserMessage(content='OLD PERSISTED USER'),
                AssistantMessage(content=(TextContent(text='OLD ASSISTANT'),))))
            session.agent.replace_messages([])
            session._restored = False
        if mode == 'active':
            ui._handle_submit('ORIGINAL USER')
        else:
            ui._extension_command('ext-customdelivery-request', json.dumps(
                {'delivery': delivery, 'trigger': mode == 'trigger', 'fail': mode == 'callback'}))
        if mode == 'legacy':
            await wait(ui, lambda: ui._extension_pending is None)
            assert ui._extension_dialog_panel is None and not calls and not session.agent.messages
            return
        await wait(ui, lambda: ui._extension_dialog_panel is not None)
        assert len(calls) == int(mode == 'active')
        answer(ui, True)
        if mode == 'callback':
            await wait(ui, lambda: ui._extension_pending is None)
            assert not calls and not session.agent.messages
            return
        if mode in {'idle', 'resumed'}:
            await wait(ui, lambda: ui._extension_pending is None)
            async with asyncio.timeout(5):
                while not any(isinstance(item, CustomAgentMessage) for item in session.agent.messages):
                    ui._poll()
                    await asyncio.sleep(.003)
        else:
            await wait(ui, lambda: len(calls) >= (2 if mode == 'active' else 1)
                       and ui._active_run_id is None and not ui._waiting and ui._extension_pending is None)
        assert sum(isinstance(item, CustomAgentMessage) for item in session.agent.messages) == 1
        assert sum(isinstance(item, UserMessage) for item in session.agent.messages) == int(
            mode in {'active', 'resumed'})
        assert not (await session.context_messages.capture())[1]
        assert 'NEVER-MODEL-METADATA' not in str(calls)
        if mode not in {'idle', 'resumed'}:
            assert 'CANONICAL-CUSTOM-DELIVERY' in str(calls[-1].messages)
        else:
            assert not calls
        assert ui._editor.get_text() == 'RETAINED PRIVATE DRAFT'
        disk = runtime.transcript_store.load_transcript(session.meta.session_id)
        assert sum(row.get('role') == 'custom' for row in disk) == 1
        assert sum(row.get('role') == 'user' for row in disk) == int(mode in {'active', 'resumed'})
        if mode == 'resumed':
            assert 'OLD PERSISTED USER' in str(disk) and 'OLD ASSISTANT' in str(disk)
    finally:
        await close(server, ui, task, writer)


@pytest.fixture(autouse=True)
def custom_admission_uses_original_agent_loop(monkeypatch):
    from xdog.agent import Agent
    original = Agent.queue_custom_message
    def checked(agent, *args, **kwargs):
        assert asyncio.get_running_loop() is agent._cancel_loop
        return original(agent, *args, **kwargs)
    monkeypatch.setattr(Agent, 'queue_custom_message', checked)
