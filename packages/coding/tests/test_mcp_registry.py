"""Coding keeps transport/delegated MCP registration outside local tool inventories."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from xdog.agent import AgentTool, AgentToolResult
from xdog.agent.mcp import McpRegistry
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.test_child_agents import create
from packages.coding.tests.test_tool_repo import assert_inventory


def bind_remote(session, tools):
    session.mcp.close()
    session.mcp = McpRegistry.from_tools(tools)
    session.agent.set_mcp_registry(session.mcp)
    return session.mcp


@pytest.fixture(autouse=True)
def no_installed_capabilities(monkeypatch):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())


async def test_local_republication_never_adopts_mcp_or_reenables_explicit_selection(tmp_path):
    session, calls = create(tmp_path)
    remote = AgentTool(name='remote_without_prefix', parameters={'type': 'object'})
    mcp = bind_remote(session, (remote,))
    try:
        session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
        for change in (session._bind_session_info_tool, session._load_children):
            change()
            assert_inventory(session)
            assert all(tool is not remote for tool in session.agent.local_tools)
            assert any(tool is remote for tool in session.agent.state.tools)
            assert not session.agent.active_tools
        revision = session.agent.tool_selection_revision
        session.tool_repo.publish(session.agent)
        assert session.agent.tool_selection_revision == revision
        await session.initialize_capabilities()
        assert session.agent.mcp_registry is mcp
        assert_inventory(session)
        assert not session.agent.active_tools and not calls
    finally:
        await session.aclose()
    assert mcp._closed


async def test_navigation_rebinds_registry_and_does_not_keep_old_remote_tools(tmp_path):
    session, calls = create(tmp_path)
    remote = AgentTool(name='remote', parameters={'type': 'object'})
    old = bind_remote(session, (remote,))
    try:
        await asyncio.to_thread(session.new_session)
        assert session.agent.mcp_registry is session.mcp and session.mcp is not old
        assert old._closed and not old.tools()
        assert all(tool is not remote for tool in session.agent.state.tools)
        assert_inventory(session)
        assert not calls
    finally:
        await session.aclose()


async def test_mcp_name_collision_is_rejected_and_failed_connection_is_retired(tmp_path, monkeypatch):
    session, calls = create(tmp_path)
    remote = replace(session.agent.local_tools[0])
    mcp = session.mcp
    current = []
    async def connect(server):
        current[:] = [remote]
    async def disconnect(server):
        current.clear()
    monkeypatch.setattr(mcp, 'tools', lambda: tuple(current))
    monkeypatch.setattr(mcp, 'connect', connect)
    disconnected = Mock(wraps=disconnect)
    monkeypatch.setattr(mcp, 'disconnect', disconnected)
    try:
        original = tuple(session.agent.state.tools)
        with pytest.raises(ValueError, match='Duplicate'):
            await session.manage_mcp('connect', 'fixture')
        assert tuple(session.agent.state.tools) == original
        assert_inventory(session)
        assert not current and not calls
        assert disconnected.call_count == 1
    finally:
        await session.aclose()


@pytest.mark.parametrize('mode,readonly,executed', [('allow-all', False, True), ('deny', False, False),
                                                  ('allow-all', True, False), ('ask', False, False)])
async def test_real_mcp_tool_uses_merged_agent_inventory_and_original_permission_gates(
        tmp_path, mode, readonly, executed):
    fixture = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_server.py'
    log = tmp_path / 'mcp-requests'
    (tmp_path / 'settings.json').write_text(json.dumps({'mcp_servers': {'fixture': {
        'command': sys.executable, 'args': [str(fixture), 'modern', str(log)]}}}))
    session, _ = create(tmp_path, mode)
    contexts = []
    try:
        await session.manage_mcp('connect', 'fixture')
        remote = next(tool for tool in session.mcp.tools() if 'value' in tool.parameters.get('properties', {}))
        assert_inventory(session)
        assert all(tool is not remote for tool in session.tool_repo.tools)
        if readonly:
            session.access_control(True)
        def stream(model, context, options):
            contexts.append(context)
            content = ((ToolCall(id='remote-call', name=remote.name, arguments={'value': 'REMOTE-RESULT'}),)
                       if context.messages[-1].role == 'user' else (TextContent(text='done'),))
            return EventStream.empty(AssistantMessage(content=content))
        session.agent.set_stream_fn(stream)
        await session.send_message('use remote')
        result = next(message for message in session.messages if message.role == 'toolResult')
        assert result.is_error is not executed
        assert any(tool.name == remote.name for tool in contexts[0].tools)
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert any(frame['method'] == 'tools/call' for frame in frames) is executed
        assert_inventory(session)
    finally:
        await session.aclose()


@pytest.mark.parametrize('revoke', ['none', 'refresh', 'retire-without-sync'])
async def test_child_mcp_proxies_are_identity_split_and_parent_revocation_stays_failclosed(tmp_path, revoke):
    session, _ = create(tmp_path, 'allow-all')
    effects, contexts, children, registry_pairs = [], [], [], []
    entered, release = threading.Event(), threading.Event()
    async def execute(call_id, params, cancel=None, on_update=None, **kwargs):
        effects.append('remote')
        return AgentToolResult.from_text('REMOTE-EFFECT')
    # Actual MCP tool has no MCP prefix; an ordinary local tool deliberately does.
    remote = AgentTool(name='remote_plain', parameters={'type': 'object'}, execute=execute)
    local = AgentTool(name='mcp_looks_remote', parameters={'type': 'object'})
    session.agent.set_tools((*session.agent.local_tools, local))
    mcp = bind_remote(session, (remote,))
    def stream(model, context, options):
        contexts.append(context)
        message = (AssistantMessage(content=(ToolCall(id='remote', name=remote.name, arguments={}),))
                   if len(contexts) == 1 else AssistantMessage(content=(TextContent(text='done'),)))
        completed = asyncio.get_running_loop().create_future()
        completed.set_result(message)
        async def generate():
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.002)
            yield DoneEvent(message=message)
        return EventStream.from_async_generator(generate(), result_future=completed)
    session.agent.set_stream_fn(stream)
    original_factory = session.children.create
    async def factory(spec, publish):
        child = await original_factory(spec, publish)
        registry = session.children.tool_repos[spec.id]
        child_mcp = session.children.mcp_registries[spec.id]
        assert child_mcp is child.mcp_registry and child_mcp is not mcp
        assert child_mcp is session.children.runtime.mcp_registries[spec.id]
        assert [tool.name for tool in registry.tools] == [local.name]
        assert [tool.name for tool in child_mcp.tools()] == [remote.name]
        assert child_mcp.tools()[0] is not remote
        children.append(child)
        registry_pairs.append((registry, child_mcp))
        return child
    session.children.runtime.factory = factory
    try:
        identifier = await session.children.runtime.start('work', access='inherit',
                                                          config={'tools': [remote.name, local.name]})
        assert await asyncio.to_thread(entered.wait, 5)
        if revoke != 'none':
            mcp.replace_delegated_tools((replace(remote),) if revoke == 'refresh' else ())
            if revoke == 'refresh':
                session.agent.sync_mcp_tools()
        release.set()
        result = await session.children.runtime.control('wait', identifier)
        assert result['status'] == 'completed', result['error']
        assert effects == (['remote'] if revoke == 'none' else [])
        remote_result = next(message for message in result['messages'] if message['role'] == 'toolResult')
        assert bool(remote_result.get('is_error')) is (revoke != 'none')
        assert not any(tool.name == remote.name for tool in registry_pairs[0][0].tools)
        if revoke != 'none':
            assert all(tool.name != remote.name for tool in contexts[-1].tools)
        assert not session.children.runtime.mcp_registries
        assert mcp.tools() or revoke == 'retire-without-sync'
        assert not mcp._closed
    finally:
        release.set()
        await session.aclose()


async def test_child_runtime_mcp_ceiling_survives_factory_hook_republication(tmp_path):
    session, _ = create(tmp_path, 'allow-all')
    remote = AgentTool(name='remote', parameters={'type': 'object'})
    bind_remote(session, (remote,))
    contexts, manifests, captured_registries = [], [], []
    original_capture = session.children.capture
    async def narrower_capture(spec):
        captured = await original_capture(spec)
        # A host admission may narrow the frozen initial grant before execution.
        return replace(captured, tools=('current_time',))
    session.children.runtime.capture = narrower_capture
    original_factory = session.children.create
    async def factory(spec, publish):
        child = await original_factory(spec, publish)
        delegated = child.mcp_registry
        assert [tool.name for tool in delegated.tools()] == [remote.name]
        captured_registries.append(delegated)
        replace_tools = delegated.replace_delegated_tools
        def observed(tools):
            selected = tuple(tools)
            manifests.append(tuple(tool.name for tool in selected))
            replace_tools(selected)
        delegated.replace_delegated_tools = observed
        return child
    session.children.runtime.factory = factory
    def stream(model, context, options):
        contexts.append(context)
        content = ((ToolCall(id='clock', name='current_time', arguments={}),) if len(contexts) == 1
                   else (TextContent(text='done'),))
        return EventStream.empty(AssistantMessage(content=content))
    session.agent.set_stream_fn(stream)
    try:
        identifier = await session.children.runtime.start('time', config={'tools': ['current_time', remote.name]})
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'completed', record['error']
        assert len(contexts) == 2 and len(manifests) >= 4
        assert all(not names for names in manifests)
        assert all([tool.name for tool in context.tools] == ['current_time'] for context in contexts)
        assert captured_registries[0]._closed
        assert not session.children.runtime.mcp_registries
        assert not session.children.runtime.tool_selections
        assert session.mcp.tools() == (remote,)  # Child cleanup never closes the parent MCP owner.
    finally:
        await session.aclose()


async def test_auth_forget_persists_narrowing_and_mcp_reregistration_does_not_reenable(tmp_path, monkeypatch):
    session, calls = create(tmp_path)
    remote = AgentTool(name='remote', parameters={'type': 'object'})
    mcp = bind_remote(session, (remote,))
    async def auth(*args, **kwargs):
        mcp.replace_delegated_tools(())
        return {'state': 'forgotten'}
    monkeypatch.setattr(mcp, 'manage_auth', auth)
    try:
        session.select_active_tools(['filesystem', remote.name],
                                    expected_revision=session.agent.tool_selection_revision)
        await session.manage_mcp_auth('fixture', 'forget', '', '', lambda: True)
        assert session.agent.active_tool_selection == ('filesystem',)
        assert session.session_data.active_tools == ('filesystem',)
        assert session.session_manager.load_session(session.session_id).active_tools == ('filesystem',)
        mcp.replace_delegated_tools((remote,))
        session.agent.sync_mcp_tools()
        session.tool_repo.publish(session.agent)
        assert session.agent.active_tool_selection == ('filesystem',)
        assert any(tool is remote for tool in session.agent.state.tools)
        assert all(tool is not remote for tool in session.agent.local_tools)
        assert_inventory(session)
        assert not calls
    finally:
        await session.aclose()
