"""Physical Linux retired-key refusal and ordinary MCP preview in both native hosts."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.agent.tests.mcp_helpers import Adapter
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(sys.platform != 'linux' or not shutil.which('tmux'), reason='Owned Linux tmux required')
PEER = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_completion_peer.py'


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('retired_keys', [('f', 'k'), ('o', 'M-f')], ids=['form-task', 'roots-off'])
def test_physical_retired_keys_preserve_mcp_prompt_focus_resize_and_termios(tmp_path, kind, retired_keys):
    log = tmp_path / 'mcp-frames'
    configs = {'owned': {'command': sys.executable, 'args': [str(PEER), 'modern', str(log)]}}
    ready, stop = threading.Event(), threading.Event()
    owned, errors = {}, []
    thread = None
    terminal = Terminal(tmp_path)

    def frames():
        return [json.loads(raw) for raw in log.read_text().splitlines()] if log.exists() else []

    def no_model():
        if kind == 'coding':
            assert not (tmp_path / 'fixture-model-count').exists()
        else:
            assert not owned['adapter'].calls and not owned['session'].agent.messages

    try:
        if kind == 'coding':
            home = tmp_path / 'home'
            home.mkdir(mode=0o700)
            (home / 'settings.json').write_text(json.dumps({'mcp_servers': configs}))
            terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
        else:
            def serve():
                async def run():
                    server = None
                    try:
                        adapter = Adapter()
                        config = ClawConfig(model='explicit-model', data_dir=str(tmp_path / 'data'),
                            socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
                            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
                            mcp_servers=configs)
                        server = GatewayServer(config)
                        with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                                   return_value=('explicit-model', adapter)):
                            await server.start()
                            runtime = server._orchestrator._runtimes['main']
                            session = runtime.get_or_create_session(allow_reset=False)
                        owned.update(config=config, runtime=runtime, session=session, adapter=adapter)
                        ready.set()
                        while not stop.is_set():
                            await asyncio.sleep(.01)
                    except BaseException as exc:
                        errors.append(exc)
                        ready.set()
                    finally:
                        if server is not None:
                            try:
                                await server.stop()
                            except BaseException as exc:
                                errors.append(exc)
                asyncio.run(run())
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            assert ready.wait(6) and not errors

        terminal.start(kind, *([owned['config'].socket_path] if kind == 'claw' else []))
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('owned · disconnected')
        terminal.wait_cursor(False)
        for key in retired_keys:
            terminal.keys(key) if key.startswith('M-') else terminal.text(key)
        terminal.wait('owned · disconnected')
        assert not log.exists()  # Removed controls cannot start a peer, model or review.
        no_model()

        terminal.text('c')
        terminal.wait('owned · connected')
        terminal.wait_cursor(False)
        no_model()
        if kind == 'claw':
            owned['process'] = owned['runtime'].mcp._clients['owned'].process
        assert [row['method'] for row in frames()] == ['server/discover']
        terminal.text('p')
        terminal.wait('▶ review')  # Wait for the painted catalog, not 'Enter preview' in the hint.
        terminal.keys('Enter')
        terminal.wait('MCP arguments')
        terminal.wait_cursor(True)
        topic = 'PHYSICAL-MCP-PREVIEW'
        terminal.keys('C-a', 'C-k')
        terminal.text(json.dumps({'topic': topic}))
        terminal.keys('Enter')
        terminal.wait('PREVIEW-' + topic)
        terminal.wait_cursor(False)
        no_model()

        requests = frames()
        assert all(row.get('method') in {'server/discover', 'prompts/list', 'prompts/get'} for row in requests)
        retrieved = [row for row in requests if row.get('method') == 'prompts/get']
        assert len(retrieved) == 1 and retrieved[0]['params']['arguments'] == {'topic': topic}
        assert all(not {'sampling', 'elicitation', 'tasks'} & row.get('params', {}).get('_meta', {}).get(
            'io.modelcontextprotocol/clientCapabilities', {}).keys() for row in requests)

        terminal.resize(60, 18)
        terminal.wait('PREVIEW-' + topic)
        terminal.wait_cursor(False)
        terminal.keys('Escape')
        terminal.wait('MCP servers')
        terminal.keys('Escape')
        terminal.wait_hidden('MCP servers')
        terminal.wait_cursor(True)
        terminal.text('PARENT AFTER MCP')
        terminal.wait_editor('PARENT AFTER MCP')
        _, cursor_y = map(int, terminal.call('display-message', '-p', '-t', 'test',
                                            '#{cursor_x} #{cursor_y}').split())
        assert terminal.visible().splitlines()[cursor_y] == '> PARENT AFTER MCP'
        no_model()  # Preview retrieval never inserts/sends untrusted data automatically.
        terminal.finish()
        no_model()
    finally:
        try:
            terminal.call('kill-server')
        finally:
            stop.set()
            if thread is not None:
                thread.join(6)
                assert not thread.is_alive() and not errors
                if 'process' in owned:
                    assert owned['process'].returncode is not None
