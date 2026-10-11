"""Actual pinned SDK serving runtime, modern three-method/four-family continuations."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.http import HttpClient
from xdog.agent.mcp.sampling_executor import SamplingExecutor
from xdog.agent.mcp.stdio import McpError

from packages.agent.tests.test_mcp_sampling_executor import Adapter
from packages.agent.tests.test_mcp_sampling_owner import selection, until

SDK_ROOT = os.environ.get('XDOG_MCP_OFFICIAL_SDK_ROOT')
NODE = shutil.which('node')
PEER = Path(__file__).parent / 'fixtures/mcp_official_sdk/input_peer.mjs'
pytestmark = pytest.mark.skipif(not SDK_ROOT or not NODE, reason='Explicit pinned independent MCP SDK 2.3.1 required')


@pytest.mark.parametrize('transport', ['stdio', 'http'])
@pytest.mark.parametrize('method', ['tools/call', 'resources/read', 'prompts/get'])
@pytest.mark.parametrize('family', ['roots', 'sampling', 'form', 'url'])
async def test_official_modern_input_requests_same_owner_reviews_and_exact_fresh_id_resume(
        tmp_path, transport, method, family):
    log, events, process = tmp_path / 'frames.jsonl', [], None
    if transport == 'http':
        process = await asyncio.create_subprocess_exec(NODE, str(PEER), transport, str(log),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(5):
            port = json.loads(await process.stdout.readline())['port']
        config = {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp', 'timeout': 5}
    else:
        config = {'command': NODE, 'args': [str(PEER), transport, str(log)], 'timeout': 5}
    config['roots'] = [{'uri': 'file:///explicit', 'name': 'Explicit'}]
    manager, adapter, work = McpManager({'sdk': config}, tmp_path), Adapter(), None
    try:
        await manager.register_sampling('sdk', lease='view', publish=events.append,
            execute=SamplingExecutor(adapter, ('explicit-model',), lambda: True), alive=lambda: True, tasks=True)
        await manager.register_elicitation('sdk', lease='view', publish=events.append, alive=lambda: True, tasks=True)
        await manager.connect('sdk')
        client = manager._clients['sdk']
        assert client.modern and not client.reverse.active and client.reverse.tasks is None
        assert manager.status()[0]['client_capabilities'] == {
            'roots': {}, 'sampling': {'tools': {}}, 'elicitation': {'form': {}, 'url': {}}}
        params = {'uri': 'test://' + family} if method == 'resources/read' else {'name': family}
        if method == 'tools/call':
            params['arguments'] = {}
        params['_meta'] = {'fixture/trace': 'ORIGINAL'}
        mirrors = {'Mcp-Name': 'test://' + family} if method == 'resources/read' else {'Mcp-Name': family}
        operation = (client.request(method, params, mirrors=mirrors) if isinstance(client, HttpClient)
                     else client.request(method, params))
        work = asyncio.create_task(manager._bridge(operation))
        if family != 'roots':
            await until(lambda: events or work.done())
            if work.done():
                await work  # Surface peer/transport diagnostic rather than just a timeout.
            assert events[0]['protocol'] == '2026-07-28' and not adapter.calls
            assert len([json.loads(row) for row in log.read_text().splitlines()
                        if json.loads(row)['type'] == 'operation']) == 1
            # Another wire request still completes while the native review is held.
            assert (await manager._bridge(client.request('tools/list', {})))['tools']
            first = events[0]
            if family == 'sampling':
                with pytest.raises(McpError, match='owner changed'):
                    await manager.answer_sampling('sdk', first['id'], lease='foreign', phase='input',
                        receipt=first['receipt'], sha256=first['sha256'], approve=True, selection=selection())
                await manager.answer_sampling('sdk', first['id'], lease='view', phase='input',
                    receipt=first['receipt'], sha256=first['sha256'], approve=True, selection=selection())
            else:
                proposal = {'action': 'accept', **({'content': {'name': 'PRIVATE'}} if family == 'form' else {})}
                await manager.answer_elicitation('sdk', first['id'], lease='view', phase='request',
                    receipt=first['receipt'], sha256=first['sha256'], response=proposal)
            await until(lambda: len(events) >= 2)
            second = events[1]
            assert second['phase'] == ('output' if family == 'sampling' else 'response') and not work.done()
            if family == 'sampling':
                await manager.answer_sampling('sdk', second['id'], lease='view', phase='output',
                    receipt=second['receipt'], sha256=second['sha256'], approve=True)
            else:
                await manager.answer_elicitation('sdk', second['id'], lease='view', phase='response',
                    receipt=second['receipt'], sha256=second['sha256'], response={'action': 'accept'})
        result = await asyncio.wait_for(work, 5)
        assert 'APPROVED-' + family in json.dumps(result)
        records = [json.loads(line) for line in log.read_text().splitlines()]
        requests = [row['message'] for row in records if row['type'] == 'inbound'
                    and row['message'].get('method') == method]
        assert len(requests) == 2 and requests[0]['id'] != requests[1]['id']
        before, after = requests[0]['params'], requests[1]['params']
        assert {key: value for key, value in after.items() if key not in ('requestState', 'inputResponses')} == before
        assert after['requestState'] == '  opaque\x00state/汉字\n  '
        assert after['_meta']['fixture/trace'] == 'ORIGINAL'
        assert 'tasks' not in before['_meta']['io.modelcontextprotocol/clientCapabilities']
        assert set(after['inputResponses']) == {'review'}
        assert not set(after['inputResponses']['review']) & {'method', 'result', 'id', 'jsonrpc'}
        assert not client.inputs._jobs and not client.reverse._jobs
        assert len(adapter.calls) == (1 if family == 'sampling' else 0)
        if family == 'url':
            assert not manager._elicitation['sdk'][1]._urls
            assert not any(event['type'].startswith('elicitation_url') for event in events)
        if transport == 'http':
            frames = [row for row in records if row['type'] == 'inbound' and row['message'].get('method') == method]
            assert frames[0]['headers']['mcp-method'] == frames[1]['headers']['mcp-method'] == method
    finally:
        await manager.aclose()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        if process is not None:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)


async def test_official_modern_dynamic_root_selection_uses_existing_review_and_no_obsolete_notification(tmp_path):
    log = tmp_path / 'frames.jsonl'
    manager = McpManager({'sdk': {'command': NODE, 'args': [str(PEER), 'stdio', str(log)],
        'roots': [{'uri': 'file:///explicit', 'name': 'Explicit'}]}}, tmp_path)
    try:
        await manager.connect('sdk')
        review = await manager.manage_roots('sdk', 'prepare', [], lease='view', alive=lambda: True)
        assert not any(json.loads(row)['type'] == 'operation' for row in log.read_text().splitlines())
        await manager.manage_roots('sdk', 'commit', receipt=review['review_id'], lease='view', alive=lambda: True)
        document = await manager.resource('sdk', 'test://roots')
        assert 'APPROVED-roots' in document.text
        records = [json.loads(row) for row in log.read_text().splitlines()]
        continued = next(row for row in records if row['type'] == 'operation' and row.get('responses'))
        assert continued['responses'] == {'review': {'roots': []}}
        assert not any(row.get('message', {}).get('method') == 'notifications/roots/list_changed' for row in records)
        with pytest.raises(McpError, match='owner/revision'):
            await manager.manage_roots('sdk', 'commit', receipt=review['review_id'], lease='view', alive=lambda: True)
    finally:
        await manager.aclose()


@pytest.mark.parametrize('end', ['detach', 'disconnect', 'cancel'])
async def test_official_modern_sampling_cancellation_waits_actual_adapter_finally_and_never_resumes(tmp_path, end):
    import threading
    log, events = tmp_path / 'frames.jsonl', []
    entered, cleaning, release = threading.Event(), threading.Event(), threading.Event()
    manager = McpManager({'sdk': {'command': NODE, 'args': [str(PEER), 'stdio', str(log)]}}, tmp_path)
    async def execute(value, chosen, cancel):
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass  # A hostile/buggy adapter cannot turn cancellation into late success.
        finally:
            cleaning.set()
            while not release.is_set():
                await asyncio.sleep(.002)
        return {'model': chosen['model'], 'role': 'assistant', 'content': {'type': 'text', 'text': 'LATE PRIVATE'}}
    work = closing = None
    try:
        await manager.register_sampling('sdk', lease='view', execute=execute, publish=events.append, alive=lambda: True)
        await manager.connect('sdk')
        client = manager._clients['sdk']
        work = asyncio.create_task(manager.resource('sdk', 'test://sampling'))
        await until(lambda: events)
        first = events[0]
        await manager.answer_sampling('sdk', first['id'], lease='view', phase='input', receipt=first['receipt'],
                                      sha256=first['sha256'], approve=True, selection=selection())
        assert await asyncio.to_thread(entered.wait, 2)
        if end == 'cancel':
            work.cancel()
        else:
            closing = asyncio.create_task(manager.disconnect('sdk') if end == 'disconnect' else
                                          manager.detach_sampling('sdk', lease='view'))
        assert await asyncio.to_thread(cleaning.wait, 2)
        assert not work.done() and client.inputs._jobs
        if closing is not None:
            closing.cancel()
            await asyncio.sleep(.01)
            assert not closing.done()
        if end != 'disconnect':
            assert (await manager._bridge(client.request('tools/list', {})))['tools']
        release.set()
        await asyncio.gather(work, *([closing] if closing else []), return_exceptions=True)
        assert not client.inputs._jobs
        records = [json.loads(row) for row in log.read_text().splitlines()]
        assert len([row for row in records if row['type'] == 'operation']) == 1
        assert 'LATE PRIVATE' not in json.dumps(records)
        assert not any(event.get('phase') == 'output' for event in events)
    finally:
        release.set()
        await manager.aclose()
        await asyncio.gather(*([work] if work else []), *([closing] if closing else []), return_exceptions=True)
