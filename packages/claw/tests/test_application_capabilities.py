"""Generic application defaults compose with group defaults, never tool authority."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from xdog.agent.capabilities.runtime import ApplicationPort, CapabilitySet
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, system_prompt_text
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


class Provider:
    requirements = ()

    def __init__(self, scope):
        self.name = f'{scope}_example'
        self.scopes = (scope,)
        self.tool_names = (self.name,)
        self.default_skills = (f'{scope}-instructions',)
        self.opens = []
        self.calls = []
        self.closes = 0

    async def open(self, host):
        self.opens.append(host)
        if self.scopes == ('application',):
            assert isinstance(host, ApplicationPort)
        async def execute(_id, args, *_args, **_kwargs):
            self.calls.append(args)
            return AgentToolResult.from_text('executed')
        async def close():
            self.closes += 1
        return SimpleNamespace(tools=(AgentTool(name=self.name, description='Example tool',
            parameters={'type': 'object'}, execute=execute),), aclose=close)


def test_application_metadata_discovery_is_inert(monkeypatch):
    provider = Provider('application')
    entry = SimpleNamespace(name=provider.name, load=lambda: provider)
    monkeypatch.setattr('xdog.agent.capabilities.runtime.entry_points', lambda **_: (entry,))
    forbidden = Mock(side_effect=AssertionError('Discovery cannot start work'))
    monkeypatch.setattr('subprocess.Popen', forbidden)
    monkeypatch.setattr('asyncio.create_subprocess_exec', forbidden)
    monkeypatch.setattr('xdog.agent.agent.Agent.__init__', forbidden)
    selected = CapabilitySet('application')
    assert selected.declared_tools == {provider.name}
    assert selected.default_skills == frozenset()
    assert not selected.tools and not provider.opens
    forbidden.assert_not_called()


@pytest.mark.parametrize('restricted', [False, True])
async def test_live_application_defaults_merge_in_list_selection_and_turn_without_permission_grant(
        tmp_path, monkeypatch, restricted):
    application, group = Provider('application'), Provider('group')
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: {'application': (application,), 'group': (group,)}.get(scope, ()))
    contexts = []
    def stream(_model, context, _options):
        contexts.append(context)
        message = (AssistantMessage(content=(ToolCall(id='app-call', name=application.name, arguments={}),),
                                    stop_reason='toolUse') if context.messages[-1].role == 'user'
                   else AssistantMessage(content=(TextContent(text='done'),)))
        return EventStream.empty(message)
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=stream)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace')))
    runtime = app._runtimes['main']
    for slug in ('application-instructions', 'group-instructions', 'chosen'):
        runtime.skill_repo.save_skill(slug, f'Unique body for {slug}.')
    try:
        assert not application.opens and not group.opens and not contexts
        assert not runtime.default_skills
        await app.start()
        assert runtime.default_skills == {'group-instructions'}
        await app.application_capabilities.start(ApplicationPort())
        await app.refresh_application_capabilities()
        session = await runtime.prepare_session(allow_reset=False)
        session.access.set_read_only(restricted)
        expected = {'application-instructions', 'group-instructions'}
        listing = await session.skills_control('list')
        assert set(listing['active']) == expected
        assert all(item['active'] for item in listing['skills'] if item['slug'] in expected)
        await session.skills_control('load', 'chosen')
        assert set(session.agent.active_skills) == expected | {'chosen'}
        await session.skills_control('unload', 'all')
        assert set(session.agent.active_skills) == expected
        assert runtime.transcript_store.get_active_skills(session.meta.session_id) == ()
        result = await app.route_message(UserInput(group_id='main', content='use application tool', sender='test'))
        assert result.error is None
        assert len(contexts) == 2 and application.calls == ([] if restricted else [{}])
        prompt = system_prompt_text(contexts[0].system_prompt) or ''
        assert all(f'Unique body for {slug}.' in prompt for slug in expected)
        tool_result = next(message for message in session.agent.messages
                           if isinstance(message, ToolResultMessage) and message.tool_call_id == 'app-call')
        assert tool_result.is_error is restricted
        assert any(tool.name == application.name for tool in contexts[0].tools)
        assert session.access.state.read_only is restricted
        runtime.reset_session()
        fresh = await runtime.prepare_session(allow_reset=False)
        assert fresh is not session
        assert set((await fresh.skills_control('list'))['active']) == expected
        assert len(application.opens) == 1
        await app.application_capabilities.aclose()
        assert set((await fresh.skills_control('list'))['active']) == {'group-instructions'}
        await fresh.skills_control('unload', 'all')
        assert set(fresh.agent.active_skills) == {'group-instructions'}
    finally:
        await app.stop()
    assert application.closes == group.closes == 1


@pytest.mark.parametrize('selection', ['configured', 'active'])
async def test_application_defaults_do_not_override_tool_selection(tmp_path, monkeypatch, selection):
    provider = Provider('application')
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: (provider,) if scope == 'application' else ())
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy',
        stream_fn=lambda *_: EventStream.empty(AssistantMessage()))
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'),
                             enabled_tools=() if selection == 'configured' else None))
    runtime = app._runtimes['main']
    slug = provider.default_skills[0]
    body = f'Unique body for {slug}.'
    runtime.skill_repo.save_skill(slug, body)
    try:
        await app.start()
        await app.application_capabilities.start(ApplicationPort())
        await app.refresh_application_capabilities()
        runtime = app._runtimes['main']
        session = await runtime.prepare_session(allow_reset=False)
        if selection == 'active':
            session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
        await session.skills_control('unload', 'all')
        assert slug in session.agent.active_skills
        assert body in (system_prompt_text(session.agent.state.system_prompt) or '')
        assert not any(tool.name == provider.name for tool in session.agent.active_tools)
        if selection == 'active':
            assert not session.agent.active_tools
        assert not provider.calls
    finally:
        await app.stop()
