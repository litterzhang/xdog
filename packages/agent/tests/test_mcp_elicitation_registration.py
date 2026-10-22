"""Owned legacy peer helper is retained; Elicitation registration is absent."""
from __future__ import annotations

import json
import sys

from xdog.agent.mcp import McpRegistry


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


async def test_unsolicited_elicitation_and_sampling_receive_refusal_without_registration_or_replay(tmp_path):
    configs, log = peer_config(tmp_path)
    manager = McpRegistry(configs, tmp_path)
    try:
        for attribute in ('register_elicitation', 'answer_elicitation', 'detach_elicitation', '_elicitation'):
            assert not hasattr(manager, attribute)
        await manager.connect('owned')
        client = manager._clients['owned']
        assert await manager._bridge(client.request('ping', {})) == {}
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        replies = [row for row in frames if row.get('id') in {'form', 'sample'}]
        assert len(replies) == 2 and all(row['error']['code'] == -32601 for row in replies)
        assert sum(row.get('method') == 'tools/list' for row in frames) == 1
        assert not any(row.get('method') in {'tools/call', 'resources/read', 'prompts/get'} for row in frames)
        assert not hasattr(client, 'reverse') and client.inputs.advertised() == {}
    finally:
        await manager.aclose()
