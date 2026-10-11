"""Physical reviewed extension compaction on both current-Linux native hosts."""
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
from xdog.claw.core.persistence.transcript_convert import messages_to_transcript
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.session_manager import SessionManager

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current-Linux terminal required')


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('approve', [False, True])
def test_physical_review_precedes_compaction_and_restores_parent_terminal(tmp_path, kind, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/compactionrequest',
                    root / 'compactionrequest')
    messages = []
    for index in range(6):
        messages.extend([UserMessage(content=f'old work {index} ' + 'x' * 2000),
                         AssistantMessage(content=(TextContent(text=f'old answer {index}'),))])
    terminal = Terminal(tmp_path)
    calls, errors, owned = [], [], {}
    ready, stop = threading.Event(), threading.Event()
    worker = None
    if kind == 'coding':
        home = tmp_path / 'home'
        store = SessionManager(home / 'sessions')
        data = store.create_session(working_dir=tmp_path, model='terminal-fixture')
        data.messages = messages
        store.save_session(data)
        settings = {'extensions': ['compactionrequest'], 'extension_dir': str(root)}
        (home / 'settings.json').write_text(json.dumps(settings))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
            extensions=('compactionrequest',), extension_dir=str(root))
        def stream(model, context, options):
            calls.append(context)
            return EventStream.empty(AssistantMessage(content=(TextContent(text='PHYSICAL-REVIEWED-SUMMARY'),)))
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
                    session.agent.replace_messages(messages)
                    session._restored = True
                    session._persisted_count = len(messages)
                    session._store.replace_transcript(session.meta.session_id, messages_to_transcript(messages))
                    owned['session'] = session
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
    try:
        if kind == 'coding':
            terminal.start(kind, '--resume-id', data.session_id, '--model', 'terminal-fixture',
                           '--permission-mode', 'deny')
        else:
            terminal.start(kind, config.socket_path)
        terminal.resize(100, 30)
        if kind == 'coding':
            terminal.wait('ready |')
        terminal.text('/ext-compactionrequest-request {"instructions":"cancel"}')
        terminal.keys('Enter')
        terminal.wait('Queue this exact compaction')
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
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
            assert not calls and not (tmp_path / 'fixture-model-count').exists()
            terminal.keys('Enter')
            terminal.wait('Extension-requested compaction completed' if kind == 'coding' else 'Compacted:')
            if kind == 'coding':
                saved = store.load_session(data.session_id)
                assert saved.compaction['recovery_branch_id'] and len(saved.messages) < len(messages)
                assert (tmp_path / 'fixture-model-count').read_text() == '1'
            else:
                assert len(calls) == 1 and not calls[0].tools
                assert owned['session']._compaction_metadata['recovery_branch_id']
        else:
            terminal.keys('Escape')
            terminal.wait('COMPACTION-QUEUED')
            assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.wait_cursor(True)
        terminal.text('UNSENT AFTER COMPACTION REVIEW')
        terminal.wait_editor('UNSENT AFTER COMPACTION REVIEW')
        terminal.finish()
    finally:
        terminal.call('kill-server')
        if worker is not None:
            stop.set()
            worker.join(6)
            assert not worker.is_alive() and not errors
