"""Physical Linux both-host task review/withholding, private peers and terminal recovery."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time

import pytest

from packages.coding.tests.test_mcp_elicitation_terminal import reviewed
from packages.coding.tests.test_mcp_sampling_terminal import full_review
from packages.coding.tests.test_mcp_task_hosts import claw_setup, task_config
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('tmux'), reason='Owned Linux tmux required')


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('family', ['sampling', 'elicitation'])
@pytest.mark.parametrize('end', ['release', 'expire', 'peer_cancel'])
def test_physical_native_task_full_review_result_transport_resize_and_termios(tmp_path, kind, family, end):
    ready, stop = threading.Event(), threading.Event()
    owned, errors = {}, []
    thread = None
    terminal = Terminal(tmp_path)
    if kind == 'coding':
        configs, log = task_config(tmp_path, family)
        if end == 'expire':
            configs['owned']['args'].append('2000')
        elif end == 'peer_cancel':
            configs['owned']['args'].extend(['60000', 'cancel'])
        home = tmp_path / 'home'
        home.mkdir(mode=0o700)
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': configs}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        def serve():
            async def run():
                server = None
                try:
                    server, config, runtime, session, adapter, log = await claw_setup(tmp_path, family)
                    if end == 'expire':
                        from dataclasses import replace
                        original = runtime.mcp.configs['owned']
                        runtime.mcp.configs['owned'] = replace(original, command=(*original.command, '2000'))
                    elif end == 'peer_cancel':
                        from dataclasses import replace
                        original = runtime.mcp.configs['owned']
                        runtime.mcp.configs['owned'] = replace(original,
                            command=(*original.command, '60000', 'cancel'))
                    owned.update(config=config, runtime=runtime, session=session, adapter=adapter, log=log)
                    ready.set()
                    while not stop.is_set():
                        await asyncio.sleep(.01)
                except BaseException as exc:
                    errors.append(exc)
                    ready.set()
                finally:
                    if server is not None:
                        await server.stop()
            asyncio.run(run())
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        assert ready.wait(6) and not errors
        log = owned['log']
    def frames():
        if not log.exists():
            return []  # Connection startup has not written its first frame yet.
        return [json.loads(raw) for raw in log.read_text().splitlines()]
    def no_model():
        if kind == 'coding':
            assert not (tmp_path / 'fixture-model-count').exists()
        else:
            assert not owned['adapter'].calls
    try:
        terminal.start(kind, *([owned['config'].socket_path] if kind == 'claw' else []))
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('owned · disconnected')
        terminal.text('j' if family == 'sampling' else 'k')
        terminal.wait('tasks armed')
        assert not log.exists()
        no_model()
        terminal.text('c')
        if end != 'peer_cancel':
            terminal.wait('Sampling input review' if family == 'sampling' else 'Elicitation request')
        no_model()
        deadline = time.monotonic() + 5
        while not any(row.get('id') == 'create' for row in frames()) and time.monotonic() < deadline:
            time.sleep(.03)
        created = next(row for row in frames() if row.get('id') == 'create')['result']['task']
        assert 'PRIVATE' not in str(created) and not any(row.get('id') == 'retrieve' for row in frames())
        initial = next(row for row in frames() if row.get('method') == 'initialize')
        assert initial['params']['capabilities']['tasks']['requests'] == {
            family: {'createMessage' if family == 'sampling' else 'create': {}}}
        if end in {'expire', 'peer_cancel'}:
            # No operator review/approval or registry polling drives expiry.
            terminal.wait('Sampling request ended' if family == 'sampling' else 'Elicitation ended')
            no_model()
            deadline = time.monotonic() + 5
            while not any(row.get('id') == 'retrieve' for row in frames()) and time.monotonic() < deadline:
                time.sleep(.03)
            result = next(row for row in frames() if row.get('id') == 'retrieve')
            assert result['error']['code'] == -32000 and 'result' not in result
            if end == 'peer_cancel':
                deadline = time.monotonic() + 5
                while not any(row.get('id') == 'peer-cancel' for row in frames()) and time.monotonic() < deadline:
                    time.sleep(.03)
                cancelled = next(row for row in frames() if row.get('id') == 'peer-cancel')
                assert cancelled['result']['status'] == 'cancelled'
                assert cancelled['result']['taskId'] == created['taskId']
            terminal.keys('Escape')
            terminal.wait_hidden('MCP servers')
            terminal.wait_cursor(True)
            terminal.text('PARENT AFTER TASK EXPIRED')
            terminal.wait_editor('PARENT AFTER TASK EXPIRED')
            terminal.finish()
            no_model()
            return
        if family == 'sampling':
            terminal.keys('C-a', 'C-k')
            terminal.text(json.dumps({'model': 'terminal-fixture' if kind == 'coding' else 'explicit-model',
                                     'max_tokens': 10, 'context': 'none', 'tools': []}))
            terminal.keys('Enter')
            terminal.wait('Approve exact request')
            terminal.resize(60, 18)
            full_review(terminal, 'input')
            no_model()
            terminal.keys('Enter')
            terminal.wait('Sampling output review')
            full_review(terminal, 'output')
        else:
            terminal.resize(60, 18)
            reviewed(terminal)
            terminal.keys('Enter')
            terminal.wait('Elicitation form')
            terminal.keys('C-a', 'C-k')
            terminal.text('{"name":"PRIVATE PHYSICAL TASK FORM"}')
            terminal.keys('Enter')
            terminal.wait('Elicitation reply')
            reviewed(terminal)
            no_model()
            terminal.keys('Enter')
            terminal.wait('Elicitation response')
            reviewed(terminal)
            no_model()
        assert not any(row.get('id') == 'retrieve' for row in frames())
        terminal.keys('Enter')
        terminal.wait('Sampling request ended' if family == 'sampling' else 'Elicitation ended')
        deadline = time.monotonic() + 5
        while not any(row.get('id') == 'retrieve' for row in frames()) and time.monotonic() < deadline:
            time.sleep(.03)
        returned = next(row for row in frames() if row.get('id') == 'retrieve')['result']
        assert returned['_meta']['io.modelcontextprotocol/related-task'] == {'taskId': created['taskId']}
        if family == 'sampling':
            assert returned['content']['text'] == ('FIXTURE-ANSWER' if kind == 'coding' else 'MODEL OUTPUT')
        else:
            assert returned['content'] == {'name': 'PRIVATE PHYSICAL TASK FORM'}
        terminal.keys('Escape')
        terminal.wait_hidden('MCP servers')
        terminal.wait_cursor(True)
        terminal.text('PARENT AFTER TASK REVIEW')
        terminal.wait_editor('PARENT AFTER TASK REVIEW')
        terminal.finish()
        if family == 'sampling':
            if kind == 'coding':
                assert (tmp_path / 'fixture-model-count').read_text() == '1'
            else:
                assert len(owned['adapter'].calls) == 1 and not owned['session'].agent.messages
        else:
            no_model()
    finally:
        terminal.call('kill-server')
        stop.set()
        if thread is not None:
            thread.join(6)
            assert not thread.is_alive() and not errors
