"""Agent consumes MCP separately; ordinary tool registration never owns MCP."""
from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.mcp import McpRegistry
from xdog.agent.tools import ToolRepo
from xdog.agent.types import BeforeToolCallResult
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from xdog.ai.utils.event_stream import EventStream


def tool(name, calls=None):
    async def execute(*args, **kwargs):
        if calls is not None:
            calls.append(name)
        return AgentToolResult.from_text(name + ' result')
    return AgentTool(name=name, description=name, parameters={'type': 'object'}, execute=execute)


def model_for(name, contexts):
    def stream(_model, context, _options):
        contexts.append(context)
        if context.messages[-1].role == 'user':
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='remote-call', name=name, arguments={}),)))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    return stream


async def drain(agent, text='run'):
    async for _ in await agent.prompt(text):
        pass


async def test_mcp_uses_same_loop_and_hooks_but_not_ordinary_registry():
    calls, contexts, approvals = [], [], []
    remote = tool('remote', calls)
    mcp = McpRegistry.from_tools((remote,))
    local = ToolRepo(builtin=False)
    local.register(tool('local'))
    async def before(ctx, cancel=None):
        approvals.append(ctx.tool_call.name)
    agent = Agent(model_for('remote', contexts), config=AgentConfig(model='offline'),
                  tools=local.tools, mcp_registry=mcp, before_tool_call=before)
    try:
        assert mcp._loop is None and mcp._thread is None
        local.publish(agent)
        assert [item.name for item in local.tools] == ['local']
        assert [item.name for item in agent.local_tools] == ['local']
        assert [item.name for item in agent.state.tools] == ['local', 'remote']
        await drain(agent)
        assert calls == approvals == ['remote']
        assert len(contexts) == 2
        assert [item.name for item in contexts[0].tools] == ['local', 'remote']
        assert any(isinstance(item, ToolResultMessage) for item in agent.messages)
        assert mcp._loop is None and mcp._thread is None
    finally:
        await agent.aclose()
        assert mcp.tools() == (remote,)  # Agent borrows, never closes host resources.
        await mcp.aclose()


async def test_mcp_calls_still_obey_permission_denial():
    calls, contexts = [], []
    registry = McpRegistry.from_tools((tool('remote', calls),))
    async def deny(ctx, cancel=None):
        return BeforeToolCallResult(block=True, reason='host denied')
    agent = Agent(model_for('remote', contexts), config=AgentConfig(model='offline'),
                  mcp_registry=registry, before_tool_call=deny)
    try:
        await drain(agent)
        assert not calls
        assert any(isinstance(item, ToolResultMessage) and item.is_error for item in agent.messages)
    finally:
        await agent.aclose()
        await registry.aclose()


async def test_local_publication_does_not_drop_remote_selection_or_rewrite_revision():
    registry = McpRegistry.from_tools((tool('remote'),))
    local = ToolRepo(builtin=False)
    local.register(tool('local'))
    agent = Agent(lambda *_: EventStream.empty(AssistantMessage()), config=AgentConfig(model='offline'),
                  tools=local.tools, mcp_registry=registry)
    try:
        agent.set_active_tools(('remote',), expected_revision=agent.tool_selection_revision)
        revision = agent.tool_selection_revision
        local.publish(agent)
        agent.sync_mcp_tools()
        assert revision == agent.tool_selection_revision
        registry.replace_delegated_tools(())
        agent.sync_mcp_tools()
        assert agent.active_tool_selection == ()
        registry.replace_delegated_tools((tool('remote'),))
        agent.sync_mcp_tools()
        assert agent.active_tool_selection == ()
        assert [item.name for item in agent.local_tools] == ['local']
    finally:
        await agent.aclose()
        await registry.aclose()


async def test_cross_registry_name_conflicts_are_atomic_and_never_silently_override():
    local, remote = tool('same'), tool('same')
    registry = McpRegistry.from_tools((remote,))
    agent = Agent(lambda *_: EventStream.empty(AssistantMessage()), tools=(local,))
    try:
        with pytest.raises(ValueError, match='Duplicate tool name'):
            agent.set_mcp_registry(registry)
        assert agent.mcp_registry is None and agent.state.tools == agent.local_tools == (local,)
        agent.set_tools(())
        agent.set_mcp_registry(registry)
        with pytest.raises(ValueError, match='Duplicate tool name'):
            agent.set_tools((local,))
        assert agent.local_tools == () and agent.state.tools == (remote,)
        agent.set_mcp_registry(None)
        assert agent.state.tools == ()
    finally:
        await agent.aclose()
        await registry.aclose()


async def test_mcp_configuration_is_not_snapshot_authority_or_automatic_connection(tmp_path):
    registry = McpRegistry(cwd=tmp_path)
    registry.register('server', {'command': 'never-run-this-program'})
    agent = Agent(lambda *_: EventStream.empty(AssistantMessage(content=(TextContent(text='done'),))),
                  config=AgentConfig(model='offline'), mcp_registry=registry)
    target_registry = McpRegistry.from_tools((tool('current'),))
    target = Agent(lambda *_: EventStream.empty(AssistantMessage()), mcp_registry=target_registry)
    try:
        await drain(agent)
        assert registry._loop is None and registry._thread is None
        snapshot = agent.dump()
        assert 'mcp' not in snapshot and 'mcp_registry' not in snapshot
        assert 'never-run-this-program' not in str(snapshot)
        target.restore(snapshot)
        assert target.mcp_registry is target_registry
        assert [item.name for item in target.state.tools] == ['current']
    finally:
        await agent.aclose()
        await target.aclose()
        await registry.aclose()
        await target_registry.aclose()


async def test_next_model_request_refreshes_mcp_without_local_publication():
    contexts = []
    registry = McpRegistry.from_tools((tool('old'),))
    def stream(_model, context, _options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage())
    agent = Agent(stream, config=AgentConfig(model='offline'), mcp_registry=registry)
    try:
        registry.replace_delegated_tools((tool('new'),))
        await drain(agent)
        assert [item.name for item in contexts[0].tools] == ['new']
        assert agent.local_tools == ()
    finally:
        await agent.aclose()
        await registry.aclose()
