"""Claw keeps actual MCP ownership separate from ordinary host tool sources."""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from xdog.agent import AgentContext, AgentTool, AgentToolResult, BeforeToolCallContext
from xdog.agent.mcp import McpRegistry
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group

FIXTURE = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_server.py'


@pytest.fixture
async def host(tmp_path, monkeypatch):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    state = SimpleNamespace(calls=[], emit=(), log=tmp_path / 'requests')
    def stream(model, context, options):
        state.calls.append(context)
        content = state.emit if context.messages[-1].role == 'user' and state.emit else (TextContent(text='done'),)
        return EventStream.empty(AssistantMessage(content=content))
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=('current_time',)),
        data_dir=tmp_path / 'data', workspace_dir=workspace, model='offline/model', stream_fn=stream,
        mcp_servers={'fixture': {'command': sys.executable, 'args': [str(FIXTURE), 'modern', str(state.log)],
                                 'allow_tools': True}})
    state.runtime = runtime
    try:
        yield state
    finally:
        await runtime.aclose()


def remote_effects(host):
    return [row for row in map(json.loads, host.log.read_text().splitlines()) if row['method'] == 'tools/call']


async def connect(host):
    runtime = host.runtime
    session = await runtime.prepare_session(allow_reset=False)
    await runtime.manage_mcp('connect', 'fixture')
    return session, runtime.mcp.tools()[0]


async def test_preconnected_mcp_binds_separately_and_management_never_publishes_local_tools(host, monkeypatch):
    runtime = host.runtime
    await runtime.manage_mcp('connect', 'fixture')  # No session is needed to own the connection.
    remote = runtime.mcp.tools()
    session = await runtime.prepare_session(allow_reset=False)
    agent = session.agent
    assert isinstance(runtime.mcp, McpRegistry) and agent.mcp_registry is runtime.mcp
    assert tuple(runtime.tool_repo.tools) == agent.local_tools
    assert tuple(agent.state.tools) == (*agent.local_tools, *remote)
    assert not {tool.name for tool in remote} & {tool.name for tool in runtime.tools}
    assert not hasattr(runtime, '_mcp_tools')
    publish = Mock(wraps=agent.set_tools)
    register = Mock(wraps=runtime.publish_tools)
    monkeypatch.setattr(agent, 'set_tools', publish)
    monkeypatch.setattr(runtime, 'publish_tools', register)
    agent.set_active_tools(['current_time', remote[0].name], expected_revision=agent.tool_selection_revision)
    await runtime.manage_mcp('refresh', 'fixture')
    assert agent.active_tool_selection == ('current_time', remote[0].name)
    await runtime.manage_mcp('disconnect', 'fixture')
    assert agent.active_tool_selection == ('current_time',)
    assert tuple(agent.state.tools) == agent.local_tools
    await runtime.manage_mcp('connect', 'fixture')
    assert agent.active_tool_selection == ('current_time',)  # Reconnection cannot restore a retired selection.
    assert tuple(runtime.tool_repo.tools) == agent.local_tools
    publish.assert_not_called()
    register.assert_not_called()
    assert not host.calls and not remote_effects(host)


@pytest.mark.parametrize('allowed,access,parent_readonly', [
    (True, 'inherit', False), (False, 'inherit', False),
    (True, 'read-only', False), (True, 'inherit', True),
])
async def test_child_mcp_manifest_preserves_host_permissions_and_never_closes_parent(
    host, allowed, access, parent_readonly,
):
    runtime = host.runtime
    session, remote = await connect(host)
    runtime.mcp.configs['fixture'] = replace(runtime.mcp.configs['fixture'], allow_tools=allowed)
    session.access.set_read_only(parent_readonly)
    host.emit = (ToolCall(id='remote', name=remote.name, arguments={'value': 'child effect'}),)
    children = []
    factory = session.children.runtime.factory
    async def capture(spec, publish):
        child = await factory(spec, publish)
        children.append(child)
        assert child.mcp_registry is not runtime.mcp
        assert not child.local_tools
        assert len(child.mcp_registry.tools()) == 1
        return child
    session.children.runtime.factory = capture
    identifier = await session.children.runtime.start('use delegated MCP', access=access,
        config={'tools': [remote.name]})
    record = await session.children.runtime.control('wait', identifier)
    results = [row for row in record['messages'] if row['role'] == 'toolResult']
    permitted = allowed and access == 'inherit' and not parent_readonly
    assert len(results) == 1 and results[0]['is_error'] is (not permitted)
    assert len(remote_effects(host)) == int(permitted)
    child = children[0]
    await child.aclose()
    await child.mcp_registry.aclose()  # Manifest close owns no parent transport/credentials.
    assert runtime.mcp.is_current_tool(remote)
    assert runtime.mcp.status()[0]['state'] == 'connected'
    assert session.agent.mcp_registry is runtime.mcp
    assert remote.name not in {tool.name for tool in runtime.tool_repo.tools}


@pytest.mark.parametrize('revocation', ['allow-tools', 'refresh', 'selection'])
async def test_child_pending_mcp_effect_rechecks_original_owner_and_grant(host, revocation):
    runtime = host.runtime
    session, remote = await connect(host)
    revoked = asyncio.Event()
    original_validation = remote.validate_arguments
    validations = 0
    async def validate(params):
        nonlocal validations
        validations += 1
        if validations > 1:
            # Hold the actual executor validator, not the transport or tool
            # implementation. The first prepared sibling owns an awaited real
            # refresh; its completed revocation must precede the second effect.
            await revoked.wait()
        await original_validation(params)
    remote.validate_arguments = validate
    async def revoke(*args, **kwargs):
        if revocation == 'allow-tools':
            runtime.mcp.configs['fixture'] = replace(runtime.mcp.configs['fixture'], allow_tools=False)
        elif revocation == 'refresh':
            # Actual external inventory replacement without republishing parent
            # state: a stale same-name snapshot is not execution authority.
            await runtime.mcp.refresh('fixture')
        else:
            session.agent.set_active_tools(['revoke'], expected_revision=session.agent.tool_selection_revision)
        revoked.set()
        return AgentToolResult.from_text('original permission revoked')
    first = AgentTool('revoke', parameters={'type': 'object'}, execute=revoke)
    session.agent.set_tools((*session.agent.local_tools, first))
    host.emit = (ToolCall(id='first', name=first.name, arguments={}),
                 ToolCall(id='second', name=remote.name, arguments={'value': 'must not execute'}))
    identifier = await session.children.runtime.start('parallel delegated effects', access='inherit',
        config={'tools': [first.name, remote.name]})
    record = await session.children.runtime.control('wait', identifier)
    assert not remote_effects(host)
    assert 'original permission revoked' in str(record['messages'])
    assert any(row.get('is_error') for row in record['messages'] if row['role'] == 'toolResult')
    assert remote not in session.agent.local_tools


async def test_local_name_collision_is_rejected_and_old_mcp_alias_is_not_an_identity(host):
    runtime = host.runtime
    session, remote = await connect(host)
    agent = session.agent
    local, combined = agent.local_tools, agent.state.tools
    effects = []
    async def execute(*args, **kwargs):
        effects.append('ordinary host effect')
        return AgentToolResult.from_text('ordinary host effect')
    ordinary = AgentTool(remote.name, parameters=remote.parameters, execute=execute)
    with pytest.raises(ValueError, match='Duplicate'):
        agent.set_tools((*local, ordinary))
    assert agent.local_tools is local and agent.state.tools is combined
    agent.set_mcp_registry(None)
    agent.set_tools((*local, ordinary))
    ctx = BeforeToolCallContext(assistant_message=AssistantMessage(),
        tool_call=ToolCall(id='local', name=ordinary.name, arguments={'value': 'local'}),
        args={'value': 'local'}, context=AgentContext(tools=list(agent.state.tools)))
    assert await session._before_tool_call(ctx) is None
    host.emit = (ctx.tool_call,)
    identifier = await session.children.runtime.start('delegate an ordinary tool named like MCP', access='inherit',
        config={'tools': [ordinary.name]})
    record = await session.children.runtime.control('wait', identifier)
    assert record['status'] == 'completed' and effects == ['ordinary host effect']
    assert not remote_effects(host) and runtime.mcp.is_current_tool(remote)


@pytest.mark.parametrize('native', [False, True])
async def test_full_mcp_prompt_and_ui_inventory_survive_disabled_native_tools(host, native):
    from xdog.ai.types import system_prompt_text
    from xdog.claw.core.types import UserInput

    runtime = host.runtime
    session, remote = await connect(host)
    agent = session.agent
    agent.set_model_capabilities(context_window=agent.context_window, max_prompt_tokens=agent.max_prompt_tokens,
                                 supports_tool_calls=native)
    host.emit = (ToolCall(id='model-remote', name=remote.name, arguments={'value': 'native effect'}),)
    inventory = session._extension_tools()
    assert inventory['api_enabled'] is native
    assert bool(session._compaction_tools()) is native
    advertised = next(tool for tool in inventory['tools'] if tool['name'] == remote.name)
    assert advertised['description'] == remote.description and advertised['parameters'] == remote.parameters
    assert remote.name in system_prompt_text(runtime.build_system_prompt())
    result = await session.run_turn(UserInput(group_id='main', content='Use the available tools'))
    assert result.error is None
    for context in host.calls:
        text = system_prompt_text(context.system_prompt)
        assert remote.name in text and remote.description in text
        assert bool(context.tools) is native
    assert len(remote_effects(host)) == int(native)
    assert any(tool is remote for tool in agent.state.tools)
    assert not any(tool is remote for tool in agent.local_tools)
    assert tuple(runtime.tool_repo.tools) == agent.local_tools
