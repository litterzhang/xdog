from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool
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
