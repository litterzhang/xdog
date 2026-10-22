"""Actual pinned SDK serving runtime, modern three-method root-only continuations."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
from xdog.agent.mcp import McpRegistry
from xdog.agent.mcp.http import HttpClient

SDK_ROOT = os.environ.get('XDOG_MCP_OFFICIAL_SDK_ROOT')
NODE = shutil.which('node')
PEER = Path(__file__).parent / 'fixtures/mcp_official_sdk/input_peer.mjs'
pytestmark = pytest.mark.skipif(not SDK_ROOT or not NODE, reason='Explicit pinned independent MCP SDK 2.3.1 required')


@pytest.mark.parametrize('transport', ['stdio', 'http'])
@pytest.mark.parametrize('method', ['tools/call', 'resources/read', 'prompts/get'])
async def test_official_modern_input_requests_same_owner_reviews_and_exact_fresh_id_resume(
        tmp_path, transport, method):
    log, process, family = tmp_path / 'frames.jsonl', None, 'roots'
    if transport == 'http':
        process = await asyncio.create_subprocess_exec(NODE, str(PEER), transport, str(log),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(5):
            port = json.loads(await process.stdout.readline())['port']
        config = {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp', 'timeout': 5}
    else:
        config = {'command': NODE, 'args': [str(PEER), transport, str(log)], 'timeout': 5}
    config['roots'] = [{'uri': 'file:///explicit', 'name': 'Explicit'}]
    manager, work = McpRegistry({'sdk': config}, tmp_path), None
    try:
        await manager.connect('sdk')
        client = manager._clients['sdk']
        assert client.modern and not hasattr(client, 'reverse')
        assert manager.status()[0]['client_capabilities'] == {
            'roots': {}}
        params = {'uri': 'test://' + family} if method == 'resources/read' else {'name': family}
        if method == 'tools/call':
            params['arguments'] = {}
        params['_meta'] = {'fixture/trace': 'ORIGINAL'}
        mirrors = {'Mcp-Name': 'test://' + family} if method == 'resources/read' else {'Mcp-Name': family}
        operation = (client.request(method, params, mirrors=mirrors) if isinstance(client, HttpClient)
                     else client.request(method, params))
        work = asyncio.create_task(manager._bridge(operation))
        result = await asyncio.wait_for(work, 5)
        assert 'APPROVED-' + family in json.dumps(result)
        records = [json.loads(line) for line in log.read_text().splitlines()]
        requests = [row['message'] for row in records if row['type'] == 'inbound'
                    and row['message'].get('method') == method]
        assert len(requests) == 2 and requests[0]['id'] != requests[1]['id']
        before, after = requests[0]['params'], requests[1]['params']
        assert {key: value for key, value in after.items() if key not in ('requestState', 'inputResponses')} == before
        assert after['requestState'] == '  opaque\x00state/汉字\n  '
        assert after['_meta']['fixture/trace'] == 'ORIGINAL'
        assert 'tasks' not in before['_meta']['io.modelcontextprotocol/clientCapabilities']
        assert after['inputResponses'] == {'review': {'roots': [{'uri': 'file:///explicit', 'name': 'Explicit'}]}}
        assert not set(after['inputResponses']['review']) & {'method', 'result', 'id', 'jsonrpc'}
        assert not client.inputs._jobs
        if transport == 'http':
            frames = [row for row in records if row['type'] == 'inbound' and row['message'].get('method') == method]
            assert frames[0]['headers']['mcp-method'] == frames[1]['headers']['mcp-method'] == method
    finally:
        await manager.aclose()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        if process is not None:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)
