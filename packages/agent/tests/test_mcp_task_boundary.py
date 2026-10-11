"""Unadvertised task requests never become ordinary model/form execution."""
from __future__ import annotations

import json
import sys

import pytest
from xdog.agent.mcp.client_capabilities import reverse_response
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.reverse_requests import ReverseRequests
from xdog.agent.mcp.stdio import McpError, StdioClient


@pytest.mark.parametrize('method', ['sampling/createMessage', 'elicitation/create'])
@pytest.mark.parametrize('augmentation', [{'task': {}}, {'task': {'ttl': 100}}, {'task': None},
    {'_meta': {'io.modelcontextprotocol/related-task': {'taskId': 'foreign'}}}])
async def test_task_augmentation_refused_before_installed_handler(method, augmentation):
    owner, calls = ReverseRequests(), []
    async def handler(value):
        calls.append(value)
        return {}
    async def write(value):
        pytest.fail('Rejected task must use ordinary transport refusal, not async handler job')
    owner.active, owner.handler, owner.elicitation_handler = True, handler, handler
    value = {'jsonrpc': '2.0', 'id': 'task', 'method': method, 'params': augmentation}
    assert reverse_response(value) == {'error': {'code': -32602, 'message': 'Client task capability not supported'}}
    assert not owner.admit(value, write, timeout=1)
    assert not calls and not owner._jobs and 'tasks' not in owner.advertised()
    await owner.close()


async def test_actual_stdio_task_refusal_exact_receipt_and_reader_recovery(tmp_path):
    script, log = tmp_path / 'peer.py', tmp_path / 'log'
    script.write_text('''import json,sys
from pathlib import Path
log=Path(sys.argv[1])
def send(row): print(json.dumps(row),flush=True)
for raw in sys.stdin:
    row=json.loads(raw)
    with log.open('a') as f:f.write(json.dumps(row)+'\\n')
    method=row.get('method')
    if method=='server/discover':
        send({'jsonrpc':'2.0','id':row['id'],'error':{'code':-32601,'message':'legacy'}})
    elif method=='initialize':
        assert 'tasks' not in row['params']['capabilities']
        send({'jsonrpc':'2.0','id':row['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}})
    elif method=='tools/list':
        for index,method in enumerate(['sampling/createMessage','elicitation/create']):
            send({'jsonrpc':'2.0','id':'task-'+str(index),'method':method,'params':{'task':{'ttl':100}}})
        send({'jsonrpc':'2.0','id':row['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':row['id'],'result':{}})
''')
    client = StdioClient(McpServerConfig.parse('owned', {'command': sys.executable,
        'args': [str(script), str(log)], 'timeout': 2}), tmp_path)
    calls = []
    async def handler(value):
        calls.append(value)
        return {}
    client.reverse.handler = client.reverse.elicitation_handler = handler
    try:
        await client.connect()
        assert await client.request('tools/list', {}) == {'tools': []}
        assert await client.request('ping', {}) == {}
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        refused = [row for row in rows if str(row.get('id', '')).startswith('task-')]
        assert len(refused) == 2 and all(row['error']['code'] == -32602 for row in refused)
        assert not calls and not client.reverse._jobs
    finally:
        await client.close()


async def test_http_task_refusal_does_not_invoke_handler_or_break_correlation():
    import httpx
    from xdog.agent.mcp.http import HttpClient
    sent, calls = [], []
    def transport(value):
        sent.append(json.loads(value.content))
        return httpx.Response(202)
    client = HttpClient(McpServerConfig.parse('owned', {'transport': 'http', 'url': 'http://127.0.0.1/mcp'}))
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    client.state, client.version, client.reverse.active = 'connected', '2025-11-25', True
    async def handler(value):
        calls.append(value)
        return {}
    client.reverse.handler = handler
    try:
        await client._message({'jsonrpc': '2.0', 'id': 'task', 'method': 'sampling/createMessage',
                               'params': {'task': {}}}, None, 200, modern=False)
        assert sent == [{'jsonrpc': '2.0', 'id': 'task', 'error': {
            'code': -32602, 'message': 'Client task capability not supported'}}]
        assert await client._message({'jsonrpc': '2.0', 'id': 3, 'result': {}}, 3, 200, modern=False) == {}
        assert not calls
    finally:
        await client.close()


async def test_direct_owners_and_executor_cannot_bypass_task_negotiation():
    import asyncio

    from xdog.agent.mcp.elicitation_owner import ElicitationOwner
    from xdog.agent.mcp.sampling_executor import SamplingExecutor
    from xdog.agent.mcp.sampling_owner import SamplingOwner

    from packages.agent.tests.test_mcp_elicitation import form
    from packages.agent.tests.test_mcp_sampling_executor import Adapter
    from packages.agent.tests.test_mcp_sampling_owner import request, selection
    events, adapter = [], Adapter()
    executor = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    sampling = SamplingOwner('view', events.append, executor, lambda: True)
    elicitation = ElicitationOwner('view', events.append, lambda: True)
    source, elicit = request(), form()
    source['params']['task'] = elicit['params']['task'] = {}
    try:
        for owner, value in ((sampling, source), (elicitation, elicit)):
            with pytest.raises(McpError) as error:
                await owner.handle(value)
            assert error.value.code == -32602
        with pytest.raises(McpError) as error:
            await executor(source, selection(), asyncio.Event())
        assert error.value.code == -32602 and not events and not adapter.calls
    finally:
        await sampling.close()
        await elicitation.close()
