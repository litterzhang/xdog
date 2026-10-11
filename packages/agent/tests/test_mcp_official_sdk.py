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
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.http import HttpClient

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
async def test_official_sdk_discovery_catalog_watch_notices_unwatch_and_graceful_close(tmp_path, transport):
    log = tmp_path / 'sdk-frames.jsonl'
    process = None
    if transport == 'http':
        process = await asyncio.create_subprocess_exec(NODE, str(PEER), transport, str(log),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(5):
            port = json.loads(await process.stdout.readline())['port']
        config = {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp'}
    else:
        config = {'command': NODE, 'args': [str(PEER), transport, str(log)]}
    manager = McpManager({'sdk': config}, tmp_path)
    first, second = [], []
    try:
        await manager.connect('sdk')
        assert manager.status()[0]['protocol'] == '2026-07-28'
        assert manager.status()[0]['allow_tools'] is False
        catalog = await manager.catalog('sdk', 'resource')
        assert catalog[0].name == 'test://watched'
        await manager.watch_resource('sdk', 'test://watched', lease='one', on_change=first.append, alive=lambda: True)
        await manager.watch_resource('sdk', 'test://watched', lease='two', on_change=second.append, alive=lambda: True)
        client = manager._clients['sdk']
        async def control(name):
            params = {'name': name, 'arguments': {}}
            operation = (client.request('tools/call', params, mirrors={'Mcp-Name': name})
                         if isinstance(client, HttpClient) else client.request('tools/call', params))
            return await manager._bridge(operation)
        await control('tick')
        await until(lambda: first and second and first[-1]['changes'] == second[-1]['changes'] == 1)
        await manager.detach_resource_watches('one')
        before = len(first)
        await control('tick')
        await until(lambda: second[-1]['changes'] == 2)
        assert len(first) == before
        await control('finish')
        await until(lambda: second[-1]['state'] == 'ended')
        assert not client.resources.watches
        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any(record['type'] == 'resource-read' for record in records)
        if transport == 'stdio':
            results = [record['message']['result'] for record in records if record['type'] == 'outbound'
                       and record['message'].get('id', 0) == -2 and 'result' in record['message']]
            assert results[0]['resultType'] == 'complete'
            assert results[0]['_meta']['io.modelcontextprotocol/subscriptionId'] == -2
            assert results[0]['_meta']['io.modelcontextprotocol/serverInfo']['name'] == 'official-sdk-peer'
        else:
            methods = [record['method'] for record in records if record['type'] == 'inbound']
            assert methods.count('subscriptions/listen') == 2 and 'resources/read' not in methods
    finally:
        await manager.aclose()
        if process is not None:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)


async def test_public_official_schema_generated_ack_and_empty_result_are_accepted_without_new_filters():
    from xdog.agent.mcp.subscriptions import ResourceSubscriptions

    process = await asyncio.create_subprocess_exec(NODE, str(PEER.with_name('schema_cases.mjs')),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
    assert process.returncode == 0, stderr.decode()
    frames = json.loads(stdout)
    router = ResourceSubscriptions()

    async def stop(watch):
        router.remove(watch)

    watch = router.add('test://watched', lambda _: None, stop)
    assert router.modern_frame(frames['acknowledgement']) and watch.state == 'active'
    assert router.modern_frame(frames['completion']) and watch.state == 'ended'
    assert not router.watches


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
    manager = McpManager({'sdk': config}, tmp_path)
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
    manager = McpManager({'sdk': config}, tmp_path)
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
    manager = McpManager({'sdk': config}, tmp_path)
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
@pytest.mark.parametrize('kind', ['tools', 'prompts', 'resources'])
async def test_official_sdk_catalog_filter_notice_cleanup_does_not_retrieve_or_register(tmp_path, transport, kind):
    log = tmp_path / 'catalog.jsonl'
    process = None
    if transport == 'http':
        process = await asyncio.create_subprocess_exec(NODE, str(PEER), transport, str(log),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(5):
            port = json.loads(await process.stdout.readline())['port']
        config = {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp'}
    else:
        config = {'command': NODE, 'args': [str(PEER), transport, str(log)]}
    manager = McpManager({'sdk': config}, tmp_path)
    events = []
    try:
        await manager.connect('sdk')
        before = log.read_text().splitlines()
        watch = await manager.watch_catalog('sdk', kind, lease='one', alive=lambda: True, on_change=events.append)
        assert watch['catalog'] == kind and watch['state'] == 'active'
        async def tick():
            client = manager._clients['sdk']
            if isinstance(client, HttpClient):
                await client.request('tools/call', {'name': 'catalogTick', 'arguments': {'kind': kind}},
                                     mirrors={'Mcp-Name': 'catalogTick'})
            else:
                await client.request('tools/call', {'name': 'catalogTick', 'arguments': {'kind': kind}})
        await manager._bridge(tick())
        await until(lambda: bool(events))
        assert events[-1]['catalog'] == kind and events[-1]['changes'] == 1
        after = [json.loads(line) for line in log.read_text().splitlines()[len(before):]]
        assert not any(record['type'] in ('resource-read', 'template-read', 'prompt-get') for record in after)
        if transport == 'http':
            assert not any(record.get('method') in ('tools/list', 'resources/list', 'prompts/list') for record in after)
        await manager.detach_resource_watches('one')
        assert not manager._resource_watches and not manager._clients['sdk'].resources.watches
    finally:
        await manager.aclose()
        if process is not None:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)


@pytest.mark.parametrize('transport', ['stdio', 'http'])
async def test_official_sdk_combined_catalog_and_multiple_uri_filters_one_stream(tmp_path, transport):
    log = tmp_path / 'sdk-combined.jsonl'
    process = None
    if transport == 'http':
        process = await asyncio.create_subprocess_exec(NODE, str(PEER), transport, str(log),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(5):
            port = json.loads(await process.stdout.readline())['port']
        config = {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp'}
    else:
        config = {'command': NODE, 'args': [str(PEER), transport, str(log)]}
    manager, notices = McpManager({'sdk': config}, tmp_path), []
    selected = {'toolsListChanged': True, 'promptsListChanged': True, 'resourcesListChanged': True,
                'resourceSubscriptions': ['test://second', 'test://watched']}
    try:
        await manager.connect('sdk')
        watch = await manager.watch_notifications('sdk', selected, lease='view', on_change=notices.append,
                                                  alive=lambda: True)
        assert watch['notifications'] == selected and watch['state'] == 'active'
        client = manager._clients['sdk']
        async def control(name, arguments):
            params = {'name': name, 'arguments': arguments}
            operation = (client.request('tools/call', params, mirrors={'Mcp-Name': name})
                         if isinstance(client, HttpClient) else client.request('tools/call', params))
            await manager._bridge(operation)
        for kind in ('tools', 'prompts', 'resources'):
            await control('catalogTick', {'kind': kind})
        for uri in selected['resourceSubscriptions']:
            await control('resourceTick', {'uri': uri})
        await until(lambda: notices and notices[-1]['changes'] == 5)
        await control('resourceTick', {'uri': 'test://unrequested'})
        await asyncio.sleep(0.1)
        assert notices[-1]['changes'] == 5
        result = await manager.unwatch_notifications('sdk', selected, lease='view')
        assert result['state'] == 'stopped' and not client.resources.watches
        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any(record['type'] in ('resource-read', 'template-read', 'prompt-get') for record in records)
        if transport == 'stdio':
            ack = [record['message'] for record in records if record['type'] == 'outbound'
                   and record['message'].get('method') == 'notifications/subscriptions/acknowledged']
            assert len(ack) == 1 and ack[0]['params']['notifications'] == selected
        else:
            listens = [record for record in records if record['type'] == 'inbound'
                       and record['method'] == 'subscriptions/listen']
            assert len(listens) == 1 and listens[0]['params']['notifications'] == selected
    finally:
        await manager.aclose()
        if process is not None:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)
