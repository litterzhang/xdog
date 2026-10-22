"""Application providers outlive conversation navigation, without feature adapters."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.capabilities.runtime import ApplicationPort
from xdog.agent.core import AgentTool
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.test_child_agents import create


class Provider:
    name = 'application_example'
    scopes = ('application',)
    tool_names = ('app_example',)
    requirements = ()

    def __init__(self):
        self.opens = []
        self.closes = 0

    async def open(self, host):
        assert isinstance(host, ApplicationPort)
        self.opens.append(host.identity)
        provider = self

        class Binding:
            tools = (AgentTool(name='app_example', description='Application tool', parameters={'type': 'object'},
                               execute=lambda *_args, **_kwargs: None),)

            async def aclose(self):
                provider.closes += 1
        return Binding()


async def test_application_binding_is_reused_across_new_session_and_closed_once(tmp_path, monkeypatch):
    provider = Provider()
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: (provider,) if scope == 'application' else ())
    session, contexts = create(tmp_path, 'allow-all')
    try:
        assert not provider.opens  # Metadata discovery does not open resources.
        await session.send_message('first')
        binding = session.application_capabilities.get(provider.name)
        original = next(tool for tool in session.agent.state.tools if tool.name == 'app_example')
        first_id = session.session_id
        await asyncio.to_thread(session.new_session)
        assert session.session_id != first_id
        await session.send_message('second')
        assert session.application_capabilities.get(provider.name) is binding
        assert next(tool for tool in session.agent.state.tools if tool.name == 'app_example') is original
        assert len(provider.opens) == 1 and provider.closes == 0
        assert all(any(tool.name == 'app_example' for tool in context.tools) for context in contexts)
        child = await session.children.runtime.start('child', config={'tools': ['current_time'], 'skills': []})
        await session.children.runtime.control('wait', child)
        assert {tool.name for tool in contexts[-1].tools} == {'current_time'}
        assert len(provider.opens) == 1  # Child construction does not discover application providers.
    finally:
        await session.aclose()
    assert provider.closes == 1
    await session.aclose()
    assert provider.closes == 1


async def test_application_tool_does_not_override_explicit_active_tool_selection(tmp_path, monkeypatch):
    provider = Provider()
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: (provider,) if scope == 'application' else ())
    session, contexts = create(tmp_path, 'allow-all')
    try:
        session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
        await session.send_message('no tools')
        assert all(not context.tools for context in contexts)
        assert any(tool.name == 'app_example' for tool in session.agent.state.tools)
    finally:
        await session.aclose()


@pytest.mark.parametrize('mode,accepted', [('allow-all', True), ('deny', False)])
async def test_installed_eyes_tool_is_loaded_by_real_coding_and_uses_normal_permissions(tmp_path, mode, accepted):
    from xdog.agent.skills import SkillManager

    session, _ = create(tmp_path, mode, skills=SkillManager(tmp_path / 'skills'))
    data = {'version': 2, 'name': 'coding-explicit-owner',
        'sources': [{'id': 'source', 'type': 'file', 'root': '.', 'include': ['*.txt']}],
        'detectors': [{'id': 'changes', 'type': 'file.change', 'source': 'source'}],
        'handlers': [{'id': 'store', 'type': 'events'}],
        'routes': [{'id': 'route', 'detector': 'changes', 'handler': 'store', 'event_types': ['file.created']}]}
    path = tmp_path / 'eyes.json'
    path.write_text(json.dumps(data))
    arguments = {'action': 'create', 'owner': 'chosen-by-main',
                 'definition_path': str(path)}

    def model(_model, context, _options):
        assert any(tool.name == 'eyes' for tool in context.tools)
        message = AssistantMessage(content=(ToolCall(id='create-eye', name='eyes', arguments=arguments),)
            if context.messages[-1].role == 'user' else (TextContent(text='done'),),
            stop_reason='toolUse' if context.messages[-1].role == 'user' else 'stop')
        return EventStream.empty(message)

    session.agent.set_stream_fn(model)
    try:
        await session.send_message('create observation')
        result = next(message for message in session.messages
                      if isinstance(message, ToolResultMessage) and message.tool_call_id == 'create-eye')
        assert result.is_error is not accepted
        assert session.application_capabilities.get('eyes') is not None
        assert session.capabilities.get('eyes') is None  # Not a session provider.
        if accepted:
            cli = json.loads(''.join(part.text for part in result.content))
            assert cli['exit_code'] == 0
            saved = json.loads(cli['stdout'])
            assert saved['owner'] == 'chosen-by-main' and 'workspace' not in saved
            assert 'tool:' not in saved['owner']
    finally:
        await session.aclose()


async def test_application_and_session_default_skills_merge_without_granting_active_tools(tmp_path, monkeypatch):
    from xdog.agent import Agent, AgentConfig
    from xdog.agent.skills import SkillManager
    from xdog.ai.types import system_prompt_text
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.settings_manager import SettingsManager

    application = Provider()
    application.default_skills = ('application-instructions',)
    scoped = Provider()
    scoped.name, scoped.scopes, scoped.tool_names = 'session_example', ('session',), ()
    scoped.default_skills = ('session-instructions',)
    async def open_session(host):
        class Binding:
            tools = ()
            async def aclose(self):
                pass
        return Binding()
    scoped.open = open_session
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
        lambda scope: {'application': (application,), 'session': (scoped,)}.get(scope, ()))
    skills = SkillManager(tmp_path / 'skills', packaged={})
    for slug in ('application-instructions', 'session-instructions', 'chosen'):
        skills.save_skill(slug, f'Unique body for {slug}.')
    contexts = []
    def stream(_model, context, _options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model'), skills=skills),
        session_data=manager.create_session(model='test/model', working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'),
        tool_registry=None, bash=None, working_dir=tmp_path)
    try:
        assert not application.opens and not session.active_skills and not contexts
        session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
        await session.send_message('first')
        expected = {'application-instructions', 'session-instructions'}
        assert session.active_skills == expected
        assert set(session.agent.active_skills) == expected
        assert all(f'Unique body for {slug}.' in (system_prompt_text(contexts[0].system_prompt) or '')
                   for slug in expected)
        await asyncio.to_thread(session.activate_skill, 'chosen')
        assert session.active_skills == expected | {'chosen'}
        assert await asyncio.to_thread(session.deactivate_skill, 'chosen')
        assert session.active_skills == expected
        assert not session.agent.active_tools
        assert all(not context.tools for context in contexts)
        assert any(tool.name == 'app_example' for tool in session.agent.state.tools)
        await asyncio.to_thread(session.new_session)
        await session.send_message('second')
        assert session.active_skills == expected and len(application.opens) == 1
        await session.application_capabilities.aclose()
        assert session.active_skills == {'session-instructions'}
    finally:
        await session.aclose()
    assert application.closes == 1


async def test_installed_flow_eyes_are_parent_only_in_real_coding_child_entrypoint(tmp_path, monkeypatch):
    from importlib.metadata import entry_points

    entries = tuple(entry for entry in entry_points(group='xdog.capabilities') if entry.name in {'flow', 'eyes'})
    assert {entry.name for entry in entries} == {'flow', 'eyes'}
    monkeypatch.setattr('xdog.agent.capabilities.runtime.entry_points', lambda **_: entries)
    session, contexts = create(tmp_path, 'allow-all')
    try:
        await session.initialize_capabilities()
        actual = {tool.name: tool for tool in session.agent.active_tools if tool.name in {'flow', 'eyes'}}
        assert set(actual) == {'flow', 'eyes'}
        for name, tool in actual.items():
            assert not session.children.capabilities.inherited(tool)
            for config in ({'tools': [name]}, {'tools': [name], 'tool_scopes': {name: {'ids': ['a']}}}):
                with pytest.raises(ValueError, match='delegable'):
                    await session.children.runtime.start('no capability scope grant', config=config)
        assert not session.children.runtime.snapshot()['children'] and not contexts
        identifier = await session.children.runtime.start('ordinary child')
        await session.children.runtime.control('wait', identifier)
        assert contexts and all(not {'flow', 'eyes'} & {tool.name for tool in context.tools or ()}
                                for context in contexts)
        assert not {'flow', 'eyes'} & set(session.agent.active_skills)
        assert all(not session.children.capabilities.inherited(tool) for tool in actual.values())
    finally:
        await session.aclose()
