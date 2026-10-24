"""Local tool publication is owner-local; MCP bindings use a separate registry."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest
from xdog.agent import AgentTool, ToolRepo
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
    assert isinstance(session.tool_repo, ToolRepo)
    assert tuple(map(id, session.tool_repo.tools)) == tuple(map(id, session.agent.local_tools))
    assert len({tool.name for tool in session.tool_repo.tools}) == len(session.tool_repo.tools)


def test_sdk_factory_uses_real_tools_with_workspace_and_preserves_custom_identity(tmp_path, monkeypatch):
    instances, directories = [], []
    custom = AgentTool(name='injected', parameters={'type': 'object'})

    class Repo(ToolRepo):
        def __init__(self, *tools, builtin=True, cwd=None):
            super().__init__(*tools, builtin=builtin, cwd=cwd)
            instances.append(self)
            directories.append(cwd)
            self.register(custom)

    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr('xdog.coding.core.sdk.ToolRepo', Repo)
    monkeypatch.setattr('xdog.ai.load', lambda: _Provider(Model(id='offline/test', context_window=128000)))
    session = create_agent_session(CreateSessionOptions(working_dir=tmp_path, model_name='offline/test')).session
    try:
        assert directories == [tmp_path]
        assert any(tool is custom for tool in instances[0].tools)
        assert any(tool is custom for tool in session.tool_repo.tools)
        assert_inventory(session)
        assert session.children.runtime._thread is None
    finally:
        session.dispose()


def test_default_helper_defers_default_names_and_cwd_to_sdk(tmp_path, monkeypatch):
    instances = []

    class Repo(ToolRepo):
        def __init__(self, *, cwd):
            assert cwd == tmp_path
            super().__init__(cwd=cwd)
            instances.append(self)

    monkeypatch.setattr('xdog.coding.core.tools.ToolRepo', Repo)
    tools = get_default_tools(tmp_path)
    assert tuple(map(id, tools)) == tuple(map(id, instances[0].tools))
    assert {tool.name for tool in tools} == ToolRepo(cwd=tmp_path).names()
    assert not ToolRepo(builtin=False).tools
    assert 'submit_result' not in {tool.name for tool in tools}


async def test_owners_are_isolated_and_publication_keeps_explicit_empty_selection(tmp_path):
    first, calls = create(tmp_path / 'first')
    second, _ = create(tmp_path / 'second')
    try:
        assert first.tool_repo is not second.tool_repo
        assert_inventory(first)
        first.agent.set_active_tools([], expected_revision=first.agent.tool_selection_revision)
        original = tuple(first.agent.state.tools)
        revision = first.agent.tool_selection_revision
        first.tool_repo.publish(first.agent)
        assert first.agent.tool_selection_revision == revision
        custom = AgentTool(name='injected', parameters={'type': 'object'})
        first.agent.set_tools((*first.agent.state.tools, custom))
        await first.initialize_capabilities()
        assert_inventory(first)
        assert not first.agent.active_tools and first.agent.active_tool_selection == ()
        assert all(tool.name != 'injected' for tool in second.tool_repo.tools)
        previous = first.tool_repo
        inventory = first.agent.local_tools
        selection_revision = first.agent.tool_selection_revision
        with pytest.raises(ValueError, match='[Dd]uplicate|[Aa]lready|[Cc]onflict'):
            first._publish_tools((*first.agent.local_tools, replace(original[0])))
        assert first.tool_repo is previous
        assert tuple(map(id, first.agent.local_tools)) == tuple(map(id, inventory))
        assert first.agent.tool_selection_revision == selection_revision
        assert_inventory(first)
        assert not calls
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.parametrize('selection', [None, (), ('current_time',)])
@pytest.mark.parametrize('failure', ['duplicate', 'publication', 'mcp-collision'])
async def test_failed_inventory_update_preserves_original_repo_and_authority(
        tmp_path, monkeypatch, selection, failure):
    from xdog.agent.mcp import McpRegistry

    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())
    session, calls = create(tmp_path)
    original_set_tools = session.agent.set_tools
    try:
        custom = AgentTool(name='injected', parameters={'type': 'object'})
        if failure == 'mcp-collision':
            session.mcp.close()
            session.mcp = McpRegistry.from_tools((custom,))
            session.agent.set_mcp_registry(session.mcp)
        session.agent.set_active_tools(selection, expected_revision=session.agent.tool_selection_revision)
        original_repo = session.tool_repo
        original_local = session.agent.local_tools
        original_inventory = tuple(session.agent.state.tools)
        original_selection = session.agent.active_tool_selection
        revision = session.agent.tool_selection_revision
        permissions = session.permissions
        policy = permissions.policy_revision
        replacement = (*original_local, custom)
        if failure == 'duplicate':
            replacement = (*original_local, replace(original_local[0]))
        elif failure == 'publication':
            def reject(tools):
                raise ValueError('Publication rejected')
            monkeypatch.setattr(session.agent, 'set_tools', reject)
        with pytest.raises(ValueError):
            session._publish_tools(replacement)
        assert session.tool_repo is original_repo
        assert tuple(map(id, session.agent.local_tools)) == tuple(map(id, original_local))
        assert tuple(map(id, session.agent.state.tools)) == tuple(map(id, original_inventory))
        assert session.agent.active_tool_selection == original_selection
        assert session.agent.tool_selection_revision == revision
        assert session.permissions is permissions and permissions.policy_revision == policy
        assert_inventory(session)
        assert not calls
    finally:
        monkeypatch.setattr(session.agent, 'set_tools', original_set_tools)
        await session.aclose()


@pytest.mark.parametrize('selection', [None, (), ('current_time',)])
async def test_successful_inventory_update_stages_independent_repo_without_new_selection(tmp_path, selection):
    session, calls = create(tmp_path)
    try:
        session.agent.set_active_tools(selection, expected_revision=session.agent.tool_selection_revision)
        original_repo = session.tool_repo
        original = tuple(original_repo.tools)
        custom = AgentTool(name='injected', parameters={'type': 'object'})
        session._publish_tools((*original, custom))
        assert session.tool_repo is not original_repo
        assert tuple(map(id, original_repo.tools)) == tuple(map(id, original))
        assert tuple(map(id, session.agent.local_tools)) == tuple(map(id, (*original, custom)))
        assert session.agent.active_tool_selection == selection
        assert_inventory(session)
        assert not calls
    finally:
        await session.aclose()


async def test_capabilities_children_and_session_info_use_owner_registry(tmp_path, providers, monkeypatch):
    session, calls = create(tmp_path)
    publications = []
    original_publish = ToolRepo.publish
    def publish(repo, agent):
        if agent is session.agent:
            publications.append(repo)
        original_publish(repo, agent)
    monkeypatch.setattr(ToolRepo, 'publish', publish)
    try:
        initial_registry = session.tool_repo
        initial_session_info = session._session_info_tool
        initial_children = session.children.tools
        await session.initialize_capabilities()
        assert publications and publications[-1] is session.tool_repo
        assert session.tool_repo is not initial_registry
        assert_inventory(session)
        for tool in (*session.capability_assembly.tools, *session.children.tools, session._session_info_tool):
            assert any(tool is bound for bound in session.tool_repo.tools)
        await asyncio.to_thread(session.new_session)
        assert_inventory(session)
        assert not any(tool is initial_session_info for tool in session.tool_repo.tools)
        assert not any(tool is old for tool in session.tool_repo.tools for old in initial_children)
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
    publish = Mock(wraps=session.tool_repo.publish)
    monkeypatch.setattr(session.tool_repo, 'publish', publish)
    sync = Mock(wraps=session.agent.sync_mcp_tools)
    monkeypatch.setattr(session.agent, 'sync_mcp_tools', sync)
    try:
        for action in ('connect', 'refresh', 'disconnect', 'connect'):
            await session.manage_mcp(action, 'probe')
            assert_inventory(session)
            assert all(any(tool is item for item in session.agent.state.tools) for tool in current)
            assert all(not any(tool is item for item in session.tool_repo.tools) for tool in current)
        await session.manage_mcp_auth('probe', 'forget', '', '', lambda: True)
        assert_inventory(session)
        assert all(tool.name != original.name for tool in session.tool_repo.tools)
        publish.assert_not_called()
        assert sync.call_count == 5 and not calls
    finally:
        await session.aclose()


async def test_child_runtime_narrowing_stages_new_repos_and_survives_later_publication(tmp_path, monkeypatch):
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
        registry = session.children.tool_repos[spec.id]
        assert registry is session.children.runtime.tool_repos[spec.id]
        assert any(tool is role for tool in registry.tools)  # Runtime has not applied its ceiling yet.
        registries.append(registry)
        original_publish = ToolRepo.publish
        def observed(repo, target):
            if target is agent:
                publications.append(tuple(tool.name for tool in repo.tools))
                registries.append(repo)
            original_publish(repo, target)
        monkeypatch.setattr(ToolRepo, 'publish', observed)
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
        assert any(tool is role for tool in registries[0].tools)  # Previous snapshots are not mutated.
        assert [tool.name for tool in registries[-1].tools] == ['current_time']
        assert registries[-1] is not registries[0]
        assert identifier not in session.children.runtime.tool_repos
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
