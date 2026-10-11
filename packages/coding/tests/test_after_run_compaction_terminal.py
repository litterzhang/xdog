"""Physical full review and parent recovery for original-run deferred compaction."""
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
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.session_manager import SessionManager

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current-Linux terminal required')


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('approve', [False, True])
def test_physical_after_run_compaction_full_review_then_canonical_pipeline(tmp_path, kind, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/aftercompact', root / 'aftercompact')
    history = []
    for index in range(6):
        history.extend([UserMessage(content=f'old history {index} ' + 'x' * 2000),
                        AssistantMessage(content=(TextContent(text='old answer'),))])
    terminal = Terminal(tmp_path)
    ready, stop = threading.Event(), threading.Event()
    calls, errors = [], []
    worker = None
    if kind == 'coding':
        home = tmp_path / 'home'
        store = SessionManager(home / 'sessions')
        data = store.create_session(working_dir=tmp_path, model='terminal-fixture')
        data.messages = history
        store.save_session(data)
        (home / 'settings.json').write_text(json.dumps({'extensions': ['aftercompact'], 'extension_dir': str(root)}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'socket'),
            pid_file=str(tmp_path / 'pid'), groups=(GroupDef(id='main', is_main=True),),
            extensions=('aftercompact',), extension_dir=str(root))
        def stream(model, context, options):
            calls.append(context)
            return EventStream.empty(AssistantMessage(content=(TextContent(text='AFTER-RUN-ANSWER'),)))
        def serve():
            async def run():
                server = GatewayServer(config)
                try:
                    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                               return_value=('offline/model', stream)):
                        await server.start()
                    runtime = server._orchestrator._runtimes['main']
                    runtime.flush_runner = None
                    session = runtime.get_or_create_session(allow_reset=False)
                    session.agent.replace_messages(history)
                    session._restored = True
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *(['--resume-id', data.session_id] if kind == 'coding' else [config.socket_path]))
        terminal.text('original parent run')
        terminal.keys('Enter')
        terminal.wait('Queue this exact compaction')
        if kind == 'coding':
            assert (tmp_path / 'fixture-model-count').read_text() == '1'
        else:
            assert len(calls) == 1
        if approve:
            terminal.keys('Right', 'Enter')
            terminal.wait('form review')
            terminal.resize(46, 14)
            terminal.resize(80, 24)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if 'Enter submits reviewed form' in terminal.visible():
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            terminal.keys('Enter')
            terminal.wait('Extension-requested compaction completed' if kind == 'coding' else 'Compacted:')
        else:
            terminal.keys('Escape')
            terminal.wait('ready' if kind == 'coding' else 'idle')
        if kind == 'coding':
            assert (tmp_path / 'fixture-model-count').read_text() == ('2' if approve else '1')
            assert (store.load_session(data.session_id).compaction is not None) is approve
        else:
            assert len(calls) == (2 if approve else 1)
        terminal.text('after compaction 草稿')
        terminal.wait_editor('after compaction 草稿')
        terminal.finish()
    finally:
        terminal.call('kill-server')
        if worker is not None:
            stop.set()
            worker.join(6)
            assert not worker.is_alive() and not errors
