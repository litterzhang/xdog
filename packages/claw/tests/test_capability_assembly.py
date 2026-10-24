"""Claw composes SDK inventories without transferring lifecycle or child authority."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from xdog.agent import AgentTool, AgentToolResult
from xdog.agent.capabilities import ApplicationPort, CapabilitySet
from xdog.agent.tool_effects import ToolEffect
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


@dataclass
class Binding:
    tools: tuple[AgentTool, ...]
    calls: list[dict] = field(default_factory=list)
    closes: int = 0

    def tool_effect(self, tool, args):
        assert any(tool is actual for actual in self.tools)
        return ToolEffect('read' if args.get('action') == 'inspect' else 'write', 'tool_data')

    async def aclose(self):
        self.closes += 1


class Provider:
    requirements = ()

    def __init__(self, scope):
        self.name = scope + '_feature'
        self.tool_names = (self.name,)
        self.default_skills = (scope + '-skill',)
        self.bindings = []

    async def open(self, host):
        binding = Binding(())
        async def execute(_id, args, *_args, **_kwargs):
            binding.calls.append(dict(args))
            return AgentToolResult.from_text('done')
        binding.tools = (AgentTool(self.name, parameters={'type': 'object', 'properties': {
            'action': {'type': 'string'}}}, execute=execute),)
        self.bindings.append(binding)
        return binding


@pytest.fixture
async def host(tmp_path, monkeypatch):
    application, group = Provider('application'), Provider('group')
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: {'application': (application,), 'group': (group,)}.get(scope, ()))
    responses = []
    def stream(*_):
        return EventStream.empty(responses.pop(0) if responses else AssistantMessage(content=(TextContent('done'),)))
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=stream)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'work')))
    # Declared defaults must have real loadable instruction bodies.
    for slug in ('application-skill', 'group-skill'):
        app._runtimes['main'].skill_repo.save_skill(slug, f'Instructions for {slug}.')
    try:
        await app.start()
        runtime = app._runtimes['main']
        session = await runtime.prepare_session(allow_reset=False)
        await app.application_capabilities.start(ApplicationPort())
        await app.refresh_application_capabilities()
        yield SimpleNamespace(app=app, runtime=runtime, session=session, responses=responses,
                              application=application.bindings[0], group=group.bindings[0])
    finally:
        await app.stop()


async def test_shared_set_and_identity_only_publication_preserve_selection(host, monkeypatch):
    app, runtime, agent = host.app, host.runtime, host.session.agent
    assert runtime.application_capabilities is app.application_capabilities
    agent.set_active_tools(['filesystem', 'application_feature'], expected_revision=agent.tool_selection_revision)
    publish = Mock(wraps=agent.set_tools)
    monkeypatch.setattr(agent, 'set_tools', publish)
    revision = agent.tool_selection_revision
    assert tuple(runtime.tool_repo.tools) == tuple(agent.state.tools)
    await app.refresh_application_capabilities()
    runtime._attach_capabilities(host.session)
    publish.assert_not_called()
    assert tuple(runtime.tool_repo.tools) == tuple(agent.state.tools)
    assert agent.tool_selection_revision == revision
    assert set(agent.active_tool_selection) == {'filesystem', 'application_feature'}

    old, = host.application.tools
    replacement = AgentTool(old.name, execute=old.execute)
    host.application.tools = (replacement,)
    await app.refresh_application_capabilities()
    publish.assert_called_once()
    assert tuple(runtime.tool_repo.tools) == tuple(agent.state.tools)
    assert any(tool is replacement for tool in agent.state.tools)
    assert not any(tool is old for tool in agent.state.tools)
    assert set(agent.active_tool_selection) == {'filesystem', 'application_feature'}

    host.application.tools = ()
    await app.refresh_application_capabilities()
    assert agent.active_tool_selection == ('filesystem',)
    host.application.tools = (replacement,)
    await app.refresh_application_capabilities()
    assert agent.active_tool_selection == ('filesystem',)  # Retirement cannot restore permission.


@pytest.mark.parametrize('collision', ['filesystem', 'group_feature'])
async def test_collisions_fail_closed_without_overwriting_base_or_other_capability(host, collision):
    before = host.session.agent.state.tools
    registered = host.runtime.tool_repo.tools
    host.application.tools = (AgentTool(collision),)
    with pytest.raises(ValueError):
        await host.app.refresh_application_capabilities()
    assert host.session.agent.state.tools is before
    assert tuple(map(id, host.runtime.tool_repo.tools)) == tuple(map(id, registered))
    with pytest.raises(ValueError):
        _ = host.runtime.tools


async def test_enabled_filter_applies_to_both_capabilities_not_existing_base_tools(host):
    host.runtime._enabled_tools = ('group_feature',)
    await host.app.refresh_application_capabilities()
    names = {tool.name for tool in host.session.agent.state.tools}
    assert 'group_feature' in names and 'application_feature' not in names
    assert {'filesystem', 'bash'} <= names
    assert host.runtime.default_skills == {'group-skill', 'application-skill'}


async def test_group_close_does_not_close_shared_application_owner(host):
    await host.runtime.aclose()
    assert host.group.closes == 1 and host.application.closes == 0
    assert host.app.application_capabilities.tools == host.application.tools
    await host.app.stop()
    assert host.group.closes == host.application.closes == 1


async def test_retired_application_identities_remain_parent_only(host):
    old, = host.application.tools
    assert not host.session.children.capabilities.inherited(old)
    host.application.tools = (AgentTool(old.name),)
    await host.app.refresh_application_capabilities()
    assert not host.session.children.capabilities.inherited(old)
    assert not host.session.children.capabilities.inherited(host.application.tools[0])
    with pytest.raises(ValueError, match='delegable'):
        await host.session.children.runtime.start('cannot inherit capability', config={'tools': [old.name]})
    assert not host.session.children.runtime.snapshot()['children']


@pytest.mark.parametrize('scope', ['application', 'group'])
async def test_main_readonly_gate_uses_actual_capability_effect_and_blocks_mutation(host, scope):
    binding = getattr(host, scope)
    tool, = binding.tools
    host.session.access.set_read_only(True)
    host.responses.extend([
        AssistantMessage(content=(ToolCall(id='read', name=tool.name, arguments={'action': 'inspect'}),
                                  ToolCall(id='write', name=tool.name, arguments={'action': 'change'}))),
        AssistantMessage(content=(TextContent('finished'),)),
    ])
    result = await host.app.route_message(UserInput(group_id='main', content='read and write', sender='test'))
    assert result.error is None
    assert binding.calls == [{'action': 'inspect'}]
    results = {message.tool_call_id: message for message in host.session.agent.messages
               if isinstance(message, ToolResultMessage)}
    assert not results['read'].is_error and results['write'].is_error
    assert host.session.access.state.read_only


async def test_readonly_gate_does_not_trust_a_name_matched_or_retired_snapshot(host):
    tool, = host.application.tools
    def context(actual):
        return SimpleNamespace(tool_call=ToolCall(id='call', name=tool.name, arguments={}),
                               args={'action': 'inspect'}, context=SimpleNamespace(tools=(actual,)))
    assert host.session._read_only_tool_call(context(tool))
    assert not host.session._read_only_tool_call(context(AgentTool(tool.name)))
    host.application.tools = ()
    assert not host.session._read_only_tool_call(context(tool))


async def test_readonly_gate_rechecks_capability_effect_after_extension_argument_rewrite(host, monkeypatch):
    session = host.session
    tool, = host.application.tools
    session.access.set_read_only(True)
    monkeypatch.setattr(session.extensions, 'enabled', ('rewrite',))
    monkeypatch.setattr(session.extensions, 'event', AsyncMock())
    monkeypatch.setattr(session.extensions, 'pre', AsyncMock(return_value={'action': 'change'}))
    context = SimpleNamespace(tool_call=ToolCall(id='rewrite', name=tool.name, arguments={}),
                              args={'action': 'inspect'}, context=SimpleNamespace(tools=(tool,)))
    result = await session._before_tool_call(context)
    assert result.block and 'read-only' in result.reason
    assert context.args == {'action': 'change'}
    assert not host.application.calls


async def test_replaced_group_owner_projects_actual_parent_effect_not_a_cached_set(host, monkeypatch):
    previous = host.runtime.capabilities
    provider = Provider('replacement')
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: (provider,))
    replacement = CapabilitySet('group')
    try:
        await replacement.start(host.runtime._capability_host)
        host.runtime.capabilities = replacement
        tool, = replacement.tools
        # Explicit owner replacement must retire its own former handles; the
        # projection must not borrow the previous owner's tools/effect policy.
        base = tuple(tool for tool in host.session.agent.local_tools if not previous.owns_tool(tool))
        host.session.agent.set_tools((*base, tool))
        context = SimpleNamespace(tool_call=ToolCall(id='actual', name=tool.name, arguments={}),
                                  args={'action': 'inspect'}, context=SimpleNamespace(tools=(tool,)))
        assert host.session._read_only_tool_call(context)
        assert host.runtime.capability_assembly.default_skills == {'replacement-skill', 'application-skill'}
        assert host.runtime.capability_assembly.tool_effect(tool, {'action': 'inspect'}) == ToolEffect(
            'read', 'tool_data')
    finally:
        await previous.aclose()
        await replacement.aclose()


async def test_explicit_builtin_selection_cannot_be_shadowed_by_capability_metadata(host):
    provider, = host.app.application_capabilities.providers
    provider.tool_names = ('filesystem',)
    host.application.tools = (AgentTool('filesystem'),)
    host.runtime._enabled_tools = ('filesystem',)
    host.runtime._tools = None
    with pytest.raises(ValueError):
        _ = host.runtime.tools
    assert any(tool.name == 'filesystem' for tool in host.runtime._tools)
