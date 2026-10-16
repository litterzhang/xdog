"""Active-tool inspection remains captured/read-only; after-run mutation adapters are gone."""
from __future__ import annotations

from xdog.agent import Agent, AgentConfig, AgentTool
from xdog.agent.extensions.active_tools import snapshot
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.tool_inventory import capture
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream


async def test_get_active_tools_reuses_captured_original_names_without_a_new_host_dialog():
    api = ExtensionAPI({})
    assert await api.get_active_tools() is None
    safe = AgentTool(name='safe', description='safe', parameters={})
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()),
                  config=AgentConfig(model='fixture'), tools=[safe])
    token = api._tool_inventory.set(capture(agent, 'original', 'child-owner'))
    try:
        value = await api.get_active_tools()
        assert value == snapshot(agent, 'original')
        value['active'].clear()
        assert (await api.get_active_tools())['active'] == ['safe']
        assert not hasattr(api, 'set_active_tools')
    finally:
        api._tool_inventory.reset(token)
