"""Named client capability refusals are evidence, not missing-feature parity claims."""
from __future__ import annotations

import json
import sys

import pytest
from xdog.agent.mcp.client_capabilities import advertised, inventory, reverse_response
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.stdio import StdioClient


def test_reverse_request_invalid_identity_never_reflected() -> None:

    def _check_case(identifier):
        with pytest.raises(ValueError, match="identity"):
            reverse_response({"jsonrpc": "2.0", "id": identifier, "method": "ping"})

    for identifier in [True, False, None, [], {}, "", "x" * 129, 2**53]:
        _check_case(identifier)


@pytest.mark.parametrize('method', ['roots/list', 'sampling/createMessage', 'elicitation/create',
                                  'tasks/list', 'custom/extension'])
def test_unimplemented_reverse_family_explicit_refusal_not_borrowed_host_capability(method):
    assert reverse_response({'jsonrpc': '2.0', 'id': 'owned', 'method': method, 'params': {}}) == {
        'error': {'code': -32601, 'message': 'Client capability not supported'}}
    assert advertised() == {} and set(inventory()) == {
        'roots', 'sampling', 'elicitation', 'tasks', 'experimental', 'extensions'}
    copied = advertised()
    copied['sampling'] = {}
    assert advertised() == {}


async def test_actual_legacy_stdio_reverse_refusals_and_ping_preserve_reader_correlation(tmp_path):
    script = tmp_path / 'owned_peer.py'
    log = tmp_path / 'requests.jsonl'
    script.write_text('''import json,sys
from pathlib import Path
log=Path(sys.argv[1])
def send(value):
    print(json.dumps(value),flush=True)
for raw in sys.stdin:
    value=json.loads(raw)
    with log.open('a') as file:file.write(json.dumps(value)+'\\n')
    method=value.get('method')
    if method=='server/discover':
        send({'jsonrpc':'2.0','id':value['id'],'error':{'code':-32601,'message':'legacy'}})
    elif method=='initialize':
        assert value['params']['capabilities']=={}
        send({'jsonrpc':'2.0','id':value['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}})
    elif method=='tools/list':
        for index,method in enumerate(['roots/list','sampling/createMessage','elicitation/create','tasks/list','ping']):
            send({'jsonrpc':'2.0','id':'reverse-'+str(index),'method':method,'params':{}})
        send({'jsonrpc':'2.0','id':value['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':value['id'],'result':{}})
''')
    config = McpServerConfig.parse('owned', {'command': sys.executable, 'args': [str(script), str(log)], 'timeout': 2})
    client = StdioClient(config, tmp_path)
    try:
        await client.connect()
        assert await client.request('tools/list', {}) == {'tools': []}
        assert await client.request('ping', {}) == {}
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        replies = [row for row in frames if str(row.get('id', '')).startswith('reverse-')]
        assert len(replies) == 5
        assert [row['error']['code'] for row in replies[:-1]] == [-32601] * 4
        assert replies[-1]['result'] == {}
        assert not any('roots' in row.get('params', {}).get('capabilities', {}) for row in frames)
        process = client.process
    finally:
        await client.close()
    assert process.returncode is not None and not client._pending


async def test_http_legacy_reverse_response_exact_identity_and_modern_no_unsolicited_admission(tmp_path):
    import httpx
    from xdog.agent.mcp.http import HttpClient
    from xdog.agent.mcp.stdio import McpError
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(202)
    client = HttpClient(McpServerConfig.parse('owned', {'transport': 'http', 'url': 'http://127.0.0.1/mcp'}))
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client.version, client.state = '2025-11-25', 'connected'
    try:
        for method in ('roots/list', 'sampling/createMessage', 'elicitation/create'):
            value = {'jsonrpc': '2.0', 'id': 'same-id', 'method': method, 'params': {}}
            assert await client._message(value, None, 200, modern=False) is None
            assert requests[-1]['id'] == 'same-id' and requests[-1]['error']['code'] == -32601
            with pytest.raises(McpError, match='unrelated'):
                await client._message(value, None, 200, modern=True)
        count = len(requests)
        with pytest.raises(ValueError, match='identity'):
            await client._message({'jsonrpc': '2.0', 'id': True, 'method': 'ping'}, None, 200, modern=False)
        assert len(requests) == count
    finally:
        await client.close()
