from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.tool_inventory import InventoryCache, capture, fingerprint
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream


def agent(count=1, size=0):
    async def execute(*args):
        pytest.fail('Metadata must not execute tools')
    return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model='model'),
        tools=[AgentTool(name='native_' + str(index), description='definition',
            parameters={'type': 'object', 'properties': {'data': {'description': 'x' * size}}}, execute=execute)
            for index in range(count)])


def test_cache_is_bounded_owned_by_digest_copied_and_retired_refs_never_target_successors():
    cache = InventoryCache()
    owner = agent(size=2000)
    first = capture(owner, 'session', 'child')
    key = cache.remember(first)
    assert key == fingerprint(first)
    first['tools'][0]['parameters'].clear()
    pinned = cache.resolve(key, session_id='session', owner_id='child')
    assert pinned['tools'][0]['parameters']
    pinned['tools'][0]['parameters']['forged'] = True
    assert 'forged' not in cache.resolve(key, session_id='session', owner_id='child')['tools'][0]['parameters']
    with pytest.raises(ValueError):
        cache.resolve(key, session_id='session', owner_id='parent')
    for number in range(9):
        cache.remember(capture(owner, 'session', str(number)))
    assert len(cache._entries) == 8 and cache.resolve(key, session_id='session', owner_id='child') is None
    assert pinned['owner_id'] == 'child'  # An admitted callback keeps its original copied snapshot after eviction.
    for malformed in (None, 'x', 'z' * 64, False):
        with pytest.raises(ValueError):
            cache.resolve(malformed, session_id='session', owner_id='child')


@pytest.mark.parametrize('cached', [False, True], ids=['older-inline', 'negotiated-reference'])
async def test_actual_worker_unchanged_snapshot_wire_budget_replacement_and_reload(tmp_path, cached):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/toolinspector', root / 'toolinspector')
    owner = agent(count=12, size=8192)
    runtime = ExtensionRuntime(['toolinspector'], root, tmp_path, context=lambda: {'session_id': 'session'})
    runtime.tool_inventory = lambda: capture(owner, 'session', 'session')
    frames = []
    async def attach(client):
        original = client._write
        async def write(value):
            frames.append(value)
            await original(value)
        client._write = write
    try:
        await runtime.initialize()
        client = runtime._manager._clients['toolinspector']
        assert client._inventory_cache_supported
        client._inventory_cache_supported = cached
        client._inventories.clear()
        await attach(client)
        first = await runtime.command('ext-toolinspector-inspect', '')
        second = await runtime.command('ext-toolinspector-inspect', '')
        assert json.loads(first.removeprefix('TOOL-INVENTORY '))['count'] == 12
        assert first == second and len(frames) == 2
        one, two = (frame['params']['context'] for frame in frames)
        assert one['host_tool_inventory']['tools']
        if cached:
            assert two['host_tool_inventory'] is None
            assert two['host_tool_inventory_ref'] == fingerprint(one['host_tool_inventory'])
            assert len(json.dumps(frames[1])) < len(json.dumps(frames[0])) / 20
        else:
            assert two['host_tool_inventory'] == one['host_tool_inventory']
            assert two['host_tool_inventory_ref'] is None
        owner.set_tools(owner.state.tools[:1])
        third = json.loads((await runtime.command('ext-toolinspector-inspect', '')).removeprefix('TOOL-INVENTORY '))
        assert third['count'] == 1 and frames[-1]['params']['context']['host_tool_inventory']['tools']
        await runtime.manage('reload', 'toolinspector')
        newer = runtime._manager._clients['toolinspector']
        assert newer is not client and newer._inventories is not client._inventories
        fourth = json.loads((await runtime.command('ext-toolinspector-inspect', '')).removeprefix('TOOL-INVENTORY '))
        assert fourth['count'] == 1 and fourth['available']
        assert not owner.state.messages
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('cached', [False, True])
async def test_optional_inspection_does_not_overflow_valid_large_callback_or_replay_it(tmp_path, cached):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/toolinspector', root / 'toolinspector')
    owner = agent(count=48, size=8192)
    runtime = ExtensionRuntime(['toolinspector'], root, tmp_path, context=lambda: {'session_id': 'session'}, timeout=5)
    runtime.tool_inventory = lambda: capture(owner, 'session', 'session')
    frames = []
    try:
        await runtime.initialize()
        client = runtime._manager._clients['toolinspector']
        client._inventory_cache_supported = cached
        client._inventories.clear()
        original = client._write
        async def write(value):
            frames.append(value)
            await original(value)
        client._write = write
        text = 'a' * (8 * 1024 * 1024 - 10000)
        output = await runtime.command('ext-toolinspector-inspect', text)
        result = json.loads(output.removeprefix('TOOL-INVENTORY '))
        assert not result['available']
        assert len(frames) == 1 and frames[0]['params']['args'] == text
        assert frames[0]['params']['context']['host_tool_inventory'] is None
        assert frames[0]['params']['context']['host_tool_inventory_ref'] is None
        assert not client._inventories and runtime.snapshot()['extensions'][0]['state'] == 'active'
        # A smaller later call restores normal inventory admission rather than
        # remembering a snapshot that this worker never received.
        result = json.loads((await runtime.command('ext-toolinspector-inspect', '')).removeprefix('TOOL-INVENTORY '))
        assert result['available'] and result['count'] == 48 and len(frames) == 2
    finally:
        await runtime.aclose()


async def test_actual_worker_cache_miss_is_unavailable_and_repairs_next_invocation_without_replay(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/toolinspector', root / 'toolinspector')
    owner = agent()
    runtime = ExtensionRuntime(['toolinspector'], root, tmp_path, context=lambda: {'session_id': 'session'})
    runtime.tool_inventory = lambda: capture(owner, 'session', 'session')
    frames = []
    try:
        await runtime.initialize()
        client = runtime._manager._clients['toolinspector']
        owner.set_model('not-yet-cached')
        reference = fingerprint(capture(owner, 'session', 'session'))
        client._inventories[reference] = None  # Simulate coherent bounded-cache eviction/race.
        original = client._write
        async def write(value):
            frames.append(value)
            await original(value)
        client._write = write
        output = json.loads((await runtime.command('ext-toolinspector-inspect', '')).removeprefix('TOOL-INVENTORY '))
        assert not output['available'] and len(frames) == 1 and reference not in client._inventories
        output = json.loads((await runtime.command('ext-toolinspector-inspect', '')).removeprefix('TOOL-INVENTORY '))
        assert output['available'] and output['model'] == 'not-yet-cached' and len(frames) == 2
        assert frames[0]['params']['context']['host_tool_inventory_ref'] == reference
        assert frames[1]['params']['context']['host_tool_inventory']['model'] == 'not-yet-cached'
        assert runtime.snapshot()['extensions'][0]['state'] == 'active' and not owner.state.messages
    finally:
        await runtime.aclose()
