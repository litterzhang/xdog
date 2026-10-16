"""Legacy reverse traffic is Roots/ping/refusal only, with no callback dispatcher."""
from __future__ import annotations

import json
import sys

import pytest
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.stdio import StdioClient


@pytest.mark.parametrize('method', ['elicitation/create', 'sampling/createMessage', 'tasks/list', 'tasks/get',
                                   'tasks/result', 'tasks/cancel'])
async def test_actual_legacy_stdio_refuses_removed_family_then_serves_roots_ping_without_replay(tmp_path, method):
    script, log = tmp_path / 'peer.py', tmp_path / 'frames'
    script.write_text('''import json,sys
from pathlib import Path
log=Path(sys.argv[1]);removed=sys.argv[2]
def send(value):print(json.dumps(value),flush=True)
for raw in sys.stdin:
    row=json.loads(raw)
    with log.open('a') as file:file.write(json.dumps(row)+'\\n')
    method=row.get('method')
    if method=='server/discover':
        send({'jsonrpc':'2.0','id':row['id'],'error':{'code':-32601,'message':'legacy'}})
    elif method=='initialize':
        assert row['params']['capabilities']=={'roots':{}}
        send({'jsonrpc':'2.0','id':row['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}})
    elif method=='tools/list':
        for key,method,params in [('removed',removed,{'task':{},'_meta':{
            'io.modelcontextprotocol/related-task':{'taskId':'forged'}}}),
            ('roots','roots/list',{}),('ping','ping',{})]:
            send({'jsonrpc':'2.0','id':key,'method':method,'params':params})
        send({'jsonrpc':'2.0','id':row['id'],'result':{'tools':[]}})
    elif method=='ping':send({'jsonrpc':'2.0','id':row['id'],'result':{}})
''')
    client = StdioClient(McpServerConfig.parse('owned', {'command': sys.executable,
        'args': [str(script), str(log), method], 'timeout': 2,
        'roots': [{'uri': 'file:///explicit/root', 'name': 'Explicit'}]}), tmp_path)
    try:
        await client.connect()
        assert not hasattr(client, 'reverse')
        assert await client.request('tools/list', {}) == {'tools': []}
        assert await client.request('ping', {}) == {}
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        removed = [row for row in frames if row.get('id') == 'removed']
        assert len(removed) == 1 and removed[0]['error']['code'] == -32601
        assert next(row for row in frames if row.get('id') == 'roots')['result'] == {
            'roots': [{'uri': 'file:///explicit/root', 'name': 'Explicit'}]}
        assert next(row for row in frames if row.get('id') == 'ping')['result'] == {}
        assert not any(row.get('method') in {'tools/call', 'resources/read', 'prompts/get'} for row in frames)
        process = client.process
    finally:
        await client.close()
    assert process.returncode is not None and not client._pending and not client.root_capability.active
