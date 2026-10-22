"""Local tool publication is owner-local; MCP bindings use a separate registry."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest
from xdog.agent import AgentTool
from xdog.agent.tools import ToolContext, ToolRegistry
from xdog.ai.types import Model
from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session
from xdog.coding.core.tools import get_default_tools

from packages.coding.tests.test_capability_assembly import Provider
from packages.coding.tests.test_child_agents import create
from packages.coding.tests.test_sdk_session_state import _Provider


@pytest.fixture
def providers(monkeypatch):
    values = Provider('session'), Provider('application')
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: tuple(value for value in values if scope in value.scopes))
    return values


def assert_inventory(session):
    assert isinstance(session.tool_registry, ToolRegistry)
    assert tuple(map(id, session.tool_registry.tools)) == tuple(map(id, session.agent.local_tools))
    assert len({tool.name for tool in session.tool_registry.tools}) == len(session.tool_registry.tools)


def test_sdk_factory_uses_one_owned_registry_including_custom_defaults(tmp_path, monkeypatch):
    instances, contexts = [], []
    custom = AgentTool(name='injected', parameters={'type': 'object'})

    class Registry(ToolRegistry):
        def __init__(self):
            super().__init__()
            instances.append(self)
            self.register_factory('injected', lambda ctx: contexts.append(ctx) or custom, default=True)

    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr('xdog.coding.core.sdk.ToolRegistry', Registry)
    monkeypatch.setattr('xdog.ai.load', lambda: _Provider(Model(id='offline/test', context_window=128000)))
    session = create_agent_session(CreateSessionOptions(working_dir=tmp_path, model_name='offline/test')).session
    try:
        assert instances == [session.tool_registry]
        assert contexts == [ToolContext(cwd=tmp_path)]
        assert any(tool is custom for tool in session.tool_registry.tools)
        assert_inventory(session)
        assert session.children.runtime._thread is None
    finally:
        session.dispose()


def test_default_helper_defers_default_names_and_cwd_to_sdk(tmp_path):
    registry = ToolRegistry()
    tools = get_default_tools(tmp_path)
    assert [tool.name for tool in tools] == list(registry.default_names())
    assert not registry.create_tools([])
    assert 'submit_result' not in {tool.name for tool in tools}


async def test_owners_are_isolated_and_publication_keeps_explicit_empty_selection(tmp_path):
    first, calls = create(tmp_path / 'first')
    second, _ = create(tmp_path / 'second')
    try:
        assert first.tool_registry is not second.tool_registry
        assert_inventory(first)
        first.agent.set_active_tools([], expected_revision=first.agent.tool_selection_revision)
        original = tuple(first.agent.state.tools)
        revision = first.agent.tool_selection_revision
        first.tool_registry.publish(first.agent)
        assert first.agent.tool_selection_revision == revision
        custom = AgentTool(name='injected', parameters={'type': 'object'})
        first.agent.set_tools((*first.agent.state.tools, custom))
        await first.initialize_capabilities()
        assert_inventory(first)
        assert not first.agent.active_tools and first.agent.active_tool_selection == ()
        assert all(tool.name != 'injected' for tool in second.tool_registry.tools)
        with pytest.raises(ValueError, match='Duplicate'):
            first._publish_tools((*first.agent.local_tools, replace(original[0])))
        assert_inventory(first)
        assert not calls
    finally:
        await first.aclose()
        await second.aclose()


async def test_capabilities_children_and_session_info_use_owner_registry(tmp_path, providers, monkeypatch):
    session, calls = create(tmp_path)
    publish = Mock(wraps=session.tool_registry.publish)
    monkeypatch.setattr(session.tool_registry, 'publish', publish)
    try:
        initial_session_info = session._session_info_tool
        initial_children = session.children.tools
        await session.initialize_capabilities()
        assert publish.called
        assert_inventory(session)
        for tool in (*session.capability_assembly.tools, *session.children.tools, session._session_info_tool):
            assert any(tool is bound for bound in session.tool_registry.tools)
        await asyncio.to_thread(session.new_session)
        assert_inventory(session)
        assert not any(tool is initial_session_info for tool in session.tool_registry.tools)
        assert not any(tool is old for tool in session.tool_registry.tools for old in initial_children)
        await session.initialize_capabilities()
        assert_inventory(session)
        assert not calls
    finally:
        await session.aclose()


async def test_mcp_refresh_disconnect_and_auth_retirement_stay_outside_local_registry(tmp_path, monkeypatch):
    session, calls = create(tmp_path)
    mcp = session.mcp
    current = []
    original = AgentTool(name='mcp_probe', parameters={'type': 'object'})
    replacement = replace(original, description='refreshed')
    async def connect(server):
        current[:] = [original]
    async def refresh(server):
        current[:] = [replacement]
    async def disconnect(server):
        current.clear()
    async def auth(*args, **kwargs):
        current.clear()
        return {'status': 'forgotten'}
    monkeypatch.setattr(mcp, 'tools', lambda: list(current))
    monkeypatch.setattr(mcp, 'connect', connect)
    monkeypatch.setattr(mcp, 'refresh', refresh)
    monkeypatch.setattr(mcp, 'disconnect', disconnect)
    monkeypatch.setattr(mcp, 'manage_auth', auth)
    publish = Mock(wraps=session.tool_registry.publish)
    monkeypatch.setattr(session.tool_registry, 'publish', publish)
    sync = Mock(wraps=session.agent.sync_mcp_tools)
    monkeypatch.setattr(session.agent, 'sync_mcp_tools', sync)
    try:
        for action in ('connect', 'refresh', 'disconnect', 'connect'):
            await session.manage_mcp(action, 'probe')
            assert_inventory(session)
            assert all(any(tool is item for item in session.agent.state.tools) for tool in current)
            assert all(not any(tool is item for item in session.tool_registry.tools) for tool in current)
        await session.manage_mcp_auth('probe', 'forget', '', '', lambda: True)
        assert_inventory(session)
        assert all(tool.name != original.name for tool in session.tool_registry.tools)
        publish.assert_not_called()
        assert sync.call_count == 5 and not calls
    finally:
        await session.aclose()


async def test_child_runtime_narrowing_uses_same_registry_and_survives_later_publication(tmp_path, monkeypatch):
    from xdog.agent import AgentToolResult
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream

    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())
    session, _ = create(tmp_path, 'allow-all')
    contexts, registries, publications = [], [], []
    role = AgentTool(name='role_only', parameters={'type': 'object'})
    async def role_tools(spec):
        return (role,)
    monkeypatch.setattr(session.children.capabilities, 'tools', role_tools)
    monkeypatch.setattr(session.children.capabilities, 'permits',
                        lambda identifier, name, tools: name == role.name and any(tool is role for tool in tools))
    def stream(model, context, options):
        contexts.append(context)
        message = (AssistantMessage(content=(ToolCall(id='clock', name='current_time', arguments={}),))
                   if len(contexts) == 1 else AssistantMessage(content=(TextContent(text='done'),)))
        return EventStream.empty(message)
    session.agent.set_stream_fn(stream)
    original_factory = session.children.create
    async def factory(spec, publish):
        agent = await original_factory(spec, publish)
        registry = session.children.tool_registries[spec.id]
        assert registry is session.children.runtime.tool_registries[spec.id]
        assert any(tool is role for tool in registry.tools)  # Runtime has not applied its ceiling yet.
        registries.append(registry)
        original_publish = registry.publish
        def observed(target):
            assert target is agent
            publications.append(tuple(tool.name for tool in registry.tools))
            original_publish(target)
        monkeypatch.setattr(registry, 'publish', observed)
        return agent
    session.children.runtime.factory = factory
    # Keep a real local executor so pre/post and model preparation republish after narrowing.
    current = next(tool for tool in session.agent.state.tools if tool.name == 'current_time')
    async def clock(*args, **kwargs):
        return AgentToolResult.from_text('clock result')
    session.agent.set_tools(tuple(replace(tool, execute=clock) if tool is current else tool
                                  for tool in session.agent.state.tools))
    try:
        identifier = await session.children.runtime.start('time', config={'tools': ['current_time']})
        result = await session.children.runtime.control('wait', identifier)
        assert result['status'] == 'completed', result['error']
        assert len(contexts) == 2 and len(publications) >= 4
        assert all(names == ('current_time',) for names in publications)
        assert all([tool.name for tool in context.tools] == ['current_time'] for context in contexts)
        assert [tool.name for tool in registries[0].tools] == ['current_time']
        assert identifier not in session.children.runtime.tool_registries
        assert identifier not in session.children.runtime.tool_selections
    finally:
        await session.aclose()


async def test_snapshot_and_session_navigation_preserve_historical_tool_provenance_as_data(tmp_path):
    from xdog.agent.core import CustomAgentMessage
    from xdog.agent.extensions.context_message import TYPE
    from xdog.agent.helpers import stream_fn_from_provider
    from xdog.agent.messages import messages_to_dicts
    from xdog.ai.types import AssistantMessage, UserMessage

    from packages.agent.tests.test_context_messages import row

    session, calls = create(tmp_path)
    session.agent.set_stream_fn(stream_fn_from_provider(_Provider(Model(id='test/model', context_window=128000))))
    legacy = CustomAgentMessage(TYPE, row(session_id=session.session_id, method='tools/call'))
    messages = [legacy, UserMessage(content='historical user'), AssistantMessage()]
    session.agent.replace_messages(messages)
    original_id = session.session_id
    expected = messages_to_dicts(messages)
    try:
        session._persist()
        assert messages_to_dicts(session.session_manager.load_session(original_id).messages) == expected
        snapshot = session.agent.dump()
        session.agent.restore(snapshot)
        assert messages_to_dicts(session.messages) == expected
        await asyncio.to_thread(session.new_session)
        assert await asyncio.to_thread(session.switch_session, original_id)
        assert messages_to_dicts(session.messages) == expected
        assert session.messages[0].data['method'] == 'tools/call'
        assert_inventory(session)
        assert all(not tool.name.startswith('ext_fixture_') for tool in session.agent.state.tools)
        assert not calls  # Neither the historical label nor restoration starts tools/models.
    finally:
        await session.aclose()
