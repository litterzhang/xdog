"""Physical parent/child Claw queue reviews show exact image descriptors and never parse /quit."""
from __future__ import annotations

import asyncio
import shutil
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current-Linux terminal required')


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('approve', [False, True])
def test_physical_claw_queue_image_review_original_parent_or_child_loop(tmp_path, child, delivery, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/queuedhost', root / 'queuedhost')
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('queuedhost',), extension_dir=str(root), extension_allow_tools=('queuedhost',))
    calls, errors, owned = [], [], {}
    ready, stop = threading.Event(), threading.Event()
    info = Model(id='offline/model', input=('text', 'image'), context_window=128000, max_tokens=4096)
    def stream(model, context, options):
        calls.append(context)
        result = (AssistantMessage(content=(ToolCall(id='queue-physical', name=next(tool.name for tool in context.tools
                    if tool.name.startswith('ext_queuedhost_request')),
                    arguments={'delivery': delivery, 'image': True}),), stop_reason='toolUse')
                  if len(calls) == 1 else AssistantMessage(content=(TextContent(text='PHYSICAL-QUEUE-DONE'),)))
        return EventStream.empty(result)
    stream.resolve_model = lambda name: info if name == 'offline/model' else None
    def serve():
        async def run():
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('offline/model', stream)):
                    await server.start()
                runtime = server._orchestrator._runtimes['main']
                runtime.image_model_info = lambda: info
                owned['session'] = runtime.get_or_create_session(allow_reset=False)
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(.01)
            except BaseException as exc:
                errors.append(exc)
                ready.set()
            finally:
                await asyncio.wait_for(server.stop(), 10)
        asyncio.run(run())
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    assert ready.wait(6) and not errors
    terminal = Terminal(tmp_path)
    try:
        terminal.start('claw', config.socket_path)
        terminal.resize(100, 30)
        terminal.text('/agents start --inherit --model offline/model child queue' if child else 'original parent turn')
        terminal.keys('Enter')
        terminal.wait('Queue this exact extension input')
        assert len(calls) == 1
        if approve:
            terminal.keys('Right', 'Enter')
            terminal.wait('form review')
            terminal.resize(60, 18)
            deadline = time.monotonic() + 10
            reviewed = ''
            while time.monotonic() < deadline:
                frame = terminal.visible()
                reviewed += frame
                if 'Enter submits reviewed form' in frame:
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            assert 'REVIEWED-QUEUE /quit' in reviewed and 'image/png' in reviewed and 'sha256' in reviewed
            assert len(calls) == 1
            terminal.keys('Enter')
        else:
            terminal.keys('Escape')
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            session = owned['session']
            if len(calls) >= 2 and not session.agent.is_streaming and not session.children.supervisor.active:
                break
            time.sleep(.02)
        else:
            raise AssertionError(terminal.visible())
        actual = [msg for context in calls[1:] for msg in context.messages
                  if msg.role == 'user' and 'REVIEWED-QUEUE' in str(msg.content)]
        assert bool(actual) is approve
        if approve:
            assert 'requested by extension queuedhost' in str(actual[-1].content)
            assert 'mime_type=' in str(actual[-1].content)
        if child:
            assert not session.agent.messages
        terminal.wait_cursor(True)
        terminal.text('UNSENT AFTER QUEUE REVIEW')
        terminal.wait_editor('UNSENT AFTER QUEUE REVIEW')
        terminal.finish()
    finally:
        terminal.call('kill-server')
        stop.set()
        worker.join(12)
        assert not worker.is_alive() and not errors
