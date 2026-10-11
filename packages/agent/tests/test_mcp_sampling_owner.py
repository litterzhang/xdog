"""Input execution and output disclosure independently need original-owner consent."""
from __future__ import annotations

import asyncio
import copy

import pytest
from xdog.agent.mcp.sampling_owner import SamplingOwner
from xdog.agent.mcp.stdio import McpError


def request():
    return {'jsonrpc': '2.0', 'id': 'server', 'method': 'sampling/createMessage', 'params': {
        'messages': [{'role': 'user', 'content': {'type': 'text', 'text': 'UNTRUSTED REQUEST'}}],
        'maxTokens': 20}}


def selection():
    return {'model': 'explicit-model', 'max_tokens': 10, 'context': 'none', 'tools': []}


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.001)


def answer(owner, event, **kwargs):
    owner.answer(event['id'], lease='view', phase=event['phase'], receipt=event['receipt'],
                 sha256=event['sha256'], approve=True, **kwargs)


async def test_two_phase_no_execution_before_review_no_output_before_release_and_single_use():
    events, calls = [], []
    async def execute(value, chosen, cancel):
        calls.append((copy.deepcopy(value), copy.deepcopy(chosen)))
        return {'model': chosen['model'], 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE OUTPUT'}}
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    work = asyncio.create_task(owner.handle(request()))
    try:
        await until(lambda: events)
        review = events[0]
        assert review['phase'] == 'input' and not calls and not work.done()
        with pytest.raises(McpError, match='owner/phase'):
            owner.answer(review['id'], lease='foreign', phase='input', receipt=review['receipt'],
                         sha256=review['sha256'], approve=True, selection=selection())
        answer(owner, review, selection=selection())
        with pytest.raises(McpError):
            answer(owner, review, selection=selection())
        await until(lambda: len(events) == 2)
        assert events[1]['phase'] == 'output' and 'PRIVATE OUTPUT' in str(events[1]['body'])
        assert len(calls) == 1 and not work.done()
        assert calls[0][0]['params']['messages'][0]['content']['text'] == 'UNTRUSTED REQUEST'
        answer(owner, events[1])
        value = await asyncio.wait_for(work, 2)
        assert value['content']['text'] == 'PRIVATE OUTPUT'
        assert events[-1] == {'type': 'sampling_closed', 'id': review['id']}
        assert not owner._jobs
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('phase', ['input', 'output'])
async def test_denied_phase_never_releases_output_or_replays_request(phase):
    events, calls = [], []
    async def execute(value, chosen, cancel):
        calls.append(value)
        return {'model': chosen['model'], 'role': 'assistant', 'content': {'type': 'text', 'text': 'SECRET OUTPUT'}}
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    work = asyncio.create_task(owner.handle(request()))
    try:
        await until(lambda: events)
        if phase == 'output':
            answer(owner, events[0], selection=selection())
            await until(lambda: len(events) == 2)
        event = events[-1]
        owner.answer(event['id'], lease='view', phase=phase, receipt=event['receipt'],
                     sha256=event['sha256'], approve=False)
        with pytest.raises(McpError, match='denied'):
            await work
        assert len(calls) == (1 if phase == 'output' else 0)
        assert not owner._jobs and 'SECRET OUTPUT' not in str(events[-1])
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('change', [{'context': 'allServers'}, {'tools': ['forged']},
                                  {'max_tokens': 21}, {'max_tokens': True}])
async def test_explicit_selection_cannot_expand_request_ceiling(change):
    events, calls = [], []
    async def execute(*args):
        calls.append(args)
        raise AssertionError('Should not execute')
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    work = asyncio.create_task(owner.handle(request()))
    try:
        await until(lambda: events)
        with pytest.raises(McpError):
            answer(owner, events[0], selection={**selection(), **change})
        assert not calls and not work.done()
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_close_during_execution_waits_executor_finally_and_withholds_output():
    events = []
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def execute(value, chosen, cancel):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            await release.wait()
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    work = asyncio.create_task(owner.handle(request()))
    closing = None
    try:
        await until(lambda: events)
        answer(owner, events[0], selection=selection())
        await entered.wait()
        closing = asyncio.create_task(owner.close())
        await cleanup.wait()
        assert not closing.done() and owner._jobs
        release.set()
        await asyncio.wait_for(closing, 2)
        assert not owner._jobs and not any(e.get('phase') == 'output' for e in events)
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('failure', ['wrong-model', 'unselected-tool'])
async def test_executor_cannot_bypass_selected_model_or_reenable_rejected_tool(failure):
    events = []
    async def execute(value, chosen, cancel):
        if failure == 'wrong-model':
            return {'model': 'different', 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE'}}
        return {'model': chosen['model'], 'role': 'assistant', 'content': [
            {'type': 'tool_use', 'name': 'proposed', 'id': 'call', 'input': {}}]}
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    source = request()
    source['params']['tools'] = [{'name': 'proposed', 'inputSchema': {'type': 'object'}}]
    work = asyncio.create_task(owner.handle(source))
    try:
        await until(lambda: events)
        answer(owner, events[0], selection=selection())
        with pytest.raises(McpError):
            await work
        assert not any(e.get('phase') == 'output' for e in events)
        assert not owner._jobs
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_review_deadline_and_revoked_host_have_no_executor_entry():
    events, calls = [], []
    async def execute(*args):
        calls.append(args)
        return {}
    owner = SamplingOwner('view', events.append, execute, lambda: True, timeout=.1)
    with pytest.raises(TimeoutError):
        await owner.handle(request())
    assert not calls and not owner._jobs
    await owner.close()


async def test_host_revocation_interrupts_running_executor_and_awaits_its_real_cleanup():
    events, live = [], [True]
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def execute(value, chosen, cancel):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            assert cancel.is_set()
            cleaning.set()
            await release.wait()
    owner = SamplingOwner('view', events.append, execute, lambda: live[0])
    work = asyncio.create_task(owner.handle(request()))
    try:
        await until(lambda: events)
        answer(owner, events[0], selection=selection())
        await asyncio.wait_for(entered.wait(), 2)
        live[0] = False
        await asyncio.wait_for(cleaning.wait(), 2)
        assert not work.done() and owner._jobs
        release.set()
        with pytest.raises(McpError, match='revoked'):
            await asyncio.wait_for(work, 2)
        assert not owner._jobs and not any(e.get('phase') == 'output' for e in events)
    finally:
        release.set()
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('deny', [False, True])
async def test_actual_stdio_peer_gets_no_unreviewed_input_execution_or_output_release(tmp_path, deny):
    import json
    import sys

    from xdog.agent.mcp.config import McpServerConfig
    from xdog.agent.mcp.stdio import StdioClient
    peer, log = tmp_path / 'peer.py', tmp_path / 'peer-log'
    peer.write_text('''import json,sys
from pathlib import Path
log=Path(sys.argv[1])
def send(value): print(json.dumps(value),flush=True)
for raw in sys.stdin:
    row=json.loads(raw)
    with log.open('a') as file:file.write(json.dumps(row)+'\\n')
    method=row.get('method')
    if method=='server/discover':
        send({'jsonrpc':'2.0','id':row['id'],'error':{'code':-32601,'message':'legacy'}})
    elif method=='initialize':
        assert row['params']['capabilities']=={'sampling':{}}
        send({'jsonrpc':'2.0','id':row['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}})
    elif method=='tools/list':
        send({'jsonrpc':'2.0','id':'owned-sample','method':'sampling/createMessage','params':{
            'messages':[{'role':'user','content':{'type':'text','text':'UNTRUSTED REQUEST'}}],'maxTokens':20}})
        send({'jsonrpc':'2.0','id':row['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':row['id'],'result':{}})
''')
    events, calls = [], []
    async def execute(value, chosen, cancel):
        calls.append(value)
        return {'model': chosen['model'], 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE OUTPUT'}}
    owner = SamplingOwner('view', events.append, execute, lambda: True, timeout=5)
    client = StdioClient(McpServerConfig.parse('owned', {'command': sys.executable,
        'args': [str(peer), str(log)], 'timeout': 5}), tmp_path)
    client.reverse.handler = owner.handle
    def receipts():
        return [json.loads(line) for line in log.read_text().splitlines() if 'owned-sample' in line]
    try:
        await client.connect()
        assert await client.request('tools/list', {}) == {'tools': []}
        await until(lambda: events)
        assert not calls and not receipts()
        assert await client.request('ping', {}) == {}  # Independent reader while waiting on input review.
        answer(owner, events[0], selection=selection())
        await until(lambda: any(event.get('phase') == 'output' for event in events))
        output = next(event for event in events if event.get('phase') == 'output')
        assert len(calls) == 1 and not receipts()
        assert await client.request('ping', {}) == {}
        if deny:
            owner.answer(output['id'], lease='view', phase='output', receipt=output['receipt'],
                         sha256=output['sha256'], approve=False)
        else:
            answer(owner, output)
        await until(lambda: not client.reverse._jobs)
        await client.request('ping', {})
        response = receipts()
        assert len(response) == 1
        if deny:
            assert response[0]['error']['code'] == -32000 and 'PRIVATE OUTPUT' not in str(response)
        else:
            assert response[0]['result']['content']['text'] == 'PRIVATE OUTPUT'
        assert not owner._jobs
    finally:
        await client.close()
        await owner.close()


@pytest.mark.parametrize('approve_context', [False, True])
async def test_exact_extra_context_requires_separate_review_and_executes_frozen_snapshot(approve_context):
    events, calls, reads = [], [], []
    context_rows = [{'role': 'user', 'content': {'type': 'text', 'text': 'EXPLICIT CONTEXT'}}]
    async def context(scope):
        reads.append(scope)
        return context_rows
    async def execute(value, chosen, cancel):
        calls.append((copy.deepcopy(value), copy.deepcopy(chosen)))
        return {'model': chosen['model'], 'role': 'assistant', 'content': {'type': 'text', 'text': 'RESULT'}}
    source = request()
    source['params']['includeContext'] = 'thisServer'
    owner = SamplingOwner('view', events.append, execute, lambda: True, context=context)
    work = asyncio.create_task(owner.handle(source))
    try:
        await until(lambda: events)
        answer(owner, events[0], selection={**selection(), 'context': 'thisServer'})
        await until(lambda: len(events) == 2)
        disclosure = events[1]
        assert disclosure['phase'] == 'context' and not calls and reads == ['thisServer']
        assert 'EXPLICIT CONTEXT' in str(disclosure['body'])
        # Mutation after capture must neither reread context nor change execution.
        context_rows[0]['content']['text'] = 'LATER UNREVIEWED CONTEXT'
        if not approve_context:
            owner.answer(disclosure['id'], lease='view', phase='context', receipt=disclosure['receipt'],
                         sha256=disclosure['sha256'], approve=False)
            with pytest.raises(McpError, match='denied'):
                await work
            assert not calls
        else:
            answer(owner, disclosure)
            await until(lambda: len(events) == 3)
            assert calls[0][1]['context'] == 'none'  # No post-consent resolver call.
            assert calls[0][0]['params']['messages'][0]['content']['text'] == 'EXPLICIT CONTEXT'
            assert reads == ['thisServer']
            answer(owner, events[2])
            assert (await work)['content']['text'] == 'RESULT'
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_extra_context_without_provider_refuses_before_execution():
    events, calls = [], []
    async def execute(*args):
        calls.append(args)
        return {}
    source = request()
    source['params']['includeContext'] = 'allServers'
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    work = asyncio.create_task(owner.handle(source))
    try:
        await until(lambda: events)
        answer(owner, events[0], selection={**selection(), 'context': 'allServers'})
        with pytest.raises(McpError, match='provider unavailable'):
            await work
        assert not calls
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_host_loss_during_context_capture_waits_for_resolver_cleanup_without_execution():
    events, calls, live = [], [], [True]
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def context(scope):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            await release.wait()
    async def execute(*args):
        calls.append(args)
        return {}
    source = request()
    source['params']['includeContext'] = 'thisServer'
    owner = SamplingOwner('view', events.append, execute, lambda: live[0], context=context)
    work = asyncio.create_task(owner.handle(source))
    try:
        await until(lambda: events)
        answer(owner, events[0], selection={**selection(), 'context': 'thisServer'})
        await entered.wait()
        live[0] = False
        await asyncio.wait_for(cleanup.wait(), 2)
        assert not work.done() and owner._jobs and not calls
        release.set()
        with pytest.raises(McpError, match='context capture'):
            await work
        assert not calls and not owner._jobs
    finally:
        release.set()
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_required_tools_denied_selection_never_enters_custom_executor():
    events, calls = [], []
    async def execute(*args):
        calls.append(args)
        return {'model': 'explicit-model', 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE'}}
    source = request()
    source['params'].update(tools=[{'name': 'approved', 'inputSchema': {'type': 'object'}}],
                            toolChoice={'mode': 'required'})
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    work = asyncio.create_task(owner.handle(source))
    try:
        await until(lambda: events)
        answer(owner, events[0], selection=selection())
        with pytest.raises(McpError, match='Required'):
            await asyncio.wait_for(work, 2)
        assert not calls and not any(event.get('phase') == 'output' for event in events)
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_custom_executor_text_only_cannot_bypass_required_tool_response():
    events, calls = [], []
    async def execute(*args):
        calls.append(args)
        return {'model': 'explicit-model', 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE'}}
    source = request()
    source['params'].update(tools=[{'name': 'approved', 'inputSchema': {'type': 'object'}}],
                            toolChoice={'mode': 'required'})
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    work = asyncio.create_task(owner.handle(source))
    try:
        await until(lambda: events)
        answer(owner, events[0], selection={**selection(), 'tools': ['approved']})
        with pytest.raises(McpError, match='required tool'):
            await asyncio.wait_for(work, 2)
        assert len(calls) == 1 and not any(event.get('phase') == 'output' for event in events)
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_unsettled_new_assistant_batch_refuses_before_review_or_executor():
    events, calls = [], []
    async def execute(*args):
        calls.append(args)
        return {}
    source = request()
    source['params']['messages'] = [
        {'role': 'assistant', 'content': {'type': 'tool_use', 'name': 'one', 'id': 'first', 'input': {}}},
        {'role': 'assistant', 'content': {'type': 'tool_use', 'name': 'two', 'id': 'second', 'input': {}}},
        {'role': 'user', 'content': [
            {'type': 'tool_result', 'toolUseId': 'first', 'content': []},
            {'type': 'tool_result', 'toolUseId': 'second', 'content': []}]}]
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    try:
        with pytest.raises(McpError, match='settlement'):
            await owner.handle(source)
        assert not events and not calls and not owner._jobs
    finally:
        await owner.close()


async def test_context_and_server_tool_ids_colliding_get_exact_disjoint_reviewed_pairing():
    events, calls, reads = [], [], []
    def rows(label):
        return [
            {'role': 'assistant', 'content': {'type': 'tool_use', 'id': 'same', 'name': 'lookup', 'input': {}}},
            {'role': 'user', 'content': {'type': 'tool_result', 'toolUseId': 'same',
                                      'content': [{'type': 'text', 'text': label}]}}]
    context_rows = rows('CONTEXT')
    async def context(scope):
        reads.append(scope)
        return context_rows
    async def execute(value, chosen, cancel):
        calls.append(copy.deepcopy(value))
        return {'model': chosen['model'], 'role': 'assistant', 'content': {'type': 'text', 'text': 'OUTPUT'}}
    source = request()
    source['params'].update(messages=rows('SERVER'), includeContext='thisServer')
    owner = SamplingOwner('view', events.append, execute, lambda: True, context=context)
    work = asyncio.create_task(owner.handle(source))
    try:
        await until(lambda: events)
        answer(owner, events[0], selection={**selection(), 'context': 'thisServer'})
        await until(lambda: len(events) == 2)
        assert not calls and reads == ['thisServer']
        combined = events[1]['body']['messages']
        assert combined[0]['content']['id'] != 'same'
        assert combined[1]['content']['toolUseId'] == combined[0]['content']['id']
        assert combined[2]['content']['id'] == combined[3]['content']['toolUseId'] == 'same'
        assert context_rows[0]['content']['id'] == source['params']['messages'][0]['content']['id'] == 'same'
        context_rows[1]['content']['content'][0]['text'] = 'UNREVIEWED LATE'
        answer(owner, events[1])
        await until(lambda: len(events) == 3)
        assert calls[0]['params']['messages'] == combined and 'UNREVIEWED LATE' not in str(calls)
        answer(owner, events[2])
        assert (await work)['content']['text'] == 'OUTPUT'
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)
