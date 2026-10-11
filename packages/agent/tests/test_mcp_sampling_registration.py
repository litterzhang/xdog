"""Explicit manager registration routes two-phase reviews on the owning MCP loop."""
from __future__ import annotations

import asyncio
import json
import sys
import threading

import pytest
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.sampling_executor import SamplingExecutor
from xdog.agent.mcp.stdio import McpError
from xdog.ai.types import AssistantMessage, TextContent

from packages.agent.tests.test_mcp_sampling_executor import Adapter


def config(tmp_path):
    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    peer.write_text('''import json,sys
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
            'messages':[{'role':'user','content':{'type':'text','text':'SERVER INPUT'}}],'maxTokens':20}})
        send({'jsonrpc':'2.0','id':row['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':row['id'],'result':{}})
''')
    return {'owned': {'command': sys.executable, 'args': [str(peer), str(log)], 'timeout': 5}}, log


async def wait(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.002)


@pytest.mark.parametrize('end', ['release', 'detach', 'disconnect'])
async def test_actual_manager_registration_review_routing_release_or_revocation(tmp_path, end):
    configs, log = config(tmp_path)
    manager = McpManager(configs, tmp_path)
    events = []
    adapter = Adapter(AssistantMessage(content=(TextContent(text='GENERATED PRIVATE'),)))
    executor = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    try:
        await manager.register_sampling('owned', lease='view', publish=events.append,
                                        execute=executor, alive=lambda: True)
        assert not log.exists()  # Registration is not connection or model execution.
        await manager.connect('owned')
        client = manager._clients['owned']
        await wait(lambda: events)
        first = events[0]
        assert first['server'] == 'owned' and first['lease'] == 'view' and first['phase'] == 'input'
        assert not adapter.calls and manager.status()[0]['client_capabilities'] == {'sampling': {'tools': {}}}
        with pytest.raises(McpError, match='disconnected'):
            await manager.register_sampling('owned', lease='another', publish=events.append,
                                            execute=executor, alive=lambda: True)
        with pytest.raises(McpError, match='owner changed'):
            await manager.answer_sampling('owned', first['id'], lease='foreign', phase='input',
                                          receipt=first['receipt'], sha256=first['sha256'], approve=True,
                                          selection={'model': 'explicit-model', 'max_tokens': 10,
                                                     'context': 'none', 'tools': []})
        await manager._bridge(client.request('ping', {}))
        if end == 'release':
            await manager.answer_sampling('owned', first['id'], lease='view', phase='input',
                                          receipt=first['receipt'], sha256=first['sha256'], approve=True,
                                          selection={'model': 'explicit-model', 'max_tokens': 10,
                                                     'context': 'none', 'tools': []})
            await wait(lambda: len(events) >= 2)
            output = events[1]
            assert output['phase'] == 'output' and len(adapter.calls) == 1
            before = [json.loads(row) for row in log.read_text().splitlines()]
            assert not any(row.get('id') == 'sample' for row in before)
            await manager.answer_sampling('owned', output['id'], lease='view', phase='output',
                                          receipt=output['receipt'], sha256=output['sha256'], approve=True)
            await wait(lambda: not client.reverse._jobs)
            await manager._bridge(client.request('ping', {}))
            rows = [json.loads(row) for row in log.read_text().splitlines()]
            delivered = [row for row in rows if row.get('id') == 'sample']
            assert len(delivered) == 1 and delivered[0]['result']['content']['text'] == 'GENERATED PRIVATE'
        else:
            if end == 'detach':
                await manager.detach_sampling('owned', lease='foreign')
                assert manager._sampling  # Foreign view cannot revoke owning review.
                await manager.detach_sampling('owned', lease='view')
                assert manager.status()[0]['client_capabilities'] == {}
                assert await manager._bridge(client.request('ping', {})) == {}
            else:
                await manager.disconnect('owned')
                assert client.process is None
            assert not manager._sampling and not client.reverse._jobs and not adapter.calls
            with pytest.raises(McpError, match='owner changed'):
                await manager.answer_sampling('owned', first['id'], lease='view', phase='input',
                                              receipt=first['receipt'], sha256=first['sha256'], approve=True)
    finally:
        await manager.aclose()


async def test_detach_keeps_registration_owned_until_executor_cleanup_actually_finishes(tmp_path):
    configs, _ = config(tmp_path)
    manager, events = McpManager(configs, tmp_path), []
    entered, cleanup, release = threading.Event(), threading.Event(), threading.Event()
    async def execute(value, chosen, cancel):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            while not release.is_set():
                await asyncio.sleep(.002)
    work = None
    try:
        await manager.register_sampling('owned', lease='view', publish=events.append,
                                        execute=execute, alive=lambda: True)
        await manager.connect('owned')
        await wait(lambda: events)
        event = events[0]
        await manager.answer_sampling('owned', event['id'], lease='view', phase='input',
            receipt=event['receipt'], sha256=event['sha256'], approve=True,
            selection={'model': 'explicit-model', 'max_tokens': 10, 'context': 'none', 'tools': []})
        assert await asyncio.to_thread(entered.wait, 2)
        work = asyncio.create_task(manager.detach_sampling('owned', lease='view'))
        assert await asyncio.to_thread(cleanup.wait, 2)
        assert not work.done() and manager._sampling['owned'][2].is_set()
        release.set()
        await asyncio.wait_for(work, 2)
        assert not manager._sampling and not manager._clients['owned'].reverse._jobs
        assert not any(event.get('phase') == 'output' for event in events)
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await manager.aclose()


async def test_disconnect_drops_registration_and_reconnect_never_replays_sampling_consent(tmp_path):
    configs, log = config(tmp_path)
    manager, events = McpManager(configs, tmp_path), []
    executor = SamplingExecutor(Adapter(), ('explicit-model',), lambda: True)
    try:
        await manager.register_sampling('owned', lease='old', publish=events.append,
                                        execute=executor, alive=lambda: True)
        await manager.connect('owned')
        await wait(lambda: events)
        await manager.disconnect('owned')
        assert not manager._sampling
        assert manager.status()[0]['client_capability_support']['sampling'] == 'unsupported'
        count = len(events)
        await manager.connect('owned')
        client = manager._clients['owned']
        await manager._bridge(client.request('ping', {}))
        assert len(events) == count and client.reverse.handler is None
        rows = [json.loads(row) for row in log.read_text().splitlines()]
        initializes = [row for row in rows if row.get('method') == 'initialize']
        assert initializes[0]['params']['capabilities'] == {'sampling': {'tools': {}}}
        assert initializes[-1]['params']['capabilities'] == {}
        assert not manager._sampling and manager.status()[0]['client_capabilities'] == {}
    finally:
        await manager.aclose()


async def test_failed_connection_retires_sampling_registration_without_executor_entry(tmp_path):
    configs, _ = config(tmp_path)
    configs['owned']['command'] = str(tmp_path / 'missing-executable')
    manager, events, calls = McpManager(configs, tmp_path), [], []
    async def execute(*args):
        calls.append(args)
        raise AssertionError('No executor on failed connection')
    try:
        await manager.register_sampling('owned', lease='view', publish=events.append,
                                        execute=execute, alive=lambda: True)
        with pytest.raises(OSError):
            await manager.connect('owned')
        assert not manager._sampling and not manager._clients and not calls and not events
    finally:
        await manager.aclose()


async def test_manager_real_peer_extra_context_needs_separate_review_before_adapter_execution(tmp_path):
    configs, log = config(tmp_path)
    peer = tmp_path / 'peer.py'
    peer.write_text(peer.read_text().replace("'maxTokens':20", "'maxTokens':20,'includeContext':'thisServer'"))
    manager, events, captures = McpManager(configs, tmp_path), [], []
    adapter = Adapter()
    executor = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    rows = [{'role': 'user', 'content': {'type': 'text', 'text': 'APPROVED EXTRA SNAPSHOT'}}]
    async def context(scope):
        captures.append(scope)
        return rows
    async def approve(event, selection=None):
        await manager.answer_sampling('owned', event['id'], lease='view', phase=event['phase'],
                                      receipt=event['receipt'], sha256=event['sha256'], approve=True,
                                      selection=selection)
    try:
        await manager.register_sampling('owned', lease='view', publish=events.append, execute=executor,
                                        alive=lambda: True, context=context)
        await manager.connect('owned')
        await wait(lambda: events)
        assert not adapter.calls and not captures
        await approve(events[0], {'model': 'explicit-model', 'max_tokens': 10, 'context': 'thisServer', 'tools': []})
        await wait(lambda: len(events) == 2)
        assert events[1]['phase'] == 'context' and not adapter.calls
        assert captures == ['thisServer']
        assert 'APPROVED EXTRA SNAPSHOT' in str(events[1]['body'])
        rows[0]['content']['text'] = 'UNAPPROVED LATER CHANGE'
        await approve(events[1])
        await wait(lambda: len(events) == 3)
        assert events[2]['phase'] == 'output' and len(adapter.calls) == 1
        actual = adapter.calls[0][1].messages
        assert actual[0].content[0].text == 'APPROVED EXTRA SNAPSHOT' and len(actual) == 2
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        assert not any(row.get('id') == 'sample' for row in frames)
        await approve(events[2])
        await wait(lambda: not manager._clients['owned'].reverse._jobs)
        await manager._bridge(manager._clients['owned'].request('ping', {}))
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        assert next(row for row in frames if row.get('id') == 'sample')['result']['content']['text'] == 'MODEL OUTPUT'
        assert captures == ['thisServer']
    finally:
        await manager.aclose()
