"""Explicit legacy root metadata consent; no automatic workspace/filesystem grants."""
from __future__ import annotations

import asyncio
import json
import sys

import pytest
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.client_capabilities import RootCapability
from xdog.agent.mcp.config import McpServerConfig

ROOTS = [{'uri': 'file:///explicit/%E6%A0%B9', 'name': 'Explicit root'}]


def write_peer(path):
    path.write_text('''import json,sys
from pathlib import Path
log=Path(sys.argv[1]);modern=sys.argv[2]=='modern'
def send(value): print(json.dumps(value),flush=True)
for raw in sys.stdin:
    value=json.loads(raw)
    with log.open('a') as f:f.write(json.dumps(value)+'\\n')
    method=value.get('method')
    if method=='server/discover':
        if modern:
            assert value['params']['_meta']['io.modelcontextprotocol/clientCapabilities']=={}
            send({'jsonrpc':'2.0','id':value['id'],'result':{
                'supportedVersions':['2026-07-28'],'capabilities':{'tools':{}}}})
        else:
            send({'jsonrpc':'2.0','id':value['id'],'error':{'code':-32601,'message':'legacy'}})
    elif method=='initialize':
        send({'jsonrpc':'2.0','id':value['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{'tools':{}}}})
    elif method=='tools/list':
        if not modern:
            send({'jsonrpc':'2.0','id':'roots-query','method':'roots/list','params':{}})
        send({'jsonrpc':'2.0','id':value['id'],'result':{'tools':[]}})
    elif method=='ping':
        send({'jsonrpc':'2.0','id':value['id'],'result':{}})
''')


@pytest.mark.parametrize('roots', [True, None, 'cwd', [{}], [{'uri': 'https://host/root'}],
                                 [{'uri': 'file://remote/share'}], [{'uri': 'file:///a/../b'}],
                                 [{'uri': 'file:///a%0Ab'}], [{'uri': 'file:///bad%'}], [{'uri': 'file:///a%7Fb'}],
                                 [{'uri': 'file:///a'}, {'uri': 'file:///%61'}], ROOTS * 2,
                                 [{'uri': 'file:///a', 'name': '\x1bsecret'}]])
def test_invalid_root_config_refuses_before_connect_or_implicit_cwd(roots):
    with pytest.raises(ValueError):
        McpServerConfig.parse('owned', {'command': sys.executable, 'roots': roots})


def test_connection_root_consent_snapshot_withdrawal_and_params_never_filesystem_reads():
    capability = RootCapability((('file:///explicit/root', 'Reviewed'),))
    request = {'jsonrpc': '2.0', 'id': 'same', 'method': 'roots/list', 'params': {}}
    assert capability.advertised() == {} and capability.response(request)['error']['code'] == -32601
    capability.active = True
    assert capability.advertised() == {'roots': {'listChanged': True}}
    response = capability.response(request)
    assert response == {'result': {'roots': [{'uri': 'file:///explicit/root', 'name': 'Reviewed'}]}}
    response['result']['roots'].clear()
    assert capability.response(request)['result']['roots']
    assert capability.response({**request, 'params': {'cwd': '/'}})['error']['code'] == -32602
    capability.active = False
    assert capability.response(request)['error']['code'] == -32601


@pytest.mark.parametrize('modern', [False, True])
@pytest.mark.parametrize('consent', [False, True])
async def test_actual_stdio_explicit_roots_versioned_declaration_and_disconnect_withdrawal(tmp_path, modern, consent):
    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    write_peer(peer)
    config = {'command': sys.executable, 'args': [str(peer), str(log), 'modern' if modern else 'legacy']}
    if consent:
        config['roots'] = ROOTS
    manager = McpManager({'owned': config}, tmp_path)
    try:
        assert not log.exists()
        await manager.connect('owned')
        client = manager._clients['owned']
        status = manager.status()[0]
        assert status['client_capabilities'] == (
            {'roots': {} if modern else {'listChanged': True}} if consent else {})
        await manager._bridge(client.request('ping', {}))  # Barrier: reverse response reached actual peer.
        records = [json.loads(line) for line in log.read_text().splitlines()]
        if not modern:
            init = next(r for r in records if r.get('method') == 'initialize')
            assert init['params']['capabilities'] == ({'roots': {'listChanged': True}} if consent else {})
            reverse = next(r for r in records if r.get('id') == 'roots-query')
            assert reverse == ({'jsonrpc': '2.0', 'id': 'roots-query', 'result': {'roots': ROOTS}}
                               if consent else {'jsonrpc': '2.0', 'id': 'roots-query', 'error': {
                                   'code': -32601, 'message': 'Client capability not supported'}})
        assert not any(r.get('method') in {'resources/read', 'tools/call'} for r in records)
        await manager.disconnect('owned')
        assert not client.root_capability.active and manager.status()[0]['client_capabilities'] == {}
    finally:
        await manager.aclose()


async def test_reused_transport_modern_discovery_empty_then_request_owned_roots(tmp_path):
    from xdog.agent.mcp.stdio import StdioClient
    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    write_peer(peer)
    config = McpServerConfig.parse('owned', {'command': sys.executable,
        'args': [str(peer), str(log), 'legacy'], 'roots': ROOTS})
    client = StdioClient(config, tmp_path)
    try:
        await client.connect()
        assert client.root_capability.active and not client.modern
        await client.close()
        client.config = McpServerConfig.parse('owned', {'command': sys.executable,
            'args': [str(peer), str(log), 'modern'], 'roots': ROOTS})
        await client.connect()
        assert client.modern and client.root_capability.active
        assert not client.reverse.active and client.inputs.advertised() == {'roots': {}}
        assert await client.request('tools/list', {}) == {'tools': []}
    finally:
        await client.close()


@pytest.mark.parametrize('fault', ['none', 'foreign-lease', 'cancel-review', 'expired', 'revoked', 'reconnect'])
async def test_real_stdio_dynamic_root_review_single_use_withdraw_and_notify(tmp_path, fault):
    import time

    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    write_peer(peer)
    # Each ping requests a fresh roots list so notification/control effects are
    # observed through the actual peer, not only internal capability objects.
    peer.write_text(peer.read_text().replace("    elif method=='ping':\n", """    elif method=='ping':
        send({'jsonrpc':'2.0','id':'dynamic-'+str(value['id']),'method':'roots/list','params':{}})
"""))
    manager = McpManager({'owned': {'command': sys.executable, 'args': [str(peer), str(log), 'legacy'],
                                  'roots': ROOTS}}, tmp_path)
    alive = True
    try:
        await manager.connect('owned')
        client = manager._clients['owned']
        with pytest.raises(Exception, match='configured disclosure'):
            await manager.manage_roots('owned', 'prepare', [{'uri': 'file:///not-configured'}],
                                       lease='view', alive=lambda: alive)
        review = await manager.manage_roots('owned', 'prepare', [], lease='view', alive=lambda: alive)
        assert review['state'] == 'review' and review['roots'] == []
        assert client.root_capability.roots  # Preparation is not admission.
        if fault == 'foreign-lease':
            lease = 'another-view'
        else:
            lease = 'view'
        if fault == 'cancel-review':
            await manager.manage_roots('owned', 'cancel', lease='view', alive=lambda: alive)
        elif fault == 'expired':
            def expire():
                stored = manager._root_consents['owned']
                manager._root_consents['owned'] = (*stored[:4], time.monotonic() - 301, stored[5])
            async def expired():
                expire()
            await manager._bridge(expired())
        elif fault == 'revoked':
            alive = False
        elif fault == 'reconnect':
            await manager.disconnect('owned')
            await manager.connect('owned')
        if fault != 'none':
            with pytest.raises(Exception):
                await manager.manage_roots('owned', 'commit', receipt=review['review_id'],
                                           lease=lease, alive=lambda: alive)
            assert manager._clients['owned'].root_capability.roots
        else:
            result = await manager.manage_roots('owned', 'commit', receipt=review['review_id'],
                                               lease=lease, alive=lambda: alive)
            assert result['roots'] == [] and result['revision'] == 1
            assert client.root_capability.advertised() == {'roots': {'listChanged': True}}
            with pytest.raises(Exception, match='review'):
                await manager.manage_roots('owned', 'commit', receipt=review['review_id'],
                                           lease=lease, alive=lambda: alive)
            await manager._bridge(client.request('ping', {}))
            await manager._bridge(client.request('ping', {}))  # Barrier observes response from prior ping.
            records = [json.loads(line) for line in log.read_text().splitlines()]
            assert sum(row.get('method') == 'notifications/roots/list_changed' for row in records) == 1
            dynamic = [row for row in records if str(row.get('id', '')).startswith('dynamic-')]
            assert dynamic[0]['result'] == {'roots': []}
            restored = await manager.manage_roots('owned', 'prepare', ROOTS, lease='view', alive=lambda: alive)
            await manager.manage_roots('owned', 'commit', receipt=restored['review_id'],
                                       lease='view', alive=lambda: alive)
            assert client.root_capability.roots == (('file:///explicit/%E6%A0%B9', 'Explicit root'),)
            assert not manager.tools()
    finally:
        await manager.aclose()


async def test_root_publication_cancel_waits_transport_and_never_rolls_back_or_reuses_receipt(tmp_path):
    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    write_peer(peer)
    manager = McpManager({'owned': {'command': sys.executable, 'args': [str(peer), str(log), 'legacy'],
                                  'roots': ROOTS}}, tmp_path)
    entered, settled = __import__('threading').Event(), __import__('threading').Event()
    task = None
    try:
        await manager.connect('owned')
        client = manager._clients['owned']
        original = client.notify
        async def held(method, params=None):
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                settled.set()
        client.notify = held
        review = await manager.manage_roots('owned', 'prepare', [], lease='view', alive=lambda: True)
        task = asyncio.create_task(manager.manage_roots('owned', 'commit', receipt=review['review_id'],
                                                      lease='view', alive=lambda: True))
        assert await asyncio.to_thread(entered.wait, 3)
        assert client.root_capability.roots == ()
        with pytest.raises(Exception, match='publication still owns'):
            await manager.manage_roots('owned', 'prepare', ROOTS, lease='other-view', alive=lambda: True)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert settled.is_set() and client.root_capability.roots == ()
        with pytest.raises(Exception, match='review'):
            await manager.manage_roots('owned', 'commit', receipt=review['review_id'],
                                       lease='view', alive=lambda: True)
        client.notify = original
    finally:
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await manager.aclose()
