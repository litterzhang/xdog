"""Host-only configured-subset selection, shared model/executor inventory and CAS."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


def tools(executed):
    async def run(identifier, args, *rest, **kwargs):
        executed.append(identifier)
        return AgentToolResult(content=(TextContent(text='OK'),))
    return tuple(AgentTool(name=name, description=name, parameters={'type': 'object'}, execute=run)
                 for name in ('allowed', 'excluded'))


def test_selection_is_configured_subset_cas_and_survives_refresh_without_growing():
    configured = tools([])
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), tools=configured)
    original_messages = agent.messages
    revision = agent.tool_selection_revision
    agent.set_active_tools(['allowed'], expected_revision=revision)
    assert agent.active_tools == (configured[0],) and agent.state.tools == configured
    with pytest.raises(ValueError, match='changed'):
        agent.set_active_tools(None, expected_revision=revision)
    agent.set_tools((*configured, AgentTool(name='new_tool', parameters={})))
    assert [tool.name for tool in agent.active_tools] == ['allowed']
    agent.set_tools((configured[1],))
    assert agent.active_tools == ()
    agent.set_tools(configured)
    assert agent.active_tools == ()  # Removed/re-added alias does not regain selection.
    agent.set_active_tools(None, expected_revision=agent.tool_selection_revision)
    assert agent.active_tools == configured
    agent.set_active_tools([], expected_revision=agent.tool_selection_revision)
    agent.reset()
    assert agent.active_tools == ()  # Message reset is not host tool-selection consent.
    assert agent.messages == original_messages


def test_bad_selection_never_changes_configured_or_active_tools() -> None:

    def _check_case(names):
        agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), tools=tools([]))
        before = (agent.state, agent.active_tools, agent.tool_selection_revision)
        with pytest.raises(ValueError):
            agent.set_active_tools(names, expected_revision=agent.tool_selection_revision)
        assert before == (agent.state, agent.active_tools, agent.tool_selection_revision)

    for names in [["foreign"], ["allowed", "allowed"], [True], "allowed", [""], [1]]:
        _check_case(names)


@pytest.mark.parametrize('mode', ['parallel', 'sequential'])
async def test_unselected_model_tool_call_never_executes_and_selected_still_passes_host_hook(mode):
    from xdog.agent.types import BeforeToolCallResult
    executed, before_calls, contexts = [], [], []
    configured = tools(executed)
    async def before(context, cancel):
        before_calls.append(context.tool_call.name)
        return BeforeToolCallResult(block=True, reason='HOST DENIED')
    def stream(model, context, options):
        contexts.append(context)
        if len(contexts) == 1:
            return EventStream.empty(AssistantMessage(content=(
                ToolCall(id='excluded-call', name='excluded', arguments={}),
                ToolCall(id='allowed-call', name='allowed', arguments={})), stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='DONE'),)))
    agent = Agent(stream, tools=configured, before_tool_call=before,
                  config=AgentConfig(model='offline', tool_execution=mode))
    agent.set_active_tools(['allowed'], expected_revision=0)
    events = await agent.prompt('No unapproved execution')
    async for _ in events:
        pass
    await agent.wait_for_idle()
    assert all([tool.name for tool in context.tools] == ['allowed'] for context in contexts)
    assert not executed and before_calls == ['allowed']
    assert 'not found' in str(agent.messages) and 'HOST DENIED' in str(agent.messages)
    assert agent.state.tools == configured


async def test_active_selection_cannot_change_while_provider_or_cleanup_is_running():
    started, release = asyncio.Event(), asyncio.Event()
    async def transform(messages):
        started.set()
        await release.wait()
        return messages
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), tools=tools([]), transform_context=transform,
                  config=AgentConfig(model='offline'))
    events = await agent.prompt('held')
    try:
        await asyncio.wait_for(started.wait(), 2)
        with pytest.raises(RuntimeError, match='idle'):
            agent.set_active_tools([], expected_revision=0)
        assert len(agent.active_tools) == 2
    finally:
        release.set()
        async for _ in events:
            pass
        await agent.wait_for_idle()
    agent.set_active_tools([], expected_revision=0)
    assert not agent.active_tools


async def test_actual_extension_refresh_keeps_explicit_selection_and_removal_retires_alias(tmp_path):
    import shutil
    from pathlib import Path

    from xdog.agent.extensions.runtime import ExtensionRuntime
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/sample', root / 'sample')
    runtime = ExtensionRuntime(['sample'], root, tmp_path)
    configured = tools([])
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), tools=configured)
    try:
        await runtime.initialize()
        runtime.sync_tools(agent)
        extension = runtime.tools()[0].name
        agent.set_active_tools(['allowed', extension], expected_revision=agent.tool_selection_revision)
        revision = agent.tool_selection_revision
        runtime.sync_tools(agent)
        assert agent.tool_selection_revision == revision
        assert {tool.name for tool in agent.active_tools} == {'allowed', extension}
        assert 'excluded' in {tool.name for tool in agent.state.tools}
        agent.set_active_tools(['allowed'], expected_revision=agent.tool_selection_revision)
        runtime.sync_tools(agent)
        assert [tool.name for tool in agent.active_tools] == ['allowed']
        agent.set_active_tools([extension], expected_revision=agent.tool_selection_revision)
        await runtime.manage('unload', 'sample')
        runtime.sync_tools(agent)
        assert agent.active_tools == ()
        await runtime.manage('reload', 'sample')
        runtime.sync_tools(agent)
        assert extension in {tool.name for tool in agent.state.tools} and not agent.active_tools
    finally:
        await runtime.aclose()
    assert not runtime._manager._thread.is_alive()


@pytest.mark.parametrize('selection', [[], ['allowed'], None])
async def test_selection_does_not_override_model_no_tool_capability(selection):
    contexts = []
    def stream(model, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='DONE'),)))
    agent = Agent(stream, tools=tools([]), config=AgentConfig(model='offline', supports_tool_calls=False))
    agent.set_active_tools(selection, expected_revision=agent.tool_selection_revision)
    events = await agent.prompt('ordinary text')
    async for _ in events:
        pass
    await agent.wait_for_idle()
    assert len(contexts) == 1 and contexts[0].tools is None
    assert len(agent.state.tools) == 2


def test_invalid_selection_revision_and_ambiguous_inventory_are_atomic():
    configured = tools([])
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), tools=configured)
    for revision in (True, None, -1, 1):
        with pytest.raises(ValueError, match='changed'):
            agent.set_active_tools([], expected_revision=revision)
    agent.set_active_tools(['allowed'], expected_revision=0)
    before = agent.state, agent.active_tools, agent.tool_selection_revision
    with pytest.raises(ValueError, match='unique'):
        agent.set_tools((configured[0], configured[0]))
    assert before == (agent.state, agent.active_tools, agent.tool_selection_revision)


async def test_extension_active_tools_api_is_unavailable_without_command_host_context(tmp_path):
    import shutil
    from pathlib import Path

    from xdog.agent.extensions.api import ExtensionAPI
    from xdog.agent.extensions.runtime import ExtensionRuntime
    api = ExtensionAPI({})
    assert await api.get_active_tools() is None
    assert not hasattr(api, 'set_active_tools')
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/toolselector', root / 'toolselector')
    runtime = ExtensionRuntime(['toolselector'], root, tmp_path)
    try:
        await runtime.initialize()
        response = await runtime.tools()[0].execute('call', {})
        assert response.content[0].text == 'NO-HOST-TOOL-AUTHORITY'
        assert 'ext-toolselector-select' not in runtime.commands()
    finally:
        await runtime.aclose()


def test_active_selection_snapshot_preserves_default_vs_explicit_same_inventory():
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), tools=tools([]))
    assert agent.active_tool_selection is None
    agent.set_active_tools(['allowed', 'excluded'], expected_revision=agent.tool_selection_revision)
    assert agent.active_tool_selection == ('allowed', 'excluded')
    agent.set_tools((*agent.state.tools, AgentTool(name='new', parameters={})))
    assert agent.active_tool_selection == ('allowed', 'excluded')
    agent.set_active_tools(None, expected_revision=agent.tool_selection_revision)
    assert agent.active_tool_selection is None and len(agent.active_tools) == 3
