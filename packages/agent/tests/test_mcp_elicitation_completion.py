"""Legacy URL completion notice routed from an actual owned peer, not login success."""
from __future__ import annotations

import asyncio
import json

from xdog.agent.mcp import McpManager

from packages.agent.tests.test_mcp_elicitation_registration import peer_config


async def test_actual_peer_url_completion_after_review_no_request_replay_or_credential_admission(tmp_path):
    configs, log = peer_config(tmp_path)
    script = tmp_path / 'peer.py'
    source = script.read_text().replace("'message':'Review name','requestedSchema':{'type':'object',\n"
        "                'properties':{'name':{'type':'string'}},'required':['name']}",
        "'mode':'url','message':'External interaction','elicitationId':'url-owned',"
        "'url':'https://operator.example/flow'")
    source = source.replace("    elif method=='ping':", """    elif row.get('id')=='form' and 'result' in row:
        for key in ['foreign', 'url-owned', 'url-owned']:
            send({'jsonrpc':'2.0','method':'notifications/elicitation/complete','params':{'elicitationId':key}})
    elif method=='ping':""")
    script.write_text(source)
    manager, events = McpManager(configs, tmp_path), []
    async def wait(predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(.002)
    async def approve(event):
        await manager.answer_elicitation('owned', event['id'], lease='view', phase=event['phase'],
            receipt=event['receipt'], sha256=event['sha256'], response={'action': 'accept'})
    try:
        await manager.register_elicitation('owned', lease='view', publish=events.append, alive=lambda: True)
        await manager.connect('owned')
        await wait(lambda: events)
        await approve(events[0])
        await wait(lambda: len(events) == 2)
        await approve(events[1])
        await wait(lambda: any(event['type'] == 'elicitation_url_complete' for event in events))
        assert sum(event['type'] == 'elicitation_url_pending' for event in events) == 1
        assert sum(event['type'] == 'elicitation_url_complete' for event in events) == 1
        assert not manager._oauth and not manager.tools()
        client = manager._clients['owned']
        assert await manager._bridge(client.request('ping', {})) == {}
        rows = [json.loads(row) for row in log.read_text().splitlines()]
        assert sum(row.get('id') == 'form' and 'result' in row for row in rows) == 1
        assert sum(row.get('method') == 'tools/list' for row in rows) == 1
        assert not any(row.get('method') in {'tools/call', 'resources/read'} for row in rows)
        await manager.disconnect('owned')
        assert not manager._elicitation
    finally:
        await manager.aclose()


async def test_current_http_completion_notification_is_refused_before_legacy_handler():
    import pytest
    from xdog.agent.mcp.config import McpServerConfig
    from xdog.agent.mcp.http import HttpClient
    from xdog.agent.mcp.stdio import McpError
    client = HttpClient(McpServerConfig.parse('owned', {'transport': 'http', 'url': 'http://127.0.0.1/mcp'}))
    called = []
    async def handler(value):
        return {'action': 'accept'}
    client.reverse.active = True
    client.reverse.elicitation_handler = handler
    client.reverse.elicitation_completion = called.append
    notice = {'jsonrpc': '2.0', 'method': 'notifications/elicitation/complete',
              'params': {'elicitationId': 'owned'}}
    with pytest.raises(McpError, match='unrelated'):
        await client._message(notice, None, 200, modern=True)
    assert not called
    assert await client._message(notice, None, 200, modern=False) is None
    assert called == [notice]
    await client.close()
