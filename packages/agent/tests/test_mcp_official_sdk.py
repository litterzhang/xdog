"""Opt-in independent serving-SDK interop; no downloads, model calls or live services.

Run with XDOG_MCP_OFFICIAL_SDK_ROOT pointing to an isolated npm install of
@modelcontextprotocol/server@2.3.1. Default CI skip is NOT conformance evidence.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
from xdog.agent.mcp import McpRegistry

SDK_ROOT = os.environ.get('XDOG_MCP_OFFICIAL_SDK_ROOT')
NODE = shutil.which('node')
PEER = Path(__file__).parent / 'fixtures/mcp_official_sdk/peer.mjs'
pytestmark = pytest.mark.skipif(not SDK_ROOT or not NODE,
                               reason='requires explicitly installed independent MCP SDK 2.3.1')


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.003)






@pytest.mark.parametrize('transport', ['stdio', 'http'])
async def test_official_sdk_resource_prompt_template_completion_and_link_preview_are_inert(tmp_path, transport):
    log = tmp_path / 'sdk-context.jsonl'
    process = None
    if transport == 'http':
        process = await asyncio.create_subprocess_exec(NODE, str(PEER), transport, str(log),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(5):
            port = json.loads(await process.stdout.readline())['port']
        config = {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp'}
    else:
        config = {'command': NODE, 'args': [str(PEER), transport, str(log)]}
    manager = McpRegistry({'sdk': config}, tmp_path)
    try:
        await manager.connect('sdk')
        assert len(await manager.catalog('sdk', 'resource')) == 1
        assert (await manager.catalog('sdk', 'prompt'))[0].name == 'review'
        assert (await manager.catalog('sdk', 'template'))[0].name == 'test://document/{name}'
        prompt = await manager.complete('sdk', 'prompt', 'review', 'topic', 'S')
        assert prompt.values == ('SDK-TOPIC', '第二项')
        template = await manager.complete('sdk', 'template', 'test://document/{name}', 'name', 'S')
        assert template.values == ('SDK-DOCUMENT',)
        before = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any(record['type'] in {'resource-read', 'template-read', 'prompt-get'} for record in before)
        document = await manager.resource('sdk', 'test://document/operator-selected')
        assert 'SDK-DOCUMENT:operator-selected' in document.draft_text()
        linked = await manager.prompt('sdk', 'review', {'topic': 'operator-selected'})
        assert 'SDK-RESOURCE-LINK' in linked.text and 'fixture/private' in linked.text
        assert 'https://must-not-fetch.invalid/operator-selected' in linked.draft_text()
        after = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(record['type'] == 'template-read' for record in after) == 1
        assert sum(record['type'] == 'prompt-get' for record in after) == 1
        assert not any(record['type'] in {'tick', 'resource-read'} for record in after)
        assert not any(manager.allowed(tool.name) for tool in manager.tools())
        assert manager.status()[0]['allow_tools'] is False  # Discovery is not an execution grant.
    finally:
        await manager.aclose()
        if process is not None:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)


@pytest.mark.parametrize('transport', ['stdio', 'http'])
async def test_official_sdk_failed_tool_preserves_image_text_and_unsuccessful_structured_diagnostic(
        tmp_path, transport):
    from xdog.ai.types import ImageContent, TextContent

    log = tmp_path / 'sdk-error.jsonl'
    process = None
    if transport == 'http':
        process = await asyncio.create_subprocess_exec(NODE, str(PEER), transport, str(log),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(5):
            port = json.loads(await process.stdout.readline())['port']
        config = {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp'}
    else:
        config = {'command': NODE, 'args': [str(PEER), transport, str(log)]}
    manager = McpRegistry({'sdk': config}, tmp_path)
    try:
        await manager.connect('sdk')
        tool = next(tool for tool in manager.tools() if '/diagnostic' in tool.label)
        result = await tool.execute('error', {})  # Explicit owned test call; no agent permission grant.
        assert result.is_error and any(isinstance(part, ImageContent) for part in result.content)
        text = '\n'.join(part.text for part in result.content if isinstance(part, TextContent))
        assert 'SDK-DIAGNOSTIC-FAILED' in text and 'SDK-STRUCTURED-ERROR-TAIL' in text
        assert result.details['structured_content']['retry'] is False
        assert manager.allowed(tool.name) is False
        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(record['type'] == 'diagnostic' for record in records) == 1
    finally:
        await manager.aclose()
        if process is not None:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)


async def test_public_official_content_schema_corpus_matches_typed_admission_and_inert_previews():
    from xdog.agent.mcp.content import content
    from xdog.agent.mcp.context import prompt_blocks
    from xdog.agent.mcp.stdio import McpError

    process = await asyncio.create_subprocess_exec(NODE, str(PEER.with_name('content_cases.mjs')),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
    assert process.returncode == 0, stderr.decode()
    cases = json.loads(stdout)
    for value in cases['valid']:
        assert content(value, image_projection=False) == value
        assert prompt_blocks([{'role': 'user', 'content': value}])
    for value in cases['invalid']:
        with pytest.raises(McpError):
            content(value)


@pytest.mark.parametrize('transport', ['stdio', 'http'])
async def test_official_serving_sdk_mixed_content_tool_validates_preserves_metadata_and_never_fetches(
    tmp_path, transport,
):
    log = tmp_path / 'sdk-content.jsonl'
    process = None
    if transport == 'http':
        process = await asyncio.create_subprocess_exec(NODE, str(PEER), transport, str(log),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(5):
            port = json.loads(await process.stdout.readline())['port']
        config = {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp'}
    else:
        config = {'command': NODE, 'args': [str(PEER), transport, str(log)]}
    manager = McpRegistry({'sdk': config}, tmp_path)
    try:
        await manager.connect('sdk')
        tool = next(tool for tool in manager.tools() if '/content' in tool.label)
        result = await tool.execute('call', {})
        text = '\n'.join(part.text for part in result.content)
        for marker in ('SDK-CONTENT-TEXT', 'SDK-CONTENT-METADATA', 'SDK-CONTENT-AUDIO',
                       'SDK-CONTENT-INNER', 'SDK-CONTENT-OUTER', 'SDK-CONTENT-LINK'):
            assert marker in text
        assert not result.is_error
        assert result.details['mcp_content_metadata'][0]['metadata']['annotations']['priority'] == 0.5
        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(record['type'] == 'content' for record in records) == 1
        assert not any(record['type'] in ('resource-read', 'template-read', 'prompt-get') for record in records)
    finally:
        await manager.aclose()
        if process is not None:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)


@pytest.mark.parametrize('transport', ['stdio', 'http'])
async def test_official_sdk_subscription_advertisement_cannot_open_any_implicit_watch(tmp_path, transport):
    log = tmp_path / 'sdk-no-watch.jsonl'
    process = None
    if transport == 'http':
        process = await asyncio.create_subprocess_exec(NODE, str(PEER), transport, str(log),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(5):
            port = json.loads(await process.stdout.readline())['port']
        config = {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp'}
    else:
        config = {'command': NODE, 'args': [str(PEER), transport, str(log)]}
    manager = McpRegistry({'sdk': config}, tmp_path)
    try:
        await manager.connect('sdk')
        client = manager._clients['sdk']
        assert client.capabilities['resources']['subscribe'] is True
        for name in ('watch_resource', 'watch_catalog', 'watch_notifications', 'resources', '_resource_streams'):
            assert not hasattr(client, name)
        before = log.read_text()
        for method in ('subscriptions/listen', 'resources/subscribe', 'resources/unsubscribe'):
            from xdog.agent.mcp import McpError
            with pytest.raises(McpError, match='not supported'):
                await manager._bridge(client.request(method, {'uri': 'test://watched'}))
        assert log.read_text() == before  # No denied request reaches the actual serving SDK.
        assert (await manager.catalog('sdk', 'resource'))[0].name == 'test://watched'
        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any(row['type'] in {'resource-read', 'prompt-get', 'completion'} for row in records)
        assert not any(row['type'] == 'inbound' and row['method'] in {
            'subscriptions/listen', 'resources/subscribe', 'resources/unsubscribe'} for row in records)
        if transport == 'http':
            assert not hasattr(client, '_background')
            assert not any(row['type'] == 'http-request' and row['method'] == 'GET' for row in records)
    finally:
        await manager.aclose()
        if process is not None:
            process.terminate()
            await asyncio.wait_for(process.wait(), 5)
