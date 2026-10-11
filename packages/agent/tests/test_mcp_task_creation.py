"""Task creation uses the original registry worker AND independent exact human reviews."""
from __future__ import annotations

import copy

import pytest
from xdog.agent.mcp.elicitation_owner import ElicitationOwner
from xdog.agent.mcp.reverse_requests import ReverseRequests
from xdog.agent.mcp.sampling_owner import SamplingOwner
from xdog.agent.mcp.stdio import McpError
from xdog.agent.mcp.task_registry import TaskRegistry, TaskRegistryError

from packages.agent.tests.test_mcp_elicitation import form
from packages.agent.tests.test_mcp_sampling_owner import answer, request, selection, until


async def test_sampling_task_exact_envelope_reviews_input_required_and_result_release():
    wire, reviews, calls = [], [], []
    registry, reverse = TaskRegistry(), ReverseRequests()
    reverse.active, reverse.tasks = True, registry
    async def execute(value, chosen, cancel):
        calls.append(copy.deepcopy(value))
        return {'model': chosen['model'], 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE'}}
    owner = SamplingOwner('view', reviews.append, execute, lambda: True)
    reverse.handler, reverse.sampling_task_handler = owner.handle, owner.handle_task
    source = request()
    source['params']['task'] = {'ttl': 10000, 'pollInterval': 500}
    async def write(value):
        wire.append(value)
    try:
        assert reverse.advertised()['tasks']['requests'] == {'sampling': {'createMessage': {}}}
        assert reverse.admit(source, write, timeout=2)
        await until(lambda: wire and reviews)
        task_id = wire[0]['result']['task']['taskId']
        assert set(wire[0]['result']) == {'task'} and 'PRIVATE' not in str(wire)
        assert registry.state(task_id)['status'] == 'input_required' and not calls
        assert reviews[0]['body']['params']['task'] == source['params']['task']
        assert not reverse.admit(source, write, timeout=2)
        answer(owner, reviews[0], selection=selection())
        await until(lambda: len(reviews) == 2)
        assert registry.state(task_id)['status'] == 'input_required'
        assert 'task' not in calls[0]['params']  # Model execution only after original task request review.
        with pytest.raises(TaskRegistryError):
            registry.result(task_id)
        answer(owner, reviews[1])
        await until(lambda: registry.state(task_id)['status'] == 'completed')
        output = registry.result(task_id)
        assert output['content']['text'] == 'PRIVATE'
        assert output['_meta']['io.modelcontextprotocol/related-task'] == {'taskId': task_id}
        assert len(calls) == 1 and sum(row.get('id') == source['id'] for row in wire) == 1
    finally:
        await reverse.close()
        await owner.close()


async def test_elicitation_task_two_reviews_and_foreign_worker_cannot_borrow_identity():
    registry, reverse, reviews, wire = TaskRegistry(), ReverseRequests(), [], []
    reverse.active, reverse.tasks = True, registry
    owner = ElicitationOwner('view', reviews.append, lambda: True)
    reverse.elicitation_handler, reverse.elicitation_task_handler = owner.handle, owner.handle_task
    source = form()
    source['params']['task'] = {'ttl': 10000}
    async def write(value):
        wire.append(value)
    async def approve(event, response):
        await owner.answer(event['id'], lease='view', phase=event['phase'], receipt=event['receipt'],
                           sha256=event['sha256'], response=response)
    try:
        assert reverse.admit(source, write, timeout=2)
        await until(lambda: wire and reviews)
        task_id = wire[0]['result']['task']['taskId']
        assert registry.state(task_id)['status'] == 'input_required'
        assert reviews[0]['body']['params']['task'] == {'ttl': 10000}
        with pytest.raises(TaskRegistryError, match='original registered worker'):
            await owner.handle_task(source, task_id, registry)
        with pytest.raises(McpError) as error:
            await owner.handle(source)
        assert error.value.code == -32602
        await approve(reviews[0], {'action': 'accept', 'content': {'name': 'PRIVATE FORM'}})
        await until(lambda: len(reviews) == 2)
        assert reviews[1]['phase'] == 'response' and registry.state(task_id)['status'] == 'input_required'
        with pytest.raises(TaskRegistryError):
            registry.result(task_id)
        await approve(reviews[1], {'action': 'accept'})
        await until(lambda: registry.state(task_id)['status'] == 'completed')
        assert registry.result(task_id) == {'action': 'accept', 'content': {'name': 'PRIVATE FORM'}, '_meta': {
            'io.modelcontextprotocol/related-task': {'taskId': task_id}}}
    finally:
        await reverse.close()
        await owner.close()


@pytest.mark.parametrize('ttl', [None, 0, True, -1, 600001])
async def test_unsupported_retention_refuses_without_human_or_operation_admission(ttl):
    registry, reverse, calls, wire = TaskRegistry(), ReverseRequests(), [], []
    reverse.active, reverse.tasks = True, registry
    async def handler(*args):
        calls.append(args)
        raise AssertionError('Invalid task must not execute')
    reverse.handler, reverse.sampling_task_handler = handler, handler
    source = request()
    source['params']['task'] = {'ttl': ttl}
    async def write(value):
        wire.append(value)
    try:
        assert reverse.admit(source, write, timeout=2)
        await until(lambda: wire)
        assert wire[0]['error']['code'] == -32000
        assert not calls and not registry.list()['tasks']
    finally:
        await reverse.close()


async def test_related_task_forgery_refused_even_when_creation_registered():
    registry, reverse, calls = TaskRegistry(), ReverseRequests(), []
    reverse.active, reverse.tasks = True, registry
    async def handler(*args):
        calls.append(args)
        return {}
    reverse.handler, reverse.sampling_task_handler = handler, handler
    source = request()
    source['params'].update(task={}, _meta={'io.modelcontextprotocol/related-task': {'taskId': 'foreign'}})
    try:
        assert not reverse.admit(source, handler, timeout=2)
        assert not calls and not registry._tasks and not reverse._jobs
    finally:
        await reverse.close()


@pytest.mark.parametrize('withdraw', ['sampling', 'elicitation', 'disconnect'])
async def test_actual_manager_stdio_task_negotiation_reviews_and_family_retirement(tmp_path, withdraw):
    import json
    import sys

    from xdog.agent.mcp import McpManager

    script, log = tmp_path / 'task_peer.py', tmp_path / 'frames'
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
        send({'jsonrpc':'2.0','id':row['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}})
    elif method=='tools/list':
        send({'jsonrpc':'2.0','id':'sample','method':'sampling/createMessage','params':{
            'messages':[],'maxTokens':10,'task':{'ttl':10000}}})
        send({'jsonrpc':'2.0','id':'form','method':'elicitation/create','params':{
            'message':'Review data','requestedSchema':{'type':'object','properties':{
                'name':{'type':'string'}},'required':['name']},'task':{}}})
        send({'jsonrpc':'2.0','id':row['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':row['id'],'result':{}})
''')
    manager = McpManager({'owned': {'command': sys.executable, 'args': [str(script), str(log)], 'timeout': 3}},
                         tmp_path)
    sampling, elicitation, calls = [], [], []
    async def execute(*args):
        calls.append(args)
        raise AssertionError('No model without independent consent')
    def frames():
        return [json.loads(raw) for raw in log.read_text().splitlines()]
    try:
        await manager.register_sampling('owned', lease='view', publish=sampling.append,
                                        execute=execute, alive=lambda: True, tasks=True)
        await manager.register_elicitation('owned', lease='form-view', publish=elicitation.append,
                                          alive=lambda: True, tasks=True)
        await manager.connect('owned')
        client = manager._clients['owned']
        await until(lambda: sampling and elicitation and
                    sum(row.get('id') in {'sample', 'form'} for row in frames()) == 2)
        initial = next(row for row in frames() if row.get('method') == 'initialize')
        assert initial['params']['capabilities']['tasks']['requests'] == {
            'sampling': {'createMessage': {}}, 'elicitation': {'create': {}}}
        registry = client.reverse.tasks
        assert registry is not None
        created = {row['id']: row['result']['task']['taskId'] for row in frames()
                   if row.get('id') in {'sample', 'form'}}
        for identifier in created.values():
            state = await manager._bridge(_state(registry, identifier))
            assert state['status'] == 'input_required'
        assert not calls and 'PRIVATE' not in str(frames())
        assert await manager._bridge(client.request('ping', {})) == {}
        if withdraw == 'disconnect':
            await manager.disconnect('owned')
            assert not registry._tasks and not manager._sampling and not manager._elicitation
        elif withdraw == 'sampling':
            await manager.detach_sampling('owned', lease='view')
            assert client.reverse.elicitation_task_handler is not None
            assert created['sample'] not in registry._tasks and created['form'] in registry._tasks
            event = elicitation[0]
            await manager.answer_elicitation('owned', event['id'], lease='form-view', phase=event['phase'],
                                            receipt=event['receipt'], sha256=event['sha256'],
                                            response={'action': 'decline'})
            await until(lambda: registry._tasks[created['form']].status == 'completed')
            assert client.reverse.advertised()['tasks']['requests'] == {'elicitation': {'create': {}}}
        else:
            await manager.detach_elicitation('owned', lease='form-view')
            assert created['form'] not in registry._tasks and created['sample'] in registry._tasks
            event = sampling[0]
            await manager.answer_sampling('owned', event['id'], lease='view', phase=event['phase'],
                                          receipt=event['receipt'], sha256=event['sha256'], approve=False)
            await until(lambda: registry._tasks[created['sample']].status == 'failed')
            assert client.reverse.advertised()['tasks']['requests'] == {'sampling': {'createMessage': {}}}
        assert not calls
        if withdraw != 'disconnect':
            assert await manager._bridge(client.request('ping', {})) == {}
            assert client.reverse._used >= {'sample', 'form'}
    finally:
        await manager.aclose()


async def _state(registry, identifier):
    return registry.state(identifier)


async def test_older_negotiated_protocol_withdraws_registry_but_keeps_synchronous_reviews():
    reverse, registry = ReverseRequests(), TaskRegistry()
    reverse.active, reverse.tasks = True, registry
    async def handler(*args):
        return {}
    reverse.handler, reverse.sampling_task_handler = handler, handler
    await reverse.negotiated('2025-06-18')
    assert reverse.tasks is None and reverse.sampling_task_handler is None
    assert reverse.advertised() == {'sampling': {}}
    source = request()
    source['params']['task'] = {}
    assert not reverse.admit(source, handler, timeout=2)
    assert registry._closed
    await reverse.close()


@pytest.mark.parametrize('end', ['release', 'expire', 'delivery_error'])
async def test_actual_http_sse_task_creation_and_result_require_both_form_reviews(end):
    import asyncio
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from xdog.agent.mcp.http import HttpClient

    source = form()
    source['params']['task'] = {'ttl': 10000 if end == 'release' else 300, 'pollInterval': 500}
    records, reviews = [], []
    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def respond(self, status, value=None, frames=None):
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
            method, rid = row.get('method'), row.get('id')
            if method == 'server/discover':
                self.respond(400)
            elif method == 'initialize':
                self.respond(200, {'jsonrpc': '2.0', 'id': rid, 'result': {
                    'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}})
            elif method == 'tools/list':
                self.respond(200, frames=[source, {'jsonrpc': '2.0', 'id': rid, 'result': {'tools': []}}])
            elif method == 'ping':
                created = next(row for row in records if row.get('id') == 'elicitation')['result']['task']
                self.respond(200, frames=[{'jsonrpc': '2.0', 'id': 'retrieve', 'method': 'tasks/result',
                    'params': {'taskId': created['taskId']}}, {'jsonrpc': '2.0', 'id': rid, 'result': {}}])
            elif rid == 'elicitation' and end == 'delivery_error':
                self.respond(500, {'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32000}})
            else:
                self.respond(202)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Peer)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    from xdog.agent.mcp.config import McpServerConfig
    client = HttpClient(McpServerConfig.parse('owned', {'transport': 'http',
        'url': f'http://127.0.0.1:{server.server_port}/mcp', 'timeout': 2}))
    registry = TaskRegistry()
    owner = ElicitationOwner('view', reviews.append, lambda: True)
    client.reverse.tasks = registry
    client.reverse.elicitation_handler, client.reverse.elicitation_task_handler = owner.handle, owner.handle_task
    async def approve(event, response):
        await owner.answer(event['id'], lease='view', phase=event['phase'], receipt=event['receipt'],
                           sha256=event['sha256'], response=response)
    try:
        await client.connect()
        assert await client.request('tools/list', {}) == {'tools': []}
        if end == 'delivery_error':
            await until(lambda: any(row.get('id') == 'elicitation' for row in records))
            await until(lambda: not registry._tasks and not client.reverse._jobs)
            assert registry._retained_bytes == 0 and not owner._jobs
            assert sum(row.get('id') == 'elicitation' for row in records) == 1
            assert not any(row.get('id') == 'retrieve' for row in records)
            return
        await until(lambda: reviews and any(row.get('id') == 'elicitation' for row in records))
        created = next(row for row in records if row.get('id') == 'elicitation')['result']['task']
        assert registry.state(created['taskId'])['status'] == 'input_required'
        assert await client.request('ping', {}) == {}  # tasks/result wait does not hold original SSE response.
        assert not any(row.get('id') == 'retrieve' for row in records)
        if end == 'expire':
            await until(lambda: not registry._tasks and not registry._expiring)
            await until(lambda: any(row.get('id') == 'retrieve' for row in records))
            retrieved = next(row for row in records if row.get('id') == 'retrieve')
            assert retrieved['error']['code'] == -32000 and 'result' not in retrieved
            assert registry._retained_bytes == 0
            assert not any(row.get('method') == 'tools/call' for row in records)
            return
        await approve(reviews[0], {'action': 'accept', 'content': {'name': 'PRIVATE FORM'}})
        await until(lambda: len(reviews) == 2)
        assert not any(row.get('id') == 'retrieve' for row in records)
        await approve(reviews[1], {'action': 'accept'})
        await until(lambda: any(row.get('id') == 'retrieve' for row in records))
        retrieved = next(row for row in records if row.get('id') == 'retrieve')['result']
        assert retrieved == {'action': 'accept', 'content': {'name': 'PRIVATE FORM'}, '_meta': {
            'io.modelcontextprotocol/related-task': {'taskId': created['taskId']}}}
        await until(lambda: any(row.get('method') == 'notifications/tasks/status' for row in records))
        notices = [row for row in records if row.get('method') == 'notifications/tasks/status']
        assert all(row['params']['taskId'] == created['taskId'] and 'id' not in row for row in notices)
        assert 'PRIVATE' not in json.dumps(notices)
        assert sum(row.get('method') == 'tools/list' for row in records) == 1
        assert not any(row.get('method') in {'tools/call', 'sampling/createMessage'} for row in records)
    finally:
        await client.close()
        await owner.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        worker.join(3)
        assert not worker.is_alive()
    assert not registry._tasks and not client.reverse._jobs


async def test_task_family_withdrawal_survives_repeated_control_cancellation_and_retires_result():
    import asyncio

    registry, reverse, wire = TaskRegistry(), ReverseRequests(), []
    reverse.active, reverse.tasks = True, registry
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def synchronous(value):
        raise AssertionError('Task must never invoke synchronous handler')
    async def operation(value, identifier, original):
        original.authorize(identifier, 'sampling/createMessage')
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            await release.wait()
        return {'model': 'offline', 'role': 'assistant', 'content': {'type': 'text', 'text': 'WITHHELD'}}
    reverse.handler, reverse.sampling_task_handler = synchronous, operation
    source = request()
    source['params']['task'] = {}
    async def write(value):
        wire.append(value)
    assert reverse.admit(source, write, timeout=2)
    await until(lambda: wire and entered.is_set())
    identifier = wire[0]['result']['task']['taskId']
    withdrawal = asyncio.create_task(reverse.withdraw('sampling/createMessage'))
    try:
        await asyncio.wait_for(cleanup.wait(), 2)
        withdrawal.cancel()
        await asyncio.sleep(.01)
        withdrawal.cancel()
        await asyncio.sleep(.01)
        assert not withdrawal.done() and identifier in registry._tasks
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(withdrawal, 2)
        await reverse.close()
    assert not registry._tasks and sum(row.get('id') == source['id'] for row in wire) == 1
    assert 'WITHHELD' not in str(wire)


@pytest.mark.parametrize('failure', ['error', 'timeout', 'cancel'])
async def test_undelivered_creation_settles_original_worker_and_retires_task_before_slot_release(failure):
    import asyncio

    reverse, registry = ReverseRequests(), TaskRegistry()
    reverse.active, reverse.tasks = True, registry
    entered, writing, cleanup, release = (asyncio.Event() for _ in range(4))
    sent = []
    async def handler(value):
        pytest.fail('Task creation cannot invoke ordinary execution')
    async def operation(value, identifier, original):
        original.authorize(identifier, 'sampling/createMessage')
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cleanup.set()
            await release.wait()
            return {'model': 'offline', 'role': 'assistant', 'content': {'type': 'text', 'text': 'LATE PRIVATE'}}
    async def write(value):
        sent.append(value)
        writing.set()
        await entered.wait()
        if failure == 'error':
            raise OSError('PRIVATE TRANSPORT DETAIL')
        await asyncio.sleep(30)
    reverse.handler, reverse.sampling_task_handler = handler, operation
    source = request()
    source['params']['task'] = {'ttl': 10000}
    try:
        assert reverse.admit(source, write, timeout=.05)
        await asyncio.wait_for(writing.wait(), 2)
        await asyncio.wait_for(entered.wait(), 2)
        if failure == 'cancel':
            reverse.cancel({'method': 'notifications/cancelled', 'params': {'requestId': source['id']}})
        await asyncio.wait_for(cleanup.wait(), .5)
        identifier = sent[0]['result']['task']['taskId']
        assert source['id'] in reverse._jobs and identifier in registry._tasks
        assert registry._tasks[identifier].cancel_requested
        # Repeated cancellation cannot interrupt original operation finally.
        reverse.cancel({'method': 'notifications/cancelled', 'params': {'requestId': source['id']}})
        await asyncio.sleep(.01)
        assert source['id'] in reverse._jobs and identifier in registry._tasks
        release.set()
        await until(lambda: not reverse._jobs)
        assert not registry._tasks and registry._retained_bytes == 0
        assert len(sent) == 1 and 'LATE PRIVATE' not in str(sent)
        assert not reverse.admit(source, write, timeout=.05)  # No automatic replay.
    finally:
        release.set()
        await reverse.close()


async def test_failed_creation_delivery_erases_even_already_completed_private_result():
    import asyncio

    reverse, registry = ReverseRequests(), TaskRegistry()
    reverse.active, reverse.tasks = True, registry
    completed = asyncio.Event()
    async def handler(value):
        pytest.fail('Task cannot downgrade')
    async def operation(value, identifier, original):
        original.authorize(identifier, 'sampling/createMessage')
        return {'model': 'offline', 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE COMPLETED'}}
    async def write(value):
        identifier = value['result']['task']['taskId']
        await until(lambda: registry.state(identifier)['status'] == 'completed')
        assert registry._retained_bytes > 0
        completed.set()
        raise OSError('Undelivered creation')
    reverse.handler, reverse.sampling_task_handler = handler, operation
    source = request()
    source['params']['task'] = {}
    try:
        assert reverse.admit(source, write, timeout=2)
        await asyncio.wait_for(completed.wait(), 2)
        await until(lambda: not reverse._jobs)
        assert not registry._tasks and registry._retained_bytes == 0
    finally:
        await reverse.close()
