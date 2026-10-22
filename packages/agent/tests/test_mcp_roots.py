"""Explicit legacy root metadata consent; no automatic workspace/filesystem grants."""
from __future__ import annotations

import json
import sys

import pytest
from xdog.agent.mcp import McpRegistry
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
    assert capability.advertised() == {'roots': {}}
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
    manager = McpRegistry({'owned': config}, tmp_path)
    try:
        assert not log.exists()
        await manager.connect('owned')
        client = manager._clients['owned']
        status = manager.status()[0]
        assert status['client_capabilities'] == (
            {'roots': {}} if consent else {})
        await manager._bridge(client.request('ping', {}))  # Barrier: reverse response reached actual peer.
        records = [json.loads(line) for line in log.read_text().splitlines()]
        if not modern:
            init = next(r for r in records if r.get('method') == 'initialize')
            assert init['params']['capabilities'] == ({'roots': {}} if consent else {})
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
        assert not hasattr(client, 'reverse') and client.inputs.advertised() == {'roots': {}}
        assert await client.request('tools/list', {}) == {'tools': []}
    finally:
        await client.close()


def test_internal_root_fence_cannot_expand_ceiling_or_use_stale_revision():
    root = RootCapability((('file:///explicit/root', 'Explicit'),))
    root.active = True
    root.replace((), root.revision)
    assert root.roots == () and root.ceiling == (('file:///explicit/root', 'Explicit'),)
    with pytest.raises(ValueError, match='revision'):
        root.replace(root.ceiling, 0)
    with pytest.raises(ValueError, match='configured disclosure consent'):
        root.replace((('file:///not-configured', 'Other'),), root.revision)
    root.active = False
    with pytest.raises(ValueError, match='owner'):
        root.replace(root.ceiling, root.revision)


async def test_config_connect_disconnect_is_only_public_root_disclosure_boundary(tmp_path):
    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    write_peer(peer)
    manager = McpRegistry({'owned': {'command': sys.executable, 'args': [str(peer), str(log), 'legacy'],
                                  'roots': ROOTS}}, tmp_path)
    try:
        assert not hasattr(manager, 'manage_roots') and not hasattr(manager, 'detach_roots')
        assert not log.exists()
        await manager.connect('owned')
        original = manager._clients['owned']
        assert original.root_capability.roots == original.root_capability.ceiling
        assert original.root_capability.active
        await manager.disconnect('owned')
        assert not original.root_capability.active
        await manager.connect('owned')
        successor = manager._clients['owned']
        assert successor is not original and successor.root_capability.active
        assert successor.root_capability.roots == successor.root_capability.ceiling
        assert not original.root_capability.active
        assert await manager._bridge(successor.request('ping', {})) == {}
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any(row.get('method') in {'notifications/roots/list_changed', 'tools/call'} for row in frames)
    finally:
        await manager.aclose()


@pytest.mark.parametrize('params', [{'task': {}}, {'_meta': {
    'io.modelcontextprotocol/related-task': {'taskId': 'forged'}}}])
def test_root_disclosure_never_accepts_task_augmentation_as_authority(params):
    roots = RootCapability((('file:///explicit/root', 'Explicit'),))
    roots.active = True
    packet = {'jsonrpc': '2.0', 'id': 'old', 'method': 'roots/list', 'params': params}
    assert roots.response(packet)['error']['code'] == -32602
    assert roots.roots == roots.ceiling and roots.revision == 0
