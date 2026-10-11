"""Explicit installed registry wire controls, not task creation/host parity evidence."""
from __future__ import annotations

import asyncio
import json
import sys

from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.reverse_requests import ReverseRequests
from xdog.agent.mcp.stdio import StdioClient
from xdog.agent.mcp.task_registry import TaskRegistry


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.002)


async def test_result_wait_reader_independence_and_exact_task_identity():
    owner, registry, sent = ReverseRequests(), TaskRegistry(), []
    owner.active, owner.tasks = True, registry
    release = asyncio.Event()
    async def operation(identifier):
        await release.wait()
        return {'private': 'REVIEWED RESULT'}
    async def write(value):
        sent.append(value)
    identifier = registry.create('sampling/createMessage', operation)['task']['taskId']
    try:
        assert owner.advertised() == {'tasks': {'list': {}, 'cancel': {}}}
        def packet(rid, method, params):
            return {'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}
        assert owner.admit(packet('result', 'tasks/result', {'taskId': identifier}), write, timeout=2)
        assert owner.admit(packet('get', 'tasks/get', {'taskId': identifier}), write, timeout=2)
        assert owner.admit(packet('foreign', 'tasks/get', {'taskId': 'foreign'}), write, timeout=2)
        await until(lambda: len(sent) == 2)
        assert next(row for row in sent if row['id'] == 'get')['result']['status'] == 'working'
        assert next(row for row in sent if row['id'] == 'foreign')['error']['code'] == -32000
        assert not any(row['id'] == 'result' for row in sent)
        release.set()
        await until(lambda: not owner._jobs)
        assert next(row for row in sent if row['id'] == 'result')['result'] == {'private': 'REVIEWED RESULT'}
        assert not owner.admit(packet('result', 'tasks/result', {'taskId': identifier}), write, timeout=2)
    finally:
        release.set()
        await owner.close()
    assert not registry._tasks and owner.tasks is None


async def test_actual_legacy_stdio_installed_registry_list_get_cancel_without_reader_block(tmp_path):
    registry = TaskRegistry()
    async def operation(identifier):
        await asyncio.sleep(30)
        return {}
    identifier = registry.create('elicitation/create', operation)['task']['taskId']
    script, log = tmp_path / 'peer.py', tmp_path / 'frames'
    script.write_text('''import sys,json
from pathlib import Path
log=Path(sys.argv[1]); task=sys.argv[2]
def send(value): print(json.dumps(value),flush=True)
for raw in sys.stdin:
    row=json.loads(raw)
    with log.open('a') as f:f.write(json.dumps(row)+'\\n')
    method=row.get('method')
    if method=='server/discover':
        send({'jsonrpc':'2.0','id':row['id'],'error':{'code':-32601,'message':'legacy'}})
    elif method=='initialize':
        assert row['params']['capabilities']=={'tasks':{'list':{},'cancel':{}}}
        send({'jsonrpc':'2.0','id':row['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}})
    elif method=='tools/list':
        for rid,method,params in [('list','tasks/list',{}),('get','tasks/get',{'taskId':task}),
                                  ('cancel','tasks/cancel',{'taskId':task})]:
            send({'jsonrpc':'2.0','id':rid,'method':method,'params':params})
        send({'jsonrpc':'2.0','id':row['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':row['id'],'result':{}})
''')
    client = StdioClient(McpServerConfig.parse('owned', {'command': sys.executable,
        'args': [str(script), str(log), identifier], 'timeout': 2}), tmp_path)
    client.reverse.tasks = registry
    try:
        await client.connect()
        assert await client.request('tools/list', {}) == {'tools': []}
        await until(lambda: not client.reverse._jobs)
        assert await client.request('ping', {}) == {}
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        responses = {row['id']: row for row in rows if row.get('id') in {'list', 'get', 'cancel'}}
        assert set(responses) == {'list', 'get', 'cancel'}
        assert responses['list']['result']['tasks'][0]['taskId'] == identifier
        assert responses['get']['result']['taskId'] == identifier
        assert responses['cancel']['result']['status'] == 'cancelled'
    finally:
        await client.close()
    assert not registry._tasks and registry._retained_bytes == 0


async def test_result_wait_flood_preserves_bounded_cancel_control_lane():
    owner, registry, sent = ReverseRequests(), TaskRegistry(), []
    owner.active, owner.tasks = True, registry
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            await release.wait()
        return {'private': 'NEVER PUBLISH'}
    async def write(value):
        sent.append(value)
    identifier = registry.create('sampling/createMessage', operation)['task']['taskId']
    def packet(rid, method):
        return {'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': {'taskId': identifier}}
    try:
        await entered.wait()
        for rid in range(4):
            assert owner.admit(packet(rid, 'tasks/result'), write, timeout=2)
        assert not owner.admit(packet(4, 'tasks/result'), write, timeout=2)
        assert owner.admit(packet('cancel', 'tasks/cancel'), write, timeout=2)
        await cleanup.wait()
        assert not any(row.get('id') == 'cancel' for row in sent)
        for rid in range(3):
            assert owner.admit(packet(f'get-{rid}', 'tasks/get'), write, timeout=2)
        assert not owner.admit(packet('over-control', 'tasks/get'), write, timeout=2)
        await until(lambda: len(sent) == 3)
        assert all(row['result']['status'] == 'working' for row in sent)
        release.set()
        await until(lambda: not owner._jobs)
        assert next(row for row in sent if row['id'] == 'cancel')['result']['status'] == 'cancelled'
        assert all(next(row for row in sent if row['id'] == rid)['error']['code'] == -32000 for rid in range(4))
        assert 'NEVER PUBLISH' not in json.dumps(sent)
    finally:
        release.set()
        await owner.close()


async def test_disconnect_signals_all_workers_before_held_cancel_job_settles():
    owner, registry, sent = ReverseRequests(), TaskRegistry(), []
    owner.active, owner.tasks = True, registry
    entered = [asyncio.Event(), asyncio.Event()]
    cleanup = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    def operation(index):
        async def run(identifier):
            entered[index].set()
            try:
                await asyncio.sleep(30)
            finally:
                cleanup[index].set()
                await release.wait()
            return {}
        return run
    identifiers = [registry.create('sampling/createMessage', operation(index))['task']['taskId']
                   for index in range(2)]
    async def write(value):
        sent.append(value)
    closing = None
    try:
        await asyncio.gather(*(event.wait() for event in entered))
        assert owner.admit({'jsonrpc': '2.0', 'id': 'cancel', 'method': 'tasks/cancel',
                            'params': {'taskId': identifiers[0]}}, write, timeout=2)
        await asyncio.wait_for(cleanup[0].wait(), 2)
        closing = asyncio.create_task(owner.close())
        await asyncio.wait_for(cleanup[1].wait(), 2)
        assert not closing.done() and not owner.active
        release.set()
        await asyncio.wait_for(closing, 2)
        assert not sent and not registry._tasks and not owner._jobs
    finally:
        release.set()
        if closing is not None:
            await closing
        else:
            await owner.close()


async def test_actual_http_sse_result_wait_and_cancel_posts_preserve_original_response():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from xdog.agent.mcp.http import HttpClient

    registry = TaskRegistry()
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            await release.wait()
        return {'private': 'NOT RELEASED'}
    identifier = registry.create('elicitation/create', operation)['task']['taskId']
    records, headers = [], []
    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def respond(self, status, value=None, *, frames=None):
            body = (b''.join(b'data: ' + json.dumps(row).encode() + b'\n\n' for row in frames)
                    if frames is not None else b'' if value is None else json.dumps(value).encode())
            self.send_response(status)
            self.send_header('Content-Type', 'text/event-stream' if frames is not None else 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            self.respond(405)
        def do_POST(self):
            row = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            records.append(row)
            headers.append(dict(self.headers))
            method, rid = row.get('method'), row.get('id')
            if method == 'server/discover':
                self.respond(400)
            elif method == 'initialize':
                self.respond(200, {'jsonrpc': '2.0', 'id': rid, 'result': {
                    'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}})
            elif method == 'tools/list':
                frames = [{'jsonrpc': '2.0', 'id': 'held-result', 'method': 'tasks/result',
                           'params': {'taskId': identifier}},
                          {'jsonrpc': '2.0', 'id': 'cancel', 'method': 'tasks/cancel',
                           'params': {'taskId': identifier}},
                          {'jsonrpc': '2.0', 'id': rid, 'result': {'tools': []}}]
                self.respond(200, frames=frames)
            elif method == 'ping':
                self.respond(200, {'jsonrpc': '2.0', 'id': rid, 'result': {}})
            else:
                self.respond(202)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Peer)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    client = HttpClient(McpServerConfig.parse('owned', {'transport': 'http',
        'url': f'http://127.0.0.1:{server.server_port}/mcp', 'timeout': 2}))
    client.reverse.tasks = registry
    try:
        await entered.wait()
        await client.connect()
        initial = next(row for row in records if row.get('method') == 'initialize')
        assert initial['params']['capabilities'] == {'tasks': {'list': {}, 'cancel': {}}}
        assert await client.request('tools/list', {}) == {'tools': []}
        await asyncio.wait_for(cleanup.wait(), 2)
        assert await client.request('ping', {}) == {}
        assert not any(row.get('id') in {'cancel', 'held-result'} for row in records)
        release.set()
        await until(lambda: sum(row.get('id') in {'cancel', 'held-result'} for row in records) == 2)
        responses = {row['id']: row for row in records if row.get('id') in {'cancel', 'held-result'}}
        assert responses['cancel']['result']['status'] == 'cancelled'
        assert responses['held-result']['error']['code'] == -32000
        assert 'NOT RELEASED' not in json.dumps(records)
        assert sum(row.get('method') == 'tools/list' for row in records) == 1
        assert all(header['Host'] == f'127.0.0.1:{server.server_port}' for header in headers)
    finally:
        release.set()
        await client.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        worker.join(3)
        assert not worker.is_alive()
    assert not registry._tasks and not client.reverse._jobs


async def test_cancel_result_read_does_not_cancel_original_operation():
    owner, registry, sent = ReverseRequests(), TaskRegistry(), []
    owner.active, owner.tasks = True, registry
    entered, release = asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        entered.set()
        await release.wait()
        return {'private': 'REVIEWED'}
    async def write(value):
        sent.append(value)
    identifier = registry.create('elicitation/create', operation)['task']['taskId']
    try:
        await entered.wait()
        assert owner.admit({'jsonrpc': '2.0', 'id': 'read', 'method': 'tasks/result',
                            'params': {'taskId': identifier}}, write, timeout=2)
        await asyncio.sleep(.01)
        owner.cancel({'method': 'notifications/cancelled', 'params': {'requestId': 'read'}})
        await until(lambda: not owner._jobs)
        assert sent[0]['error']['code'] == -32000
        assert registry.state(identifier)['status'] == 'working'
        release.set()
        await until(lambda: registry.state(identifier)['status'] == 'completed')
        assert registry.result(identifier) == {'private': 'REVIEWED'}
    finally:
        release.set()
        await owner.close()


async def test_cancelled_close_caller_waits_owned_cleanup_then_propagates():
    import pytest

    owner, registry, sent = ReverseRequests(), TaskRegistry(), []
    owner.active, owner.tasks = True, registry
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            await release.wait()
        return {}
    identifier = registry.create('sampling/createMessage', operation)['task']['taskId']
    async def write(value):
        sent.append(value)
    await entered.wait()
    assert owner.admit({'jsonrpc': '2.0', 'id': 'read', 'method': 'tasks/result',
                        'params': {'taskId': identifier}}, write, timeout=2)
    closing = asyncio.create_task(owner.close())
    try:
        await asyncio.wait_for(cleanup.wait(), 2)
        closing.cancel()
        await asyncio.sleep(.01)
        assert not closing.done() and not owner.active
        closing.cancel()
        await asyncio.sleep(.01)
        assert not closing.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 2)
    assert not registry._tasks and not owner._jobs and not sent


async def test_result_wait_timeout_leaves_original_task_live_and_new_read_can_obtain_result():
    owner, registry, sent = ReverseRequests(), TaskRegistry(), []
    owner.active, owner.tasks = True, registry
    release = asyncio.Event()
    async def operation(identifier):
        await release.wait()
        return {'private': 'REVIEWED ONCE'}
    async def write(value):
        sent.append(value)
    identifier = registry.create('elicitation/create', operation)['task']['taskId']
    def packet(rid):
        return {'jsonrpc': '2.0', 'id': rid, 'method': 'tasks/result', 'params': {'taskId': identifier}}
    try:
        assert owner.admit(packet('first'), write, timeout=.04)
        await until(lambda: not owner._jobs)
        assert sent[0]['error']['code'] == -32000
        assert registry.state(identifier)['status'] == 'working'
        release.set()
        await until(lambda: registry.state(identifier)['status'] == 'completed')
        assert not owner.admit(packet('first'), write, timeout=1)
        assert owner.admit(packet('new-read'), write, timeout=1)
        await until(lambda: not owner._jobs)
        assert sent[-1]['result'] == {'private': 'REVIEWED ONCE'}
    finally:
        release.set()
        await owner.close()


async def test_cancel_control_timeout_keeps_slot_until_finally_and_original_task_stops():
    owner, registry, sent = ReverseRequests(), TaskRegistry(), []
    owner.active, owner.tasks = True, registry
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            await release.wait()
        return {'private': 'NEVER RETURN'}
    async def write(value):
        sent.append(value)
    identifier = registry.create('sampling/createMessage', operation)['task']['taskId']
    try:
        await entered.wait()
        assert owner.admit({'jsonrpc': '2.0', 'id': 'cancel', 'method': 'tasks/cancel',
                            'params': {'taskId': identifier}}, write, timeout=.04)
        await asyncio.wait_for(cleanup.wait(), 2)
        await asyncio.sleep(.08)
        assert 'cancel' in owner._jobs and not sent
        assert registry._tasks[identifier].cancel_requested
        release.set()
        await until(lambda: not owner._jobs)
        assert sent[0]['error']['code'] == -32000
        assert registry.state(identifier)['status'] == 'cancelled'
        assert registry._retained_bytes == 0
    finally:
        release.set()
        await owner.close()


async def test_cancel_original_request_after_creation_delivery_cannot_cancel_detached_task():
    import pytest

    owner, registry, sent = ReverseRequests(), TaskRegistry(), []
    owner.active, owner.tasks = True, registry
    entered, release = asyncio.Event(), asyncio.Event()
    async def synchronous(value):
        pytest.fail('No synchronous task downgrade')
    async def operation(value, identifier, original):
        original.authorize(identifier, 'sampling/createMessage')
        entered.set()
        await release.wait()
        return {'model': 'offline', 'role': 'assistant', 'content': {'type': 'text', 'text': 'REVIEWED'}}
    async def write(value):
        sent.append(value)
    owner.handler, owner.sampling_task_handler = synchronous, operation
    from packages.agent.tests.test_mcp_sampling_owner import request
    source = request()
    source['params']['task'] = {}
    try:
        assert owner.admit(source, write, timeout=2)
        await asyncio.wait_for(entered.wait(), 2)
        await until(lambda: source['id'] not in owner._jobs)
        created = next(row for row in sent if row.get('id') == source['id'])['result']['task']['taskId']
        owner.cancel({'method': 'notifications/cancelled', 'params': {'requestId': source['id']}})
        await asyncio.sleep(.01)
        assert registry.state(created)['status'] == 'working' and not registry._tasks[created].cancel_requested
        assert owner.admit({'jsonrpc': '2.0', 'id': 'explicit-task-cancel', 'method': 'tasks/cancel',
                            'params': {'taskId': created}}, write, timeout=2)
        await until(lambda: any(row.get('id') == 'explicit-task-cancel' for row in sent))
        assert registry.state(created)['status'] == 'cancelled'
        assert not any('REVIEWED' in str(row) for row in sent)
    finally:
        release.set()
        await owner.close()
