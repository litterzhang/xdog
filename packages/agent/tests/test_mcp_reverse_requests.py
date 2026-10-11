"""Owned reverse handler lifecycle; no approval/model implementation implied."""
from __future__ import annotations

import asyncio
import json
import sys

from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.reverse_requests import ReverseRequests
from xdog.agent.mcp.stdio import StdioClient


def packet(identifier=1):
    return {'jsonrpc': '2.0', 'id': identifier, 'method': 'sampling/createMessage',
            'params': {'messages': [], 'maxTokens': 20}}


async def test_handler_is_opt_in_capacity_bounded_and_replay_refused():
    owner = ReverseRequests()
    sent = []
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        entered.set()
        await release.wait()
        return {'role': 'assistant', 'content': {'type': 'text', 'text': 'OWNED RESULT'}, 'model': 'offline'}
    async def write(value):
        sent.append(value)
    assert not owner.admit(packet(), write, timeout=1) and owner.advertised() == {}
    owner.handler, owner.active = handler, True
    assert owner.advertised() == {'sampling': {}}
    for identifier in range(4):
        assert owner.admit(packet(identifier), write, timeout=2)
    assert not owner.admit(packet(4), write, timeout=2)
    assert not owner.admit(packet(0), write, timeout=2)
    await entered.wait()
    release.set()
    async with asyncio.timeout(3):
        while owner._jobs:
            await asyncio.sleep(.001)
    assert len(sent) == 4 and not owner.admit(packet(0), write, timeout=2)
    await owner.close()
    assert not owner._jobs and not owner.active


async def test_withdrawal_waits_callback_finally_and_never_delivers_to_successor():
    owner = ReverseRequests()
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    sent = []
    async def handler(request):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            await release.wait()
    async def write(value):
        sent.append(value)
    owner.handler, owner.active = handler, True
    assert owner.admit(packet(), write, timeout=5)
    await entered.wait()
    work = asyncio.create_task(owner.close())
    await cleanup.wait()
    assert not work.done() and not owner.active
    release.set()
    await asyncio.wait_for(work, 2)
    assert not sent and not owner._jobs


async def test_remote_cancel_exact_id_bool_rejected_and_no_private_error_leak():
    owner, sent = ReverseRequests(), []
    entered = asyncio.Event()
    async def handler(request):
        entered.set()
        await asyncio.sleep(30)
        raise RuntimeError('PRIVATE-CREDENTIAL')
    async def write(value):
        sent.append(value)
    owner.handler, owner.active = handler, True
    owner.admit(packet('owned'), write, timeout=2)
    await entered.wait()
    owner.cancel({'method': 'notifications/cancelled', 'params': {'requestId': True}})
    assert not owner._jobs['owned'].cancelling()
    owner.cancel({'method': 'notifications/cancelled', 'params': {'requestId': 'owned'}})
    async with asyncio.timeout(3):
        while owner._jobs:
            await asyncio.sleep(.001)
    assert sent[0]['id'] == 'owned' and sent[0]['error']['code'] == -32000
    assert 'PRIVATE' not in json.dumps(sent)
    await owner.close()


async def test_actual_stdio_reader_responds_to_ping_while_sampling_handler_held(tmp_path):
    peer = tmp_path / 'peer.py'
    peer.write_text('''import json,sys
for raw in sys.stdin:
    row=json.loads(raw);method=row.get('method')
    def send(value): print(json.dumps(value),flush=True)
    if method=='server/discover':
        send({'jsonrpc':'2.0','id':row['id'],'error':{'code':-32601,'message':'legacy'}})
    elif method=='initialize':
        assert row['params']['capabilities']=={'sampling':{}}
        send({'jsonrpc':'2.0','id':row['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}})
    elif method=='tools/list':
        send({'jsonrpc':'2.0','id':'sampling-held','method':'sampling/createMessage',
              'params':{'messages':[],'maxTokens':20}})
        send({'jsonrpc':'2.0','id':row['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':row['id'],'result':{}})
''')
    client = StdioClient(McpServerConfig.parse('owned', {'command': sys.executable,
        'args': [str(peer)], 'timeout': 2}), tmp_path)
    entered, settled = asyncio.Event(), asyncio.Event()
    async def handler(request):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            settled.set()
    client.reverse.handler = handler
    try:
        await client.connect()
        assert await client.request('tools/list', {}) == {'tools': []}
        await asyncio.wait_for(entered.wait(), 2)
        assert await client.request('ping', {}) == {}
        assert len(client.reverse._jobs) == 1
        process = client.process
    finally:
        await client.close()
    assert settled.is_set() and not client.reverse._jobs and process.returncode is not None


async def test_http_reverse_job_does_not_block_correlated_result_or_deliver_after_close(tmp_path):
    import httpx
    from xdog.agent.mcp.http import HttpClient
    sent = []
    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(202)
    client = HttpClient(McpServerConfig.parse('owned', {'transport': 'http', 'url': 'http://127.0.0.1/mcp'}))
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client.state, client.version = 'connected', '2025-11-25'
    client.reverse.active = True
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        entered.set()
        await release.wait()
        return {'model': 'offline', 'role': 'assistant', 'content': {'type': 'text', 'text': 'REVIEWED'}}
    client.reverse.handler = handler
    try:
        assert await client._message(packet('http-owned'), None, 200, modern=False) is None
        await asyncio.wait_for(entered.wait(), 2)
        assert await client._message({'jsonrpc': '2.0', 'id': 42, 'result': {'content': []}},
                                     42, 200, modern=False) == {'content': []}
        assert not sent
        release.set()
        async with asyncio.timeout(2):
            while client.reverse._jobs:
                await asyncio.sleep(.001)
        assert len(sent) == 1 and sent[0]['id'] == 'http-owned'
        assert sent[0]['result']['content']['text'] == 'REVIEWED'
    finally:
        await client.close()
    assert not client.reverse.active and not client.reverse._jobs


async def test_handler_failure_timeout_and_replacement_do_not_publish_private_data():
    owner, sent = ReverseRequests(), []
    async def write(value):
        sent.append(value)
    async def failed(request):
        raise RuntimeError('PRIVATE-CREDENTIAL-DIAGNOSTIC')
    async def timedout(request):
        await asyncio.sleep(30)
    owner.active = True
    for index, handler in enumerate((failed, timedout)):
        owner.handler = handler
        assert owner.admit(packet(index), write, timeout=.02)
        async with asyncio.timeout(2):
            while owner._jobs:
                await asyncio.sleep(.001)
    assert len(sent) == 2 and all(row['error']['code'] == -32000 for row in sent)
    assert 'PRIVATE' not in json.dumps(sent)
    entered, release = asyncio.Event(), asyncio.Event()
    async def old(request):
        entered.set()
        await release.wait()
        return {'private': 'STALE RESULT'}
    owner.handler = old
    assert owner.admit(packet(3), write, timeout=1)
    await entered.wait()
    owner.handler = failed
    release.set()
    async with asyncio.timeout(2):
        while owner._jobs:
            await asyncio.sleep(.001)
    assert len(sent) == 2
    await owner.close()


async def test_invalid_request_never_enters_handler_and_mutation_cannot_expand_result_authority():
    owner, sent, called = ReverseRequests(), [], []
    async def write(value):
        sent.append(value)
    async def handler(request):
        called.append(request)
        request['params']['tools'] = [{'name': 'forged', 'inputSchema': {'type': 'object'}}]
        return {'model': 'host', 'role': 'assistant', 'content': [
            {'type': 'tool_use', 'name': 'forged', 'id': 'call', 'input': {}}]}
    owner.handler, owner.active = handler, True
    invalid = packet('invalid')
    invalid['params']['maxTokens'] = True
    assert owner.admit(invalid, write, timeout=1)
    async with asyncio.timeout(2):
        while owner._jobs:
            await asyncio.sleep(.001)
    assert not called and sent[-1]['error']['code'] == -32000
    assert owner.admit(packet('mutation'), write, timeout=1)
    async with asyncio.timeout(2):
        while owner._jobs:
            await asyncio.sleep(.001)
    assert len(called) == 1 and sent[-1]['error']['code'] == -32000
    await owner.close()


async def test_canceling_validation_keeps_capacity_until_actual_owned_worker_exits(monkeypatch):
    import threading

    import xdog.agent.mcp.sampling as module
    entered, release = threading.Event(), threading.Event()
    original = module.request
    def held(value):
        entered.set()
        assert release.wait(5)
        return original(value)
    monkeypatch.setattr(module, 'request', held)
    owner, called, sent = ReverseRequests(), [], []
    async def handler(request):
        called.append(request)
        return {'model': 'host', 'role': 'assistant', 'content': {'type': 'text', 'text': 'RESULT'}}
    async def write(value):
        sent.append(value)
    owner.handler, owner.active = handler, True
    owner.admit(packet('validate'), write, timeout=1)
    work = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        work = asyncio.create_task(owner.close())
        await asyncio.sleep(.02)
        assert not work.done() and owner._jobs and not owner.active
        release.set()
        await asyncio.wait_for(work, 2)
        assert not owner._jobs and not called and not sent
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await owner.close()


async def test_sampling_subcapabilities_require_explicit_installed_support_and_withdraw_with_family():
    owner = ReverseRequests()
    owner.active = True
    owner.sampling_tools = owner.sampling_context = True
    assert owner.advertised() == {}  # Flags without handler grant no capability.
    async def handler(value):
        return {}
    owner.handler = handler
    assert owner.advertised() == {'sampling': {'tools': {}, 'context': {}}}
    await owner.withdraw('sampling/createMessage')
    assert not owner.sampling_tools and not owner.sampling_context and owner.advertised() == {}
    await owner.close()
