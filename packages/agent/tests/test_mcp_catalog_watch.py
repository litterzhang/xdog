from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.subscriptions import ResourceSubscriptions, SubscriptionError

PEER = Path(__file__).parent / 'fixtures/mcp_subscription_peer.py'


@pytest.mark.parametrize('mode', ['modern', 'legacy', 'modern-slow-cancel'])
@pytest.mark.parametrize('kind', ['tools', 'prompts', 'resources'])
async def test_catalog_watch_exact_filter_notices_owner_cleanup_and_no_implicit_catalog_reads(tmp_path, mode, kind):
    log = tmp_path / 'requests'
    manager = McpManager({'peer': {'command': sys.executable, 'args': [str(PEER), mode, str(log)]}}, tmp_path)
    events = []
    try:
        await manager.connect('peer')
        before = log.read_text().splitlines()
        watch = await manager.watch_catalog('peer', kind, lease='view', on_change=events.append, alive=lambda: True)
        assert watch['state'] == 'active' and watch['catalog'] == kind and watch['uri'] == ''
        await manager._bridge(manager._clients['peer'].request('fixture/update', {}))
        async with asyncio.timeout(3):
            while not events:
                await asyncio.sleep(0.003)
        assert events[-1]['changes'] == 1 and events[-1]['catalog'] == kind
        new = [json.loads(line) for line in log.read_text().splitlines()[len(before):]]
        assert not any(frame['method'] in ('resources/read', 'tools/list', 'prompts/list', 'resources/list',
                                            'tools/call', 'resources/subscribe') for frame in new)
        if not mode.startswith('legacy'):
            listen = next(frame for frame in new if frame['method'] == 'subscriptions/listen')
            assert listen['params']['notifications'] == {kind + 'ListChanged': True}
        stopped = await manager.unwatch_catalog('peer', kind, lease='view')
        assert stopped['state'] == 'stopped' and stopped['catalog'] == kind
        assert not manager._resource_watches
        await manager.detach_resource_watches('view')
        peer_state = await manager._bridge(manager._clients['peer'].request('fixture/status', {}))
        assert peer_state == {'modern': 0, 'legacy': 0}
    finally:
        await manager.aclose()


@pytest.mark.parametrize('unrequested', ['tools', 'prompts', 'resources'])
async def test_catalog_router_rejects_unrequested_filters_and_unrelated_notifications(unrequested):
    router = ResourceSubscriptions()
    kind = 'prompts' if unrequested != 'prompts' else 'tools'
    async def stop(watch):
        router.remove(watch)
    watch = router.add('', lambda _: None, stop, kind=kind)
    def notice(method, notifications=None):
        return {'jsonrpc': '2.0', 'method': method, 'params': {
            '_meta': {'io.modelcontextprotocol/subscriptionId': watch.id},
            **({'notifications': notifications} if notifications is not None else {})}}
    try:
        with pytest.raises(SubscriptionError):
            router.modern_frame(notice('notifications/subscriptions/acknowledged', {
                kind + 'ListChanged': True, unrequested + 'ListChanged': True}))
        assert router.modern_frame(notice('notifications/subscriptions/acknowledged', {kind + 'ListChanged': True}))
        with pytest.raises(SubscriptionError):
            router.modern_frame(notice('notifications/' + unrequested + '/list_changed'))
        assert watch.changes == 0
    finally:
        router.close()
