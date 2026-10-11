from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpError, McpManager
from xdog.agent.mcp.subscriptions import ResourceSubscriptions, SubscriptionError, filter_key, notification_filters
from xdog.tui.mcp_subscription import filter_text, notification_filter

PEER = Path(__file__).parent / 'fixtures/mcp_subscription_peer.py'
SELECTED = {'toolsListChanged': True, 'promptsListChanged': True, 'resourcesListChanged': True,
            'resourceSubscriptions': ['test://two', 'test://one']}


@pytest.mark.parametrize('value', [None, {}, {'toolsListChanged': False}, {'toolsListChanged': 1},
    {'foreign': True}, {'resourceSubscriptions': 'test://one'}, {'resourceSubscriptions': [None]},
    {'resourceSubscriptions': ['test://one', 'test://one']}, {'resourceSubscriptions': ['']},
    {'resourceSubscriptions': ['test://one\n']}, {'resourceSubscriptions': ['x' * 8193]},
    {'resourceSubscriptions': [str(i) for i in range(17)]},
    {'resourceSubscriptions': [str(i) + 'x' * 8191 for i in range(9)]}])
def test_agent_and_ui_reject_invalid_combined_filters(value):
    with pytest.raises(SubscriptionError):
        notification_filters(value)
    with pytest.raises(ValueError):
        notification_filter(value)


def test_filter_snapshots_order_boolean_semantics_and_ui_are_consistent():
    source = {**SELECTED, 'resourceSubscriptions': list(SELECTED['resourceSubscriptions'])}
    snapshot = notification_filters(source)
    assert snapshot == notification_filter(source)
    assert filter_key(source) == filter_text(source)
    source['resourceSubscriptions'].append('later')
    assert snapshot['resourceSubscriptions'] == ['test://one', 'test://two']
    assert filter_key({'toolsListChanged': True, 'promptsListChanged': False}) == filter_key({'toolsListChanged': True})


@pytest.mark.parametrize('bad', [
    {**SELECTED, 'resourceSubscriptions': ['test://other']},
    {**SELECTED, 'resourceSubscriptions': ['test://two', 'test://one']},
    {**SELECTED, 'toolsListChanged': 1}, {**SELECTED, 'foreign': True},
])
async def test_exact_combined_ack_filters_and_notice_routing(bad):
    router = ResourceSubscriptions()
    async def stop(watch):
        router.remove(watch)
    watch = router.add_notifications(SELECTED, lambda _: None, stop)
    def notice(method, **params):
        return {'jsonrpc': '2.0', 'method': method, 'params': {
            '_meta': {'io.modelcontextprotocol/subscriptionId': watch.id}, **params}}
    try:
        with pytest.raises(SubscriptionError):
            router.modern_frame(notice('notifications/subscriptions/acknowledged', notifications=bad))
        assert router.modern_frame(notice('notifications/subscriptions/acknowledged', notifications=watch.filters()))
        copied = watch.filters()
        copied['resourceSubscriptions'].append('mutated')
        assert len(watch.filters()['resourceSubscriptions']) == 2
        for kind in ('tools', 'prompts', 'resources'):
            router.modern_frame(notice('notifications/' + kind + '/list_changed'))
        for uri in ('test://one', 'test://two'):
            router.modern_frame(notice('notifications/resources/updated', uri=uri))
        assert watch.changes == 5
        with pytest.raises(SubscriptionError):
            router.modern_frame(notice('notifications/resources/updated', uri='test://unrequested'))
        assert watch.changes == 5
        assert router.modern_frame({'jsonrpc': '2.0', 'id': watch.id, 'result': {}})
        assert watch.state == 'ended'
        assert router.modern_frame(notice('notifications/resources/updated', uri='test://one'))
        assert watch.changes == 5  # Retired stream never targets a successor.
    finally:
        router.close()


async def test_partial_ack_is_not_accepted_or_retried():
    router = ResourceSubscriptions()
    async def stop(watch):
        router.remove(watch)
    watch = router.add_notifications(SELECTED, lambda _: None, stop)
    assert router.modern_frame({'jsonrpc': '2.0', 'method': 'notifications/subscriptions/acknowledged', 'params': {
        '_meta': {'io.modelcontextprotocol/subscriptionId': watch.id}, 'notifications': {'toolsListChanged': True}}})
    assert watch.state == 'unsupported' and not router.watches
    with pytest.raises(SubscriptionError):
        await watch.ready


@pytest.mark.parametrize('peer_mode', ['modern', 'modern-slow-cancel'])
async def test_real_combined_watch_one_stream_all_notices_exact_owner_and_legacy_refusal(tmp_path, peer_mode):
    log = tmp_path / 'requests'
    manager = McpManager({'peer': {'command': sys.executable,
                                  'args': [str(PEER), peer_mode, str(log)]}}, tmp_path)
    events = []
    try:
        await manager.connect('peer')
        before = log.read_text().splitlines()
        result = await manager.watch_notifications('peer', SELECTED, lease='view', on_change=events.append,
                                                  alive=lambda: True)
        assert result['notifications'] == notification_filters(SELECTED) and result['uri'] == ''
        reordered = {**SELECTED, 'resourceSubscriptions': list(reversed(SELECTED['resourceSubscriptions']))}
        await manager.watch_notifications('peer', reordered, lease='view', on_change=events.append, alive=lambda: True)
        assert len(manager._resource_watches) == 1
        await manager._bridge(manager._clients['peer'].request('fixture/update', {}))
        async with asyncio.timeout(3):
            while not events:
                await asyncio.sleep(0.003)
        assert events[-1]['changes'] == 5
        await manager.detach_catalog_watches('view')
        assert len(manager._resource_watches) == 1  # Separate combined capability/ownership.
        await manager.unwatch_notifications('peer', reordered, lease='foreign')
        assert len(manager._resource_watches) == 1
        await manager.detach_notification_watches('view')
        assert not manager._resource_watches
        peer_state = await manager._bridge(manager._clients['peer'].request('fixture/status', {}))
        assert peer_state == {'modern': 0, 'legacy': 0}
        frames = [json.loads(line) for line in log.read_text().splitlines()[len(before):]]
        listen = [frame for frame in frames if frame['method'] == 'subscriptions/listen']
        assert len(listen) == 1 and listen[0]['params']['notifications'] == notification_filters(SELECTED)
        assert not any(frame['method'] in ('tools/list', 'resources/list', 'prompts/list', 'resources/read',
                                           'tools/call', 'resources/subscribe', 'resources/unsubscribe')
                       for frame in frames)
    finally:
        await manager.aclose()
    legacy_log = tmp_path / 'legacy'
    manager = McpManager({'peer': {'command': sys.executable,
                                  'args': [str(PEER), 'legacy', str(legacy_log)]}}, tmp_path)
    try:
        await manager.connect('peer')
        with pytest.raises(McpError, match='current-protocol'):
            await manager.watch_notifications('peer', SELECTED, lease='view',
                                              on_change=lambda _: None, alive=lambda: True)
        assert not manager._resource_watches
        assert not any(json.loads(line)['method'] in ('subscriptions/listen', 'resources/subscribe')
                       for line in legacy_log.read_text().splitlines())
    finally:
        await manager.aclose()


@pytest.mark.parametrize('mask', range(8))
@pytest.mark.parametrize('uris', [[], ['test://one'], ['test://two', 'test://one']])
async def test_all_catalog_flag_subsets_and_uri_set_shapes_route_exactly(mask, uris):
    selected = {kind + 'ListChanged': bool(mask & (1 << bit))
                for bit, kind in enumerate(('tools', 'prompts', 'resources'))}
    selected['resourceSubscriptions'] = uris
    router = ResourceSubscriptions()
    async def stop(watch):
        router.remove(watch)
    if not mask and not uris:
        with pytest.raises(SubscriptionError):
            router.add_notifications(selected, lambda _: None, stop)
        return
    watch = router.add_notifications(selected, lambda _: None, stop)
    def notice(method, **params):
        return {'jsonrpc': '2.0', 'method': method, 'params': {
            '_meta': {'io.modelcontextprotocol/subscriptionId': watch.id}, **params}}
    try:
        assert watch.filters() == notification_filter(selected)
        router.modern_frame(notice('notifications/subscriptions/acknowledged', notifications=watch.filters()))
        for kind in ('tools', 'prompts', 'resources'):
            if selected[kind + 'ListChanged']:
                router.modern_frame(notice('notifications/' + kind + '/list_changed'))
            else:
                with pytest.raises(SubscriptionError):
                    router.modern_frame(notice('notifications/' + kind + '/list_changed'))
        for uri in uris:
            router.modern_frame(notice('notifications/resources/updated', uri=uri))
        assert watch.changes == mask.bit_count() + len(uris)
    finally:
        router.close()
