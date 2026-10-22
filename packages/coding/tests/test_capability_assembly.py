"""Coding uses SDK inventory composition while retaining its original owner lifetimes."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import Mock

import pytest
from xdog.agent import AgentContext, AgentTool, AgentToolResult, BeforeToolCallContext
from xdog.agent.capabilities import ApplicationPort, CapabilityAssembly
from xdog.agent.tool_effects import ToolEffect
from xdog.ai.types import AssistantMessage, ToolCall

from packages.coding.tests.test_child_agents import create


class Binding:
    def __init__(self, host, name):
        self.host = host
        self.tools = (AgentTool(name=name, description=name, parameters={'type': 'object'}, execute=self.execute),)
        self.closed = 0
        self.effect_calls = []

    async def execute(self, *_args, **_kwargs):
        return AgentToolResult.from_text('done')

    def tool_effect(self, actual, args):
        assert any(actual is tool for tool in self.tools)
        self.effect_calls.append((actual, args))
        return ToolEffect('read', 'tool_data')

    async def aclose(self):
        self.closed += 1


class Provider:
    requirements = ()
    default_skills = ()

    def __init__(self, scope):
        self.name = f'{scope}_probe'
        self.scopes = (scope,)
        self.tool_names = (self.name,)
        self.bindings = []

    async def open(self, host):
        binding = Binding(host, self.name)
        self.bindings.append(binding)
        return binding


@pytest.fixture
def providers(monkeypatch):
    scoped, application = Provider('session'), Provider('application')
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: tuple(p for p in (scoped, application) if scope in p.scopes))
    return scoped, application


async def test_navigation_recomposes_current_session_set_but_keeps_application_binding(tmp_path, providers):
    scoped, application = providers
    session, contexts = create(tmp_path, 'allow-all')
    try:
        projection = session.capability_assembly
        assert isinstance(projection, CapabilityAssembly)
        assert projection.sets == (session.capabilities, session.application_capabilities)
        assert projection.declared_tools == {'session_probe', 'application_probe'}
        assert not projection.tools and not scoped.bindings and not application.bindings
        await session.send_message('first')
        first_id, first_set = session.session_id, session.capabilities
        first_binding, app_binding = scoped.bindings[0], application.bindings[0]
        assert isinstance(app_binding.host, ApplicationPort)
        original_app = app_binding.tools[0]
        for navigate in (session.new_session, lambda: session.switch_session(first_id)):
            previous = scoped.bindings[-1]
            await asyncio.to_thread(navigate)
            await session.send_message('next')
            assert session.capabilities is not first_set
            assert scoped.bindings[-1] is not previous and previous.closed == 1
            assert session.application_capabilities.get(application.name) is app_binding
            assert len(application.bindings) == 1 and app_binding.closed == 0
            current = session.capability_assembly
            assert current.sets == (session.capabilities, session.application_capabilities)
            assert current.sets[0] is not projection.sets[0]
            assert current.sets[1] is projection.sets[1]
            live = current.tools
            assert any(tool is scoped.bindings[-1].tools[0] for tool in live)
            assert any(tool is original_app for tool in live)
            assert not any(tool is previous.tools[0] for tool in session.agent.state.tools)
        assert first_binding.closed == 1
        assert len(contexts) == 3  # Projection/navigation did not launch another model.
    finally:
        await session.aclose()
    assert len(scoped.bindings) == 3 and all(binding.closed == 1 for binding in scoped.bindings)
    assert app_binding.closed == 1


@pytest.mark.parametrize('scope', ['session', 'application'])
@pytest.mark.parametrize('explicit', [False, True])
async def test_host_republication_replaces_only_owned_identities_and_preserves_selection(
    tmp_path, monkeypatch, providers, scope, explicit,
):
    session, contexts = create(tmp_path)
    try:
        await session.initialize_capabilities()
        owner = session.capabilities if scope == 'session' else session.application_capabilities
        binding = owner.get(f'{scope}_probe')
        original = binding.tools[0]
        base = tuple(tool for tool in session.agent.state.tools
                     if not any(tool is cap for cap in session.capability_assembly.tools))
        if explicit:
            session.agent.set_active_tools(['filesystem', original.name],
                                           expected_revision=session.agent.tool_selection_revision)
        revision = session.agent.tool_selection_revision
        publish = Mock(wraps=session.agent.set_tools)
        monkeypatch.setattr(session.agent, 'set_tools', publish)
        await session.initialize_capabilities()
        publish.assert_not_called()
        assert session.agent.tool_selection_revision == revision
        replacement = replace(original, description='Updated live definition')
        binding.tools = (replacement,)
        await session.initialize_capabilities()
        assert publish.call_count == 1
        assert any(tool is replacement for tool in session.agent.state.tools)
        assert not any(tool is original for tool in session.agent.state.tools)
        assert all(any(tool is configured for configured in session.agent.state.tools) for tool in base)
        if explicit:
            assert set(session.agent.active_tool_selection) == {'filesystem', original.name}
        else:
            assert session.agent.active_tool_selection is None
        await session.initialize_capabilities()
        assert publish.call_count == 1
        binding.tools = ()
        await session.initialize_capabilities()
        binding.tools = (replacement,)
        await session.initialize_capabilities()
        assert publish.call_count == 3
        if explicit:
            assert session.agent.active_tool_selection == ('filesystem',)  # Removal permanently narrowed selection.
        else:
            assert any(tool is replacement for tool in session.agent.active_tools)
        assert not contexts
    finally:
        await session.aclose()


@pytest.mark.parametrize('collision', ['capability', 'host'])
async def test_host_publication_fails_closed_on_name_collision(tmp_path, providers, collision):
    session, _ = create(tmp_path)
    try:
        await session.initialize_capabilities()
        binding = providers[1].bindings[0]
        name = 'session_probe' if collision == 'capability' else 'filesystem'
        binding.tools = (replace(binding.tools[0], name=name),)
        original = session.agent.state.tools
        revision = session.agent.tool_selection_revision
        with pytest.raises(ValueError):
            await session.initialize_capabilities()
        assert session.agent.state.tools is original
        assert session.agent.tool_selection_revision == revision
    finally:
        await session.aclose()


def effect_context(tool):
    args = {'action': 'inspect'}
    return BeforeToolCallContext(assistant_message=AssistantMessage(),
        tool_call=ToolCall(id='probe', name=tool.name, arguments=args), args=args,
        context=AgentContext(tools=(tool,)))


@pytest.mark.parametrize('scope', ['session', 'application'])
async def test_effect_uses_live_actual_identity_in_both_inventories(tmp_path, providers, scope):
    session, _ = create(tmp_path)
    try:
        await session.initialize_capabilities()
        owner = session.capabilities if scope == 'session' else session.application_capabilities
        binding = owner.get(f'{scope}_probe')
        original = binding.tools[0]
        assert await session._tool_effect(effect_context(original)) == ToolEffect('read', 'tool_data')
        assert await session._tool_effect(effect_context(replace(original))) == ToolEffect('execute', 'unknown')
        binding.tools = (replace(original, description='New identity'),)
        assert await session._tool_effect(effect_context(original)) == ToolEffect('execute', 'unknown')
        await session.initialize_capabilities()
        assert await session._tool_effect(effect_context(binding.tools[0])) == ToolEffect('read', 'tool_data')
        session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
        assert await session._tool_effect(effect_context(binding.tools[0])) == ToolEffect('execute', 'unknown')
        assert len(binding.effect_calls) == 2
    finally:
        await session.aclose()


async def test_replaced_application_identity_remains_parent_only_before_any_child_capture(tmp_path, providers):
    session, contexts = create(tmp_path)
    try:
        await session.initialize_capabilities()
        binding = providers[1].bindings[0]
        original, = binding.tools
        # Replace before the first child admission (ChildPort has no cached role).
        binding.tools = (replace(original, description='replacement'),)
        await session.initialize_capabilities()
        assert not session.children.capabilities.inherited(original)
        assert not session.children.capabilities.inherited(binding.tools[0])
        # A stale original still in the parent's active inventory is not a new
        # ordinary grant, and neither identity has a scoped delegation factory.
        session.agent.set_tools(tuple(original if tool is binding.tools[0] else tool
                                      for tool in session.agent.state.tools))
        with pytest.raises(ValueError, match='delegable'):
            await session.children.runtime.start('cannot inherit retired application tool',
                                                  config={'tools': [original.name]})
        assert not contexts and not session.children.runtime.snapshot()['children']
    finally:
        await session.aclose()
