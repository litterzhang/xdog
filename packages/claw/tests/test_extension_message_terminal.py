"""Owned current-Linux physical Claw review, model delivery and terminal restoration."""
from __future__ import annotations

import asyncio
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
                               reason='Owned current-Linux terminal required')


@pytest.mark.parametrize('approve', [False, True])
def test_physical_claw_requested_message_requires_whole_review_and_restores_terminal(tmp_path, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/messagerequest',
                    root / 'messagerequest')
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('messagerequest',), extension_dir=str(root))
    calls, errors, owned = [], [], {}
    ready, stop = threading.Event(), threading.Event()
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='PHYSICAL-MESSAGE-DELIVERED'),)))
    def serve():
        async def run():
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('offline/model', stream)):
                    await server.start()
                runtime = server._orchestrator._runtimes['main']
                owned['session'] = runtime.get_or_create_session(allow_reset=False)
                owned['runtime'] = runtime
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(.01)
            except BaseException as exc:
                errors.append(exc)
                ready.set()
            finally:
                await server.stop()
        asyncio.run(run())
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    assert ready.wait(6) and not errors
    terminal = Terminal(tmp_path)
    try:
        terminal.start('claw', config.socket_path)
        terminal.resize(100, 30)
        terminal.text('/ext-messagerequest-request {"text":"/quit"}')
        terminal.keys('Enter')
        terminal.wait('Send this extension message')
        assert not calls
        if approve:
            terminal.keys('Right', 'Enter')
            terminal.wait('form review')
            terminal.resize(60, 18)
            reviewed = ''
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                frame = terminal.visible()
                reviewed += frame
                if 'Enter submits reviewed form' in frame:
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            assert 'messagerequest' in reviewed and '/quit' in reviewed and not calls
            terminal.keys('Enter')
            terminal.wait('PHYSICAL-MESSAGE-DELIVERED')
            assert len(calls) == 1
            model_text = str(calls[0].messages)
            assert 'User-approved message requested by extension messagerequest' in model_text
            assert '/quit' in model_text
            session = owned['session']
            saved = session._store.load_transcript(session.meta.session_id)
            assert 'requested by extension messagerequest' in str(saved)
            assert session.meta.session_id == owned['runtime'].get_new_session_id()
        else:
            terminal.keys('Escape')
            terminal.wait('MESSAGE-QUEUED')
            assert not calls and not owned['session'].agent.messages
        terminal.wait_cursor(True)
        terminal.text('UNSENT AFTER MESSAGE REVIEW')
        terminal.wait_editor('UNSENT AFTER MESSAGE REVIEW')
        terminal.finish()
    finally:
        terminal.call('kill-server')
        stop.set()
        worker.join(6)
        assert not worker.is_alive() and not errors
