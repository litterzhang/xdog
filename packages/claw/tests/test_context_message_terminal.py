"""Physical Linux Claw custom nextTurn review, independent of Coding's terminal."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Current Linux private terminal required')


@pytest.mark.parametrize('approve', [False, True])
def test_physical_claw_context_review_defers_model_until_explicit_user(tmp_path, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/contextrequest',
                    root / 'contextrequest')
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
        extensions=('contextrequest',), extension_dir=str(root))
    calls, errors, owned = [], [], {}
    ready, stop = threading.Event(), threading.Event()
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='CONTEXT-PHYSICAL-ANSWER'),)))
    def serve():
        async def run():
            gateway = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('test/model', stream)):
                    await gateway.start()
                owned['runtime'] = gateway._orchestrator._runtimes['main']
                owned['runtime'].get_or_create_session(allow_reset=False)
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(.01)
            except BaseException as exc:
                errors.append(exc)
                ready.set()
            finally:
                await gateway.stop()
        asyncio.run(run())
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    terminal = Terminal(tmp_path)
    try:
        assert ready.wait(8) and not errors
        terminal.start('claw', config.socket_path)
        terminal.text('/ext-contextrequest-request')
        terminal.keys('Enter')
        terminal.wait('Retain this exact next-turn context')
        assert not calls
        if approve:
            terminal.keys('Right', 'Enter')
            terminal.wait('form review')
            terminal.resize(60, 18)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if 'Enter submits reviewed form' in terminal.visible():
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            terminal.keys('Enter')
        else:
            terminal.keys('Escape')
        terminal.wait('CONTEXT-QUEUED')
        assert not calls
        terminal.text('NEXT EXPLICIT PHYSICAL USER')
        terminal.keys('Enter')
        terminal.wait('CONTEXT-PHYSICAL-ANSWER')
        assert len(calls) == 1 and ('RETAINED-CUSTOM-CONTENT' in str(calls[0].messages)) is approve
        assert 'PRIVATE-NOT-MODEL' not in str(calls[0].messages)
        runtime = owned['runtime']
        records = runtime.transcript_store.load_transcript(runtime.get_new_session_id())
        assert sum(row.get('role') == 'custom' for row in records) == int(approve)
        (tmp_path / 'canonical.json').write_text(json.dumps(records))
        terminal.text('parent editable')
        terminal.wait_editor('parent editable')
        terminal.finish()
    finally:
        terminal.call('kill-server')
        stop.set()
        thread.join(8)
        assert not thread.is_alive() and not errors
