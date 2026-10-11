"""Current Linux native terminals paint modern SDK browser continuations end to end."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
from unittest.mock import patch

import pytest
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.agent.tests.test_mcp_sampling_executor import Adapter
from packages.coding.tests.test_mcp_elicitation_terminal import reviewed
from packages.coding.tests.test_mcp_modern_browser_ui import NODE, PEER, SDK_ROOT, records
from packages.coding.tests.test_mcp_sampling_terminal import full_review
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux')
                               or not SDK_ROOT or not NODE,
                               reason='Current Linux tmux and explicit pinned independent SDK 2.3.1 required')


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('method', ['resources/read', 'prompts/get'])
@pytest.mark.parametrize('family', ['roots', 'sampling', 'form', 'url'])
def test_linux_modern_browser_full_reviews_final_document_resize_and_parent_cursor(
    tmp_path, host, method, family, *, preselect_owner=True, first_arm='b',
):
    log = tmp_path / 'physical-modern.jsonl'
    servers = {'modern': {'command': NODE, 'args': [str(PEER), 'stdio', str(log)], 'timeout': 45,
                          'roots': [{'uri': 'file:///explicit', 'name': 'Explicit'}]}}
    terminal = Terminal(tmp_path)
    terminal.child_env['XDOG_MCP_OFFICIAL_SDK_ROOT'] = SDK_ROOT
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    ready, stop = threading.Event(), threading.Event()
    errors, owned = [], {}
    thread = None
    adapter = Adapter()
    config = None
    if host == 'coding':
        home = tmp_path / 'home'
        home.mkdir(mode=0o700)
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': servers}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        config = ClawConfig(model='explicit-model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),), mcp_servers=servers)
        def serve():
            async def run():
                gateway = GatewayServer(config)
                try:
                    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                               return_value=('explicit-model', adapter)):
                        await gateway.start()
                    owned['runtime'] = gateway._orchestrator._runtimes['main']
                    # Select the original idle owner before connecting, matching
                    # the existing sampling gateway fixture (not a cold-start test).
                    if preselect_owner:
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

    def model_calls():
        count = tmp_path / 'fixture-model-count'
        return (int(count.read_text()) if count.exists() else 0) if host == 'coding' else len(adapter.calls)

    def operations():
        return [row for row in records(log) if row['type'] == 'operation']

    try:
        if thread is not None:
            assert ready.wait(8) and not errors
        terminal.start(host, *([config.socket_path] if config is not None else []))
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('modern · disconnected')
        for key in (first_arm, 'f' if first_arm == 'b' else 'b'):
            terminal.text(key)
            terminal.wait('Sampling armed' if key == 'b' else 'Elicitation armed')
        assert not records(log) and model_calls() == 0
        terminal.text('c')
        terminal.wait('modern · connected')
        terminal.text('u' if method == 'resources/read' else 'p')
        terminal.wait('MCP catalog')
        name = 'test://' + family if method == 'resources/read' else family
        terminal.text('/')
        terminal.text(name)
        terminal.keys('Enter')  # Commit literal list filter before selecting the entry.
        terminal.wait(name)
        terminal.keys('Enter')
        if method == 'prompts/get':
            terminal.wait('MCP arguments')
            terminal.keys('Enter')  # Explicit original prompt/get with an empty named-argument object.
        if family == 'sampling':
            terminal.wait('Sampling input review')
            assert len(operations()) == 1 and model_calls() == 0
            terminal.keys('C-a', 'C-k')
            terminal.text(json.dumps({'model': 'terminal-fixture' if host == 'coding' else 'explicit-model',
                                      'max_tokens': 10, 'context': 'none', 'tools': []}))
            terminal.keys('Enter')
            terminal.wait('Approve exact request')
            terminal.resize(60, 18)
            full_review(terminal, 'input')
            assert len(operations()) == 1 and model_calls() == 0
            terminal.keys('Enter')
            terminal.wait('Sampling output review')
            assert len(operations()) == 1 and model_calls() == 1
            full_review(terminal, 'output')
            terminal.keys('Enter')
        elif family in {'form', 'url'}:
            terminal.wait('Elicitation request')
            assert len(operations()) == 1 and model_calls() == 0
            terminal.resize(60, 18)
            reviewed(terminal)
            assert len(operations()) == 1
            terminal.keys('Enter')  # URL acknowledgment, not implicit browser navigation.
            if family == 'form':
                terminal.wait('Elicitation form')
                terminal.keys('C-a', 'C-k')
                terminal.text('{"name":"PHYSICALLY REVIEWED MODERN"}')
                terminal.keys('Enter')
                terminal.wait('Elicitation reply')
                reviewed(terminal)
                assert len(operations()) == 1
                terminal.keys('Enter')
            terminal.wait('Elicitation response')
            reviewed(terminal)
            assert len(operations()) == 1 and model_calls() == 0
            terminal.keys('Enter')
        terminal.wait('APPROVED-' + family)
        if family == 'roots':
            terminal.resize(60, 18)  # Other families already shrank during their review.
            terminal.wait('APPROVED-' + family)
        terminal.resize(80, 24)
        terminal.wait('APPROVED-' + family)
        assert len(operations()) == 2
        requests = [row['message'] for row in records(log) if row['type'] == 'inbound'
                    and row['message'].get('method') == method]
        assert len(requests) == 2 and requests[0]['id'] != requests[1]['id']
        assert requests[1]['params']['requestState'] == '  opaque\x00state/汉字\n  '
        reply = requests[1]['params']['inputResponses']['review']
        if family == 'form':
            assert reply == {'action': 'accept', 'content': {'name': 'PHYSICALLY REVIEWED MODERN'}}
        elif family == 'url':
            assert reply == {'action': 'accept'}
            offers = [row['message'] for row in records(log) if row['type'] == 'outbound'
                      and row['message'].get('result', {}).get('inputRequests')]
            assert offers and 'elicitationId' not in str(offers)
            assert 'Peer reports external interaction complete' not in terminal.pane()
        assert not any(row['type'] == 'inbound' and row['message'].get('method') == 'tools/call'
                       for row in records(log))
        terminal.keys('Escape')
        terminal.wait('MCP servers')
        terminal.keys('Escape')
        terminal.wait_hidden('MCP servers')
        terminal.wait_cursor(True)
        terminal.text('PARENT MODERN RECOVERED 界')
        terminal.wait_editor('PARENT MODERN RECOVERED 界')
        terminal.finish()
        assert model_calls() == (1 if family == 'sampling' else 0)
        if host == 'claw':
            session = owned['runtime']._active_session
            assert not session.agent.messages
    finally:
        # Even a failed assertion must disconnect the owned terminal before
        # joining gateway shutdown; never strand a private tmux/CLI on failure.
        try:
            terminal.call('kill-server')
        finally:
            stop.set()
            if thread is not None:
                thread.join(8)
                assert not thread.is_alive() and not errors


@pytest.mark.parametrize('first_arm', ['b', 'f'], ids=['sampling-first', 'elicitation-first'])
def test_linux_cold_claw_browser_sampling_arm_uses_current_owner(tmp_path, monkeypatch, first_arm):
    """Do not silently replace a cold admission failure with preselected-owner proof."""
    from xdog.claw.core.runtime.sampling_protocol import SamplingConnection

    log = tmp_path / 'cold-admission.jsonl'
    request = SamplingConnection.request

    def record(kind, value):
        with log.open('a') as output:
            output.write(json.dumps({'kind': kind, 'value': value}, default=str) + '\n')

    async def tracked(self, value):
        record('request', value)
        record('source', self.orchestrator.source_lease(value.get('group_id', '')))
        if not getattr(self, '_diagnostic_write', False):
            self._diagnostic_write = True
            write = self.write
            async def watched(reply):
                record('reply', reply)
                return await write(reply)
            self.write = watched
        async def snapshot():
            await asyncio.sleep(2)
            record('tasks', [{'name': task.get_name(),
                             'stack': [(frame.f_code.co_filename, frame.f_code.co_name, frame.f_lineno)
                                       for frame in task.get_stack()]}
                            for task in asyncio.all_tasks()])
        diagnostic = asyncio.create_task(snapshot())
        try:
            return await request(self, value)
        finally:
            # Keep the pending-job diagnostic alive only for this original loop.
            diagnostic.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)

    monkeypatch.setattr(SamplingConnection, 'request', tracked)
    test_linux_modern_browser_full_reviews_final_document_resize_and_parent_cursor(
        tmp_path, 'claw', 'resources/read', 'roots', preselect_owner=False, first_arm=first_arm)
