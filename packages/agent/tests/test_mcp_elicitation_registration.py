"""Real peer elicitation review registration, family-local withdrawal and no replay."""
from __future__ import annotations

import asyncio
import json
import sys

import pytest
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.stdio import McpError


def peer_config(tmp_path):
    script, log = tmp_path / 'peer.py', tmp_path / 'frames'
    script.write_text('''import sys,json
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
        send({'jsonrpc':'2.0','id':'form','method':'elicitation/create','params':{
            'message':'Review name','requestedSchema':{'type':'object',
                'properties':{'name':{'type':'string'}},'required':['name']}}})
        send({'jsonrpc':'2.0','id':'sample','method':'sampling/createMessage',
            'params':{'messages':[],'maxTokens':10}})
        send({'jsonrpc':'2.0','id':row['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':row['id'],'result':{}})
''')
    return {'owned': {'command': sys.executable, 'args': [str(script), str(log)], 'timeout': 5}}, log


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.002)


async def answer(manager, event, response, lease='view'):
    await manager.answer_elicitation('owned', event['id'], lease=lease, phase=event['phase'],
                                    receipt=event['receipt'], sha256=event['sha256'], response=response)


@pytest.mark.parametrize('end', ['release', 'detach', 'disconnect'])
async def test_actual_manager_peer_request_reply_reviews_and_original_lease_retirement(tmp_path, end):
    configs, log = peer_config(tmp_path)
    manager, events = McpManager(configs, tmp_path), []
    try:
        await manager.register_elicitation('owned', lease='view', publish=events.append, alive=lambda: True)
        assert not log.exists()
        await manager.connect('owned')
        await until(lambda: events)
        client = manager._clients['owned']
        assert manager.status()[0]['client_capabilities'] == {'elicitation': {'form': {}, 'url': {}}}
        assert events[0]['phase'] == 'request'
        assert await manager._bridge(client.request('ping', {})) == {}
        with pytest.raises(McpError, match='owner changed'):
            await answer(manager, events[0], {'action': 'accept', 'content': {'name': 'USER'}}, lease='foreign')
        await answer(manager, events[0], {'action': 'accept', 'content': {'name': 'PRIVATE FORM'}})
        await until(lambda: len(events) == 2)
        assert events[1]['phase'] == 'response'
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any(row.get('id') == 'form' for row in rows)
        if end == 'release':
            await answer(manager, events[1], {'action': 'accept'})
            await until(lambda: not client.reverse._jobs)
            await manager._bridge(client.request('ping', {}))
            rows = [json.loads(line) for line in log.read_text().splitlines()]
            reply = next(row for row in rows if row.get('id') == 'form')
            assert reply['result'] == {'action': 'accept', 'content': {'name': 'PRIVATE FORM'}}
        elif end == 'detach':
            await manager.detach_elicitation('owned', lease='foreign')
            assert manager._elicitation
            await manager.detach_elicitation('owned', lease='view')
            assert not manager._elicitation and not client.reverse._jobs
            assert await manager._bridge(client.request('ping', {})) == {}
        else:
            await manager.disconnect('owned')
            assert not manager._elicitation and not client.reverse._jobs
            await manager.connect('owned')
            assert manager._clients['owned'].reverse.elicitation_handler is None
            assert not manager._elicitation
        assert all('PRIVATE FORM' not in str(event) for event in events if event['type'] == 'elicitation_closed')
    finally:
        await manager.aclose()


@pytest.mark.parametrize('withdraw', ['sampling', 'elicitation'])
async def test_family_withdrawal_preserves_other_pending_review_and_no_response_replay(tmp_path, withdraw):
    configs, _ = peer_config(tmp_path)
    manager, sampling, elicitation = McpManager(configs, tmp_path), [], []
    async def execute(*args):
        raise AssertionError('No model before human sampling approval')
    try:
        await manager.register_sampling('owned', lease='sample-view', publish=sampling.append,
                                        execute=execute, alive=lambda: True)
        await manager.register_elicitation('owned', lease='view', publish=elicitation.append, alive=lambda: True)
        await manager.connect('owned')
        await until(lambda: sampling and elicitation)
        client = manager._clients['owned']
        if withdraw == 'sampling':
            await manager.detach_sampling('owned', lease='sample-view')
            assert client.reverse.active and client.reverse.elicitation_handler is not None
            assert 'form' in client.reverse._jobs and 'sample' not in client.reverse._jobs
            await answer(manager, elicitation[0], {'action': 'decline'})
        else:
            await manager.detach_elicitation('owned', lease='view')
            assert client.reverse.active and client.reverse.handler is not None
            assert 'sample' in client.reverse._jobs and 'form' not in client.reverse._jobs
            await manager.answer_sampling('owned', sampling[0]['id'], lease='sample-view', phase='input',
                receipt=sampling[0]['receipt'], sha256=sampling[0]['sha256'], approve=False)
        await until(lambda: not client.reverse._jobs)
        assert await manager._bridge(client.request('ping', {})) == {}
        assert client.reverse._used >= {'sample', 'form'}  # Withdrawal must not reset no-replay receipts.
    finally:
        await manager.aclose()
