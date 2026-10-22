"""Passive catalog invalidation remains, without any catalog watch operation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpError, McpRegistry
from xdog.agent.mcp.resource_metadata import CATALOGS, ResourceMetadataError, catalog_notice

PEER = Path(__file__).parent / 'fixtures/mcp_subscription_peer.py'


@pytest.mark.parametrize('kind', sorted(CATALOGS))
async def test_legacy_unsolicited_catalog_notice_never_reads_or_reregisters_without_explicit_refresh(tmp_path, kind):
    log = tmp_path / 'frames'
    manager = McpRegistry({'peer': {'command': sys.executable, 'args': [str(PEER), 'legacy', str(log)]}}, tmp_path)
    try:
        await manager.connect('peer')
        client = manager._clients['peer']
        original = manager._tools
        before = len(log.read_text().splitlines())
        assert await manager._bridge(client.request('fixture/update', {'kind': kind})) == {}
        assert client.dirty is (kind == 'tools')
        assert manager._tools is original
        new = [json.loads(line) for line in log.read_text().splitlines()[before:]]
        assert [row['method'] for row in new] == ['fixture/update']
        await manager.refresh('peer')
        assert not client.dirty
        tail = [json.loads(line) for line in log.read_text().splitlines()[before:]]
        assert [row['method'] for row in tail] == ['fixture/update', 'tools/list']
    finally:
        await manager.aclose()


@pytest.mark.parametrize('kind', sorted(CATALOGS))
async def test_current_unsolicited_catalog_notice_cannot_create_a_hidden_subscription_or_reconnect(tmp_path, kind):
    log = tmp_path / 'frames'
    manager = McpRegistry({'peer': {'command': sys.executable, 'args': [str(PEER), 'modern', str(log)]}}, tmp_path)
    try:
        await manager.connect('peer')
        client = manager._clients['peer']
        with pytest.raises(McpError, match='connection lost'):
            await manager._bridge(client.request('fixture/update', {'kind': kind}))
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert [row['method'] for row in frames] == ['server/discover', 'tools/list', 'fixture/update']
        assert client.state == 'disconnected' and not client.dirty and not client._pending
        assert not hasattr(client, 'resources')
    finally:
        await manager.aclose()


@pytest.mark.parametrize('changes', [{'params': []}, {'params': {'_meta': False}}, {'params': {'uri': 'private'}},
                                    {'id': -1}, {'jsonrpc': 'wrong'}, {'unexpected': 'field'}])
def test_malformed_passive_catalog_metadata_is_not_coerced_to_refresh_authority(changes):
    packet = {'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed', 'params': {}}
    with pytest.raises(ResourceMetadataError):
        catalog_notice({**packet, **changes})


def test_catalog_notice_helper_is_inert_and_carries_no_subscription_filters():
    assert CATALOGS == {'tools', 'resources', 'prompts'}
    for kind in CATALOGS:
        assert catalog_notice({'jsonrpc': '2.0', 'method': 'notifications/' + kind + '/list_changed'}) == kind
    notice = {'jsonrpc': '2.0', 'method': 'notifications/resources/updated', 'params': {'uri': 'x'}}
    assert catalog_notice(notice) is None
