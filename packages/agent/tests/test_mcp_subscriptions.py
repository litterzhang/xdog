"""Retired subscription APIs cannot open channels; ordinary transport ownership remains."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import runpy
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpError, McpRegistry
from xdog.agent.mcp.resource_metadata import ResourceMetadataError, resource_uri

PEER = Path(__file__).parent / 'fixtures/mcp_subscription_peer.py'
ResumePeer = runpy.run_path(str(Path(__file__).parent / 'fixtures/mcp_resume_peer.py'))['ResumePeer']
METHODS = ('subscriptions/listen', 'resources/subscribe', 'resources/unsubscribe')


def frames(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.mark.parametrize('mode', ['modern', 'legacy'])
async def test_watch_api_registry_and_streams_absent_while_ordinary_operations_remain(tmp_path, mode):
    log = tmp_path / 'frames'
    manager = McpRegistry({'peer': {'command': sys.executable, 'args': [str(PEER), mode, str(log)]}}, tmp_path)
    try:
        assert importlib.util.find_spec('xdog.agent.mcp.subscriptions') is None
        for name in ('watch_resource', 'watch_catalog', 'watch_notifications', 'unwatch_resource', 'unwatch_catalog',
                     'unwatch_notifications', 'detach_resource_watches', 'detach_catalog_watches',
                     'detach_notification_watches', '_resource_watches', '_resource_monitor', '_resource_alive'):
            assert not hasattr(manager, name)
        await manager.connect('peer')
        client = manager._clients['peer']
        for name in ('watch_resource', 'watch_catalog', 'watch_notifications', 'stop_resource_watch', 'resources'):
            assert not hasattr(client, name)
        assert 'ordinary resource' in (await manager.resource('peer', 'test://explicit')).text
        assert 'ordinary prompt' in (await manager.prompt('peer', 'review', {})).text
        assert (await manager.tools()[0].execute('call', {'value': 'explicit tool'})).content[0].text == 'explicit tool'
        assert not any(row.get('method') in METHODS for row in frames(log))
        assert not client._pending and not client.inputs._jobs
    finally:
        await manager.aclose()
    assert client.process is None and not manager._thread.is_alive()


@pytest.mark.parametrize('mode', ['modern', 'legacy'])
@pytest.mark.parametrize('method', METHODS)
async def test_removed_raw_watch_protocol_rejected_before_wire_or_sequence_mutation(tmp_path, mode, method):
    log = tmp_path / 'frames'
    manager = McpRegistry({'peer': {'command': sys.executable, 'args': [str(PEER), mode, str(log)]}}, tmp_path)
    try:
        await manager.connect('peer')
        client = manager._clients['peer']
        before, sequence = frames(log), client._sequence
        for operation in (client.request, client.notify):
            with pytest.raises(McpError, match='not supported') as error:
                await manager._bridge(operation(method, {'notifications': {'toolsListChanged': True}}))
            assert error.value.code == -32601
        assert frames(log) == before and client._sequence == sequence and not client._pending
        assert await manager._bridge(client.request('ping', {})) == {}
    finally:
        await manager.aclose()


@pytest.mark.parametrize('value', [None, False, '', 'x' * 8193, 'test://bad\0uri', 'test://bad\nuri', '\ud800'])
def test_extracted_uri_validation_remains_bounded_and_inert(value):
    with pytest.raises(ResourceMetadataError):
        resource_uri(value)


def test_uri_is_opaque_metadata_not_a_path_or_fetch_grant():
    assert resource_uri('opaque://{not-expanded}/../../中文') == 'opaque://{not-expanded}/../../中文'
    assert resource_uri('https://must-not-fetch.invalid/document') == 'https://must-not-fetch.invalid/document'


@pytest.mark.parametrize('initialize_resume', [False, True])
@pytest.mark.parametrize('value', ['partial', 'broken', 'terminal'])
async def test_retained_legacy_cursor_get_only_recovers_original_result_without_background_stream(
    tmp_path, initialize_resume, value,
):
    with ResumePeer(initialize_resume=initialize_resume) as peer:
        manager = McpRegistry({'http': {'transport': 'http', 'url': peer.url, 'timeout': 2}}, tmp_path)
        try:
            await manager.connect('http')
            client = manager._clients['http']
            assert not hasattr(client, '_background') and not hasattr(client, '_resource_streams')
            assert len(peer.gets) == int(initialize_resume)
            assert (await manager.tools()[0].execute('call', {'value': value})).content[0].text == 'RESUMED-' + value
            assert len([row for row, _ in peer.requests if row.get('method') == 'tools/call']) == 1
            assert len(peer.gets) == 1 + int(initialize_resume)
            assert all('Last-Event-ID' in headers for headers in peer.gets)
            last = peer.gets[-1]
            assert last['Mcp-Session-Id'] == 'resume-session' and last['MCP-Protocol-Version'] == '2025-11-25'
            assert last['Last-Event-ID'].encode('latin-1').decode('utf-8').endswith('界')
        finally:
            await manager.aclose()
        assert peer.deletes == 1 and not manager._thread.is_alive()


@pytest.mark.parametrize('value', ['noid', 'emptyid', 'invalid-json', 'auth', 'redirect', 'expired',
                                 'unavailable', 'exhaust', 'reset', 'mismatch'])
async def test_retained_response_recovery_refuses_unsafe_or_uncertain_results_without_post_replay(tmp_path, value):
    with ResumePeer() as peer:
        manager = McpRegistry({'http': {'transport': 'http', 'url': peer.url, 'timeout': 2}}, tmp_path)
        try:
            await manager.connect('http')
            assert peer.gets == []
            with pytest.raises(McpError) as error:
                await manager.tools()[0].execute('call', {'value': value})
            assert 'do-not-leak-token' not in str(error.value)
            assert len([row for row, _ in peer.requests if row.get('method') == 'tools/call']) == 1
            assert len([row for row, _ in peer.requests if row.get('method') == 'initialize']) == 1
            assert len(peer.gets) <= 3 and all('Last-Event-ID' in headers for headers in peer.gets)
        finally:
            await manager.aclose()


@pytest.mark.parametrize('end', ['cancel', 'timeout', 'close'])
async def test_retained_response_get_cancellation_cleanup_never_replays_call(tmp_path, end):
    with ResumePeer() as peer:
        manager = McpRegistry({'http': {'transport': 'http', 'url': peer.url,
                                      'timeout': .4 if end == 'timeout' else 2}}, tmp_path)
        task = None
        try:
            await manager.connect('http')
            client = manager._clients['http']
            cancel = asyncio.Event()
            task = asyncio.create_task(manager.tools()[0].execute('call', {'value': 'cancel'}, cancel))
            assert await asyncio.to_thread(peer.resume_started.wait, 2)
            if end == 'cancel':
                cancel.set()
            elif end == 'close':
                await manager.aclose()
            with pytest.raises((asyncio.CancelledError, McpError)):
                await asyncio.wait_for(task, 3)
            assert len([row for row, _ in peer.requests if row.get('method') == 'tools/call']) == 1
            assert len(peer.gets) == 1 and not client._active
        finally:
            peer.release.set()
            await manager.aclose()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
