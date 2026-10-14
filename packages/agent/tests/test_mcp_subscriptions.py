from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.subscriptions import ResourceSubscriptions, SubscriptionError

FIXTURE = Path(__file__).parent / 'fixtures/mcp_subscription_peer.py'


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.003)


async def test_modern_unsupported_ack_fails_without_read_or_resubscribe(tmp_path):
    log = tmp_path / 'messages'
    manager = McpManager({'peer': {'command': sys.executable, 'args': [str(FIXTURE),
        'unsupported', str(log)]}}, tmp_path)
    try:
        await manager.connect('peer')
        with pytest.raises(SubscriptionError):
            await manager.watch_resource('peer', 'test://watched', lease='one', on_change=lambda _: None,
                                         alive=lambda: True)
        assert not manager._resource_watches
        peer_state = await manager._bridge(manager._clients['peer'].request('fixture/status', {}))
        assert peer_state == {'modern': 0, 'legacy': 0}
        methods = [json.loads(line)['method'] for line in log.read_text().splitlines()]
        assert methods.count('subscriptions/listen') == 1 and 'resources/read' not in methods
    finally:
        await manager.aclose()


async def test_router_modern_ordering_identity_metadata_and_retired_ids_do_not_replay():
    router, notices = ResourceSubscriptions(), []
    async def stop(watch):
        router.remove(watch)
    watch = router.add('test://one', notices.append, stop)
    meta = {'io.modelcontextprotocol/subscriptionId': watch.id}
    update = {'jsonrpc': '2.0', 'method': 'notifications/resources/updated',
              'params': {'uri': watch.uri, '_meta': meta}}
    with pytest.raises(SubscriptionError):
        router.modern_frame(update)
    ack = {'jsonrpc': '2.0', 'method': 'notifications/subscriptions/acknowledged', 'params': {
        '_meta': meta, 'notifications': {'resourceSubscriptions': [watch.uri]}}}
    assert router.modern_frame(ack)
    assert router.modern_frame(update)
    await until(lambda: notices[-1]['changes'] == 1)
    with pytest.raises(SubscriptionError):
        router.modern_frame({**update, 'params': {'uri': 'test://foreign', '_meta': meta}})
    await watch.close()
    count = len(notices)
    assert router.modern_frame(update) and len(notices) == count
    newer = router.add(watch.uri, notices.append, stop)
    assert newer.id != watch.id and newer.changes == 0
    router.close()


@pytest.mark.parametrize('modern', [True, False])
async def test_owned_http_stream_subscription_lifetime_and_legacy_get_without_post_replay(
        tmp_path, monkeypatch, modern):
    import httpx
    import xdog.agent.mcp.http as module
    from xdog.agent.mcp.config import parse_servers
    from xdog.agent.mcp.http import HttpClient
    messages, streams, events = [], [], []
    class Stream(httpx.AsyncByteStream):
        def __init__(self, sid=None):
            self.sid, self.queue, self.closed = sid, asyncio.Queue(), False
            streams.append(self)
        async def __aiter__(self):
            if self.sid is not None:
                yield ('data: ' + json.dumps({'jsonrpc': '2.0',
                    'method': 'notifications/subscriptions/acknowledged', 'params': {
                    '_meta': {'io.modelcontextprotocol/subscriptionId': self.sid},
                    'notifications': {'resourceSubscriptions': ['test://watched']}}})
                    + '\n\n').encode()
            while True:
                message = await self.queue.get()
                yield ('data: ' + json.dumps(message) + '\n\n').encode()
        async def aclose(self):
            self.closed = True
    async def handler(request):
        if request.method == 'DELETE':
            return httpx.Response(204)
        if request.method == 'GET':
            assert not modern
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Stream())
        message = json.loads(request.content)
        messages.append(message)
        method, identifier = message['method'], message.get('id')
        if method == 'server/discover' and not modern:
            return httpx.Response(200, json={'jsonrpc': '2.0', 'id': identifier,
                                            'error': {'code': -32601, 'message': 'legacy'}})
        if method == 'server/discover':
            result = {'supportedVersions': ['2026-07-28'], 'capabilities': {'resources': {'subscribe': True}}}
        elif method == 'initialize':
            result = {'protocolVersion': '2025-11-25', 'capabilities': {'resources': {'subscribe': True}}}
        elif method == 'subscriptions/listen':
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Stream(identifier))
        else:
            result = {}
        if identifier is None:
            return httpx.Response(202)
        return httpx.Response(200, headers={'mcp-session-id': 'owned-session'},
                              json={'jsonrpc': '2.0', 'id': identifier, 'result': result})
    factory = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: factory(transport=httpx.MockTransport(handler),
                                                                          **kwargs))
    config = parse_servers({'peer': {'transport': 'http', 'url': 'https://peer.invalid/mcp', 'timeout': 2}})[0]
    client = HttpClient(config)
    try:
        await client.connect()
        await until(lambda: modern or streams)
        watch = await client.watch_resource('test://watched', events.append)
        params = {'uri': 'test://watched'}
        if modern:
            params['_meta'] = {'io.modelcontextprotocol/subscriptionId': watch.id}
        await streams[-1].queue.put({'jsonrpc': '2.0', 'method': 'notifications/resources/updated', 'params': params})
        await until(lambda: events and events[-1]['changes'] == 1)
        if modern:
            await streams[-1].queue.put({'jsonrpc': '2.0', 'id': watch.id,
                                        'result': {'resultType': 'complete'}})
            await until(lambda: watch.state == 'ended' and not client._resource_streams)
            assert not client.resources.watches and streams[-1].closed and client.state == 'connected'
            assert sum(message['method'] == 'subscriptions/listen' for message in messages) == 1
            watch = await client.watch_resource('test://watched', events.append)
            await streams[-1].queue.put({'jsonrpc': '2.0', 'id': watch.id,
                                        'error': {'code': -32603, 'message': 'private peer error'}})
            await until(lambda: watch.state == 'failed' and not client._resource_streams)
            assert client.state == 'connected' and 'private peer error' not in str(events)
            watch = await client.watch_resource('test://watched', events.append)
        await watch.close()
        assert watch.state == 'stopped' and not client.resources.watches
        assert streams[-1].closed if modern else not streams[-1].closed
        methods = [message['method'] for message in messages]
        assert 'resources/read' not in methods
        assert methods.count('subscriptions/listen') == 3 * int(modern)
        assert methods.count('resources/subscribe') == methods.count('resources/unsubscribe') == int(not modern)
    finally:
        await client.close()
    assert all(stream.closed for stream in streams)


async def test_view_owner_loss_closes_live_subscription_without_a_new_notification(tmp_path):
    log = tmp_path / 'messages'
    manager = McpManager({'peer': {'command': sys.executable, 'args': [str(FIXTURE), 'modern', str(log)]}}, tmp_path)
    alive = [True]
    try:
        await manager.connect('peer')
        await manager.watch_resource('peer', 'test://watched', lease='lost-view', on_change=lambda _: None,
                                     alive=lambda: alive[0])
        alive[0] = False
        await until(lambda: not manager._resource_watches)
        await until(lambda: 'notifications/cancelled' in log.read_text())
        assert log.read_text().count('subscriptions/listen') == 1
    finally:
        await manager.aclose()


@pytest.mark.parametrize('metadata', [False, True])
@pytest.mark.parametrize('failure', [False, True])
async def test_terminal_responses_use_rpc_id_and_do_not_end_sibling_or_replay(metadata, failure):
    router, notices = ResourceSubscriptions(), []

    async def stop(watch):
        router.remove(watch)

    watch = router.add('test://one', notices.append, stop)
    sibling = router.add('test://two', notices.append, stop)
    watch.activate()
    sibling.activate()
    payload = {'code': -32603, 'message': 'private peer error'} if failure else {'resultType': 'complete'}
    if metadata:
        payload['_meta'] = {'io.modelcontextprotocol/subscriptionId': watch.id}
    frame = {'jsonrpc': '2.0', 'id': watch.id, 'error' if failure else 'result': payload}
    assert router.modern_frame(frame)
    assert watch.state == ('failed' if failure else 'ended')
    assert sibling.state == 'active' and sibling.id in router.watches
    count = len(notices)
    assert router.modern_frame(frame) and len(notices) == count
    assert 'private peer error' not in str(notices)
    router.close()


@pytest.mark.parametrize('payload', [
    {'result': {'resultType': 'complete'}, 'error': {'code': -32603, 'message': 'private'}},
    {'result': {'resultType': 'complete', '_meta': {'io.modelcontextprotocol/subscriptionId': -99}}},
    {'result': {'resultType': 'complete', '_meta': {'io.modelcontextprotocol/subscriptionId': True}}},
    {'result': {'resultType': 'complete', '_meta': []}},
    {'error': {'code': True, 'message': 'private'}},
    {'error': {'code': -32603, 'message': 123}},
    {'error': 'private'},
    {},
])
async def test_malformed_terminal_response_cannot_mutate_watch(payload):
    router, notices = ResourceSubscriptions(), []

    async def stop(watch):
        router.remove(watch)

    watch = router.add('test://one', notices.append, stop)
    watch.activate()
    before = list(notices)
    with pytest.raises(SubscriptionError):
        router.modern_frame({'jsonrpc': '2.0', 'id': watch.id, **payload})
    assert notices == before and watch.state == 'active'
    router.close()


@pytest.mark.parametrize('method, state', [('fixture/end', 'ended'), ('fixture/error', 'failed')])
async def test_owned_stdio_terminal_response_without_metadata_preserves_connection_and_explicit_rewatch(
        tmp_path, method, state):
    log = tmp_path / 'messages'
    manager = McpManager({'peer': {'command': sys.executable, 'args': [str(FIXTURE), 'modern', str(log)]}}, tmp_path)
    notices = []
    try:
        await manager.connect('peer')
        await manager.watch_resource('peer', 'test://watched', lease='one', on_change=notices.append,
                                     alive=lambda: True)
        await manager._bridge(manager._clients['peer'].request(method, {}))
        await until(lambda: notices and notices[-1]['state'] == state)
        client = manager._clients['peer']
        assert client.state == 'connected' and not client.resources.watches
        assert 'private peer error' not in str(notices)
        assert len(await manager.catalog('peer', 'resource')) == 1
        assert log.read_text().count('subscriptions/listen') == 1
        await manager.watch_resource('peer', 'test://watched', lease='one', on_change=notices.append,
                                     alive=lambda: True)
        assert len(client.resources.watches) == 1
        assert log.read_text().count('subscriptions/listen') == 2
        assert 'resources/read' not in log.read_text()
    finally:
        await manager.aclose()


async def test_ack_optional_false_filters_and_schema_empty_meta_result_do_not_opt_in_or_read():
    router, notices = ResourceSubscriptions(), []

    async def stop(watch):
        router.remove(watch)

    watch = router.add('test://one', notices.append, stop)
    meta = {'io.modelcontextprotocol/subscriptionId': watch.id}
    assert router.modern_frame({'jsonrpc': '2.0', 'method': 'notifications/subscriptions/acknowledged', 'params': {
        '_meta': meta, 'notifications': {'resourceSubscriptions': [watch.uri], 'toolsListChanged': False,
                                       'promptsListChanged': False, 'resourcesListChanged': False}}})
    assert watch.state == 'active'
    assert router.modern_frame({'jsonrpc': '2.0', 'id': watch.id, 'result': {'_meta': meta}})
    assert watch.state == 'ended' and not router.watches


@pytest.mark.parametrize('key, value', [('toolsListChanged', True), ('promptsListChanged', 0),
                                       ('resourcesListChanged', None), ('unknownFilter', False)])
async def test_ack_unrequested_true_or_malformed_filters_do_not_activate_a_watch(key, value):
    router = ResourceSubscriptions()

    async def stop(watch):
        router.remove(watch)

    watch = router.add('test://one', lambda _: None, stop)
    with pytest.raises(SubscriptionError):
        router.modern_frame({'jsonrpc': '2.0', 'method': 'notifications/subscriptions/acknowledged', 'params': {
            '_meta': {'io.modelcontextprotocol/subscriptionId': watch.id},
            'notifications': {'resourceSubscriptions': [watch.uri], key: value}}})
    assert watch.state == 'opening' and not watch.ready.done()
    router.close()


@pytest.mark.parametrize('result', [{}, {'resultType': 'complete'},
                                   {'_meta': {'io.modelcontextprotocol/subscriptionId': -1}}])
async def test_completion_before_ack_refuses_only_its_watch_and_never_replays(result):
    router, notices = ResourceSubscriptions(), []

    async def stop(watch):
        router.remove(watch)

    watch = router.add('test://one', notices.append, stop)
    sibling = router.add('test://two', notices.append, stop)
    sibling.activate()
    frame = {'jsonrpc': '2.0', 'id': watch.id, 'result': result}
    assert router.modern_frame(frame)
    with pytest.raises(SubscriptionError, match='before acknowledgement'):
        await watch.ready
    assert watch.state == 'unsupported' and sibling.state == 'active'
    before = len(notices)
    assert router.modern_frame(frame) and len(notices) == before
    router.close()


async def test_stdio_pre_ack_completion_does_not_disconnect_sibling_or_replay_the_refused_watch(tmp_path):
    log = tmp_path / 'messages'
    manager = McpManager({'peer': {'command': sys.executable, 'args': [str(FIXTURE), 'preclose', str(log)]}}, tmp_path)
    notices = []
    try:
        await manager.connect('peer')
        await manager.watch_resource('peer', 'test://watched', lease='one', on_change=notices.append,
                                     alive=lambda: True)
        with pytest.raises(SubscriptionError, match='before acknowledgement'):
            await manager.watch_resource('peer', 'test://refused', lease='two', on_change=lambda _: None,
                                         alive=lambda: True)
        client = manager._clients['peer']
        assert client.state == 'connected' and len(client.resources.watches) == 1
        await manager._bridge(client.request('fixture/update', {}))
        await until(lambda: notices and notices[-1]['changes'] == 1)
        assert len(await manager.catalog('peer', 'resource')) == 1
        requests = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(message.get('method') == 'subscriptions/listen' and
                   message['params']['notifications']['resourceSubscriptions'] == ['test://refused']
                   for message in requests) == 1
        assert not any(message.get('method') == 'resources/read' for message in requests)
    finally:
        await manager.aclose()
