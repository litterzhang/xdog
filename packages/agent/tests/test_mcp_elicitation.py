"""Inert form/URL validation and separately installed reverse handler contracts."""
from __future__ import annotations

import asyncio
import copy

import pytest
from xdog.agent.mcp.elicitation import request, result
from xdog.agent.mcp.reverse_requests import ReverseRequests
from xdog.agent.mcp.stdio import McpError


def form(**fields):
    return {'jsonrpc': '2.0', 'id': 'elicitation', 'method': 'elicitation/create', 'params': {
        'message': 'Enter reviewed data', 'requestedSchema': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'minLength': 2},
            'count': {'type': 'integer', 'minimum': 0},
            'enabled': {'type': 'boolean'},
            'tags': {'type': 'array', 'items': {'type': 'string', 'enum': ['a', 'b']}}},
            'required': ['name']}, **fields}}


def test_form_result_exact_types_required_enum_and_no_unrequested_fields():
    source = request(form())
    accepted = {'action': 'accept', 'content': {'name': 'USER', 'count': 3, 'enabled': True, 'tags': ['a']}}
    assert result(accepted, source) == accepted
    for invalid in ({'name': 'a'}, {'name': 'USER', 'count': True}, {'count': 3},
                    {'name': 'USER', 'tags': ['unlisted']}, {'name': 'USER', 'unrequested': 'PRIVATE'}):
        with pytest.raises(McpError):
            result({'action': 'accept', 'content': invalid}, source)
    for action in ('decline', 'cancel'):
        assert result({'action': action}, source) == {'action': action}
        with pytest.raises(McpError):
            result({'action': action, 'content': {'name': 'PRIVATE'}}, source)


@pytest.mark.parametrize('field', [
    {'type': 'string', 'oneOf': [{'const': 'one', 'title': 'First'},
                               {'const': 'two', 'title': 'Second'}], 'default': 'one'},
    {'type': 'array', 'items': {'anyOf': [{'const': 'one', 'title': 'First'}]}, 'default': ['one']},
    {'type': 'string', 'enum': ['one'], 'enumNames': ['First'], 'default': 'one'},
    {'type': 'number', 'minimum': 0, 'maximum': 10, 'default': 1.5},
])
def test_named_primitive_and_titled_enum_contract_retained(field):
    source = form(requestedSchema={'type': 'object', 'properties': {'field': field}})
    assert request(source) == source
    assert result({'action': 'accept', 'content': {'field': field['default']}}, source)['action'] == 'accept'


@pytest.mark.parametrize('field', [
    {'type': 'object'}, {'type': 'string', '$ref': 'https://never-fetch.invalid/schema'},
    {'type': 'string', 'enum': ['duplicate', 'duplicate']},
    {'type': 'string', 'enum': ['one'], 'enumNames': []},
    {'type': 'string', 'minLength': 5, 'maxLength': 2},
    {'type': 'boolean', 'default': 'not bool'}, {'type': 'integer', 'default': 1.5},
    {'type': 'array', 'items': {'type': 'string'}}, {'type': 'string', 'format': 'password'},
])
def test_unsupported_or_invalid_schema_refuses_not_fetches_or_coerces(field):
    with pytest.raises(McpError):
        request(form(requestedSchema={'type': 'object', 'properties': {'field': field}}))


@pytest.mark.parametrize('url', ['https://operator.example/flow?id=one', 'http://operator.example/flow',
                                'https://user:pass@operator.example/flow', 'file:///private', 'https://host/\nunsafe'])
def test_url_is_inert_explicit_https_not_browser_or_login(url):
    source = {'jsonrpc': '2.0', 'id': 'url', 'method': 'elicitation/create',
              'params': {'mode': 'url', 'message': 'Review external interaction', 'elicitationId': 'owned', 'url': url}}
    if url == 'https://operator.example/flow?id=one':
        assert request(source) == source
        assert result({'action': 'accept'}, source) == {'action': 'accept'}
        with pytest.raises(McpError):
            result({'action': 'accept', 'content': {'private': 'data'}}, source)
    else:
        with pytest.raises(McpError):
            request(source)


async def test_reverse_elicitation_handler_independent_default_off_and_result_validation():
    dispatcher, sent, calls = ReverseRequests(), [], []
    async def write(value):
        sent.append(value)
    async def handler(value):
        calls.append(copy.deepcopy(value))
        # Attempt to expand accepted form data by mutating the callback copy.
        value['params']['requestedSchema']['properties']['private'] = {'type': 'string'}
        return {'action': 'accept', 'content': {'name': 'USER', 'private': 'must-not-release'}}
    dispatcher.active = True
    assert dispatcher.advertised() == {} and not dispatcher.admit(form(), write, timeout=2)
    dispatcher.elicitation_handler = handler
    assert dispatcher.advertised() == {'elicitation': {'form': {}, 'url': {}}}
    assert dispatcher.admit(form(), write, timeout=2)
    async with asyncio.timeout(3):
        while dispatcher._jobs:
            await asyncio.sleep(.001)
    assert len(calls) == 1 and sent[0]['error']['code'] == -32000
    assert 'must-not-release' not in str(sent)
    assert not dispatcher.admit(form(), write, timeout=2)
    await dispatcher.close()


async def test_elicitation_cancel_and_sampling_share_bounded_original_identity_capacity():
    dispatcher, sent = ReverseRequests(), []
    entered, cleaned = asyncio.Event(), asyncio.Event()
    async def handler(value):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleaned.set()
    async def write(value):
        sent.append(value)
    dispatcher.active, dispatcher.elicitation_handler = True, handler
    dispatcher.admit(form(), write, timeout=5)
    await entered.wait()
    dispatcher.cancel({'method': 'notifications/cancelled', 'params': {'requestId': 'elicitation'}})
    async with asyncio.timeout(3):
        while dispatcher._jobs:
            await asyncio.sleep(.001)
    assert cleaned.is_set() and sent[0]['id'] == 'elicitation' and sent[0]['error']['code'] == -32000
    await dispatcher.close()


async def test_actual_stdio_elicitation_holds_review_without_blocking_original_requests(tmp_path):
    import json
    import sys

    from xdog.agent.mcp.config import McpServerConfig
    from xdog.agent.mcp.stdio import StdioClient
    script, log = tmp_path / 'peer.py', tmp_path / 'frames'
    script.write_text('''import json,sys
from pathlib import Path
log=Path(sys.argv[1])
def send(value): print(json.dumps(value),flush=True)
for raw in sys.stdin:
    row=json.loads(raw)
    with log.open('a') as f:f.write(json.dumps(row)+'\\n')
    method=row.get('method')
    if method=='server/discover':
        send({'jsonrpc':'2.0','id':row['id'],'error':{'code':-32601,'message':'legacy'}})
    elif method=='initialize':
        assert row['params']['capabilities']=={'elicitation':{'form':{},'url':{}}}
        send({'jsonrpc':'2.0','id':row['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}})
    elif method=='tools/list':
        send({'jsonrpc':'2.0','id':'form','method':'elicitation/create','params':{
            'message':'Review name','requestedSchema':{'type':'object',
                'properties':{'name':{'type':'string'}},'required':['name']}}})
        send({'jsonrpc':'2.0','id':row['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':row['id'],'result':{}})
''')
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(value):
        entered.set()
        await release.wait()
        return {'action': 'accept', 'content': {'name': 'USER REVIEWED'}}
    client = StdioClient(McpServerConfig.parse('owned', {'command': sys.executable,
        'args': [str(script), str(log)], 'timeout': 3}), tmp_path)
    client.reverse.elicitation_handler = handler
    try:
        await client.connect()
        assert await client.request('tools/list', {}) == {'tools': []}
        await asyncio.wait_for(entered.wait(), 2)
        assert await client.request('ping', {}) == {}
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        assert not any(row.get('id') == 'form' for row in frames)
        release.set()
        async with asyncio.timeout(2):
            while client.reverse._jobs:
                await asyncio.sleep(.002)
        await client.request('ping', {})
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        response = next(row for row in frames if row.get('id') == 'form')
        assert response['result'] == {'action': 'accept', 'content': {'name': 'USER REVIEWED'}}
    finally:
        release.set()
        await client.close()
    assert not client.reverse._jobs and not client.reverse.active


async def test_http_elicitation_result_route_is_independent_and_exactly_correlated():
    import json

    import httpx
    from xdog.agent.mcp.config import McpServerConfig
    from xdog.agent.mcp.http import HttpClient
    frames = []
    def transport(value):
        frames.append(json.loads(value.content))
        return httpx.Response(202)
    client = HttpClient(McpServerConfig.parse('owned', {'transport': 'http', 'url': 'http://127.0.0.1/mcp'}))
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    client.state, client.version, client.reverse.active = 'connected', '2025-11-25', True
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(value):
        entered.set()
        await release.wait()
        return {'action': 'decline'}
    client.reverse.elicitation_handler = handler
    try:
        assert await client._message(form(), None, 200, modern=False) is None
        await asyncio.wait_for(entered.wait(), 2)
        assert await client._message({'jsonrpc': '2.0', 'id': 3, 'result': {'tools': []}},
                                     3, 200, modern=False) == {'tools': []}
        assert not frames
        release.set()
        async with asyncio.timeout(2):
            while client.reverse._jobs:
                await asyncio.sleep(.002)
        assert frames == [{'jsonrpc': '2.0', 'id': 'elicitation', 'result': {'action': 'decline'}}]
    finally:
        release.set()
        await client.close()
