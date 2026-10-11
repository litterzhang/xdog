"""Physical native Claw parent/child write-only reviews; no accidental prompt submission."""
from __future__ import annotations

import asyncio
import shutil
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current-Linux terminal required')


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('approve', [False, True])
def test_physical_claw_editor_write_review_preserves_original_parent_or_child_owner(tmp_path, child, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/editorwrite', root / 'editorwrite')
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('editorwrite',), extension_dir=str(root), extension_allow_tools=('editorwrite',))
    calls, errors, owned = [], [], {}
    ready, stop, model_release = threading.Event(), threading.Event(), threading.Event()
    def stream(model, context, options):
        calls.append(context)
        if len(calls) != 1:
            return EventStream.empty(AssistantMessage(content=(TextContent(text='PHYSICAL-CHILD-DONE'),)))
        tool = next(tool.name for tool in context.tools if tool.name.startswith('ext_editorwrite_'))
        result = AssistantMessage(content=(ToolCall(id='physical-write', name=tool, arguments={}),),
                                  stop_reason='toolUse')
        async def generate():
            yield StartEvent(partial=AssistantMessage())
            while not model_release.is_set():
                await asyncio.sleep(.01)
            yield DoneEvent(message=result)
        future = asyncio.get_running_loop().create_future()
        future.set_result(result)
        return EventStream.from_async_generator(generate(), result_future=future)
    def serve():
        async def run():
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('offline/model', stream)):
                    await server.start()
                owned['session'] = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
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
        if child:
            terminal.text('/agents start --inherit physical-child')
            terminal.keys('Enter')
            terminal.wait('started')
            terminal.text('/agents')
            terminal.keys('Enter')
            terminal.wait('Child agents')
            terminal.wait('[running]')
            terminal.text('s')
            terminal.wait('Child steer')
            terminal.text('PRIVATE CHILD DRAFT')
            terminal.wait_editor('PRIVATE CHILD DRAFT')
            model_release.set()
        else:
            terminal.text('/ext-editorwrite-write')
            terminal.keys('Enter')
        terminal.wait('Replace this owner')
        assert len(calls) == int(child) and not owned['session'].agent.messages
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
            assert 'PROPOSED' in reviewed and 'unsent' in reviewed and len(calls) == int(child)
            terminal.keys('Enter')
            terminal.wait('PROPOSED')
            terminal.wait_editor('unsent')
        else:
            terminal.keys('Escape')
            terminal.wait('Child steer' if child else 'EDITOR-WRITE-False')
            if child:
                terminal.wait_editor('PRIVATE CHILD DRAFT')
        assert not owned['session'].agent.messages
        if child:
            deadline = time.monotonic() + 8
            while len(calls) != 2 and time.monotonic() < deadline:
                time.sleep(.01)
            assert len(calls) == 2 and 'PROPOSED' not in str(calls[-1].messages)
            terminal.keys('Escape')
            terminal.wait('Child agents')
            terminal.keys('Escape')
        else:
            assert not calls
        terminal.wait_cursor(True)
        terminal.finish()
    finally:
        terminal.call('kill-server')
        model_release.set()
        stop.set()
        worker.join(12)
        assert not worker.is_alive() and not errors
