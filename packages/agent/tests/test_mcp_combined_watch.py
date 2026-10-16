"""Removed combined-filter receipts are inert legacy data or explicit current errors."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from xdog.agent.mcp import McpError, McpManager
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.http import HttpClient

PEER = Path(__file__).parent / 'fixtures/mcp_subscription_peer.py'


@pytest.mark.parametrize('mode', ['modern', 'legacy'])
async def test_old_negative_receipt_identity_cannot_settle_or_poison_an_ordinary_successor(tmp_path, mode):
    log = tmp_path / 'frames'
    manager = McpManager({'peer': {'command': sys.executable, 'args': [str(PEER), mode, str(log)]}}, tmp_path)
    try:
        await manager.connect('peer')
        client = manager._clients['peer']
        assert await manager._bridge(client.request('fixture/old-receipt', {})) == {'value': 'current result'}
        assert await manager._bridge(client.request('ping', {})) == {}
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(row.get('method') == 'fixture/old-receipt' for row in frames) == 1
        assert not any(row.get('method') in {'subscriptions/listen', 'resources/subscribe', 'resources/read'}
                       for row in frames)
        assert not client._pending and not hasattr(client, 'resources')
    finally:
        await manager.aclose()


@pytest.mark.parametrize('method', ['notifications/subscriptions/acknowledged', 'notifications/resources/updated'])
@pytest.mark.parametrize('metadata', [None, False, -1, {'io.modelcontextprotocol/subscriptionId': -1}])
async def test_retired_filter_receipts_cannot_publish_or_trigger_reads_in_mock_http(method, metadata):
    sent = []
    def forbidden(request):
        sent.append(request)
        raise AssertionError('Removed receipt cannot initiate I/O')
    client = HttpClient(McpServerConfig.parse('peer', {'transport': 'http', 'url': 'http://127.0.0.1/mcp'}))
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
    client.state, client.version = 'connected', '2025-11-25'
    notice = {'jsonrpc': '2.0', 'method': method, 'params': {'_meta': metadata, 'uri': 'test://not-fetched',
              'notifications': {'toolsListChanged': True, 'resourceSubscriptions': ['test://not-fetched']}}}
    try:
        assert await client._message(notice, None, 200, modern=False) is None
        assert not client.dirty and not sent and not hasattr(client, '_resource_streams')
        with pytest.raises(McpError, match='unrelated'):
            await client._message(notice, None, 200, modern=True)
        assert not sent
        with pytest.raises(McpError, match='identity'):
            await client._message({'jsonrpc': '2.0', 'id': -1, 'result': {}}, 42, 200, modern=False)
        assert await client._message({'jsonrpc': '2.0', 'id': 42, 'result': {'content': []}},
                                     42, 200, modern=False) == {'content': []}
    finally:
        await client.close()
