"""Short SDK loading uses the real Agent/registries, never an implicit host runtime."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, BeforeToolCallResult, ToolRepo
from xdog.agent.capabilities import ApplicationPort, CapabilitySet, discover
from xdog.agent.skills import SkillRepo
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, system_prompt_text
from xdog.ai.utils.event_stream import EventStream


def install(monkeypatch, tmp_path, *, skill_names=('procedure',), body='PACKAGED_PROCEDURE_BODY'):
    events = []
    directories = {}
    for slug in skill_names:
        directory = tmp_path / 'installed' / slug
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(f'---\nname: {slug}\ndescription: packaged instructions\n---\n{body}')
        directories[slug] = directory
    monkeypatch.setattr('xdog.agent.skills.repo.packaged_skills', lambda: directories)

    async def execute(*args, **kwargs):
        events.append('execute')
        return AgentToolResult.from_text('actual result')
    actual = AgentTool(name='feature', description='feature', parameters={'type': 'object'}, execute=execute)

    class Binding:
        tools = (actual,)
        async def aclose(self):
            events.append('close')

    class Provider:
        name = 'feature'
        scopes = ('application',)
        tool_names = ('feature',)
        requirements = ()
        async def open(self, host):
            assert isinstance(host, ApplicationPort)
            events.append('open')
            return Binding()

    provider = Provider()
    provider.skill_names = skill_names
    def rejected():
        pytest.fail('An unselected provider must not be imported or opened')
    entries = (SimpleNamespace(name='feature', load=lambda: provider),
               SimpleNamespace(name='unused', load=rejected))
    monkeypatch.setattr('xdog.agent.capabilities.runtime.entry_points', lambda **_: entries)
    return events, directories


def open_capabilities(names, *, scope='application', host=None):
    return CapabilitySet(scope, names=names, skill_repo=SkillRepo(builtin=False)).open(host)


def no_model(*args, **kwargs):
    pytest.fail('Assembly must not call the model')


async def test_named_loading_assembles_tools_and_packaged_skill_without_manual_manager(monkeypatch, tmp_path):
    events, directories = install(monkeypatch, tmp_path)
    # Neither cwd nor a local same-name skill can override packaged instructions.
    local = tmp_path / 'local'
    (local / 'procedure').mkdir(parents=True)
    (local / 'procedure' / 'SKILL.md').write_text('LOCAL_OVERRIDE')
    monkeypatch.chdir(local)
    async with open_capabilities(['feature']) as assembly:
        async with assembly.create_agent(no_model) as agent:
            assert type(agent) is Agent
            assert {tool.name for tool in agent.local_tools} == {'feature', 'skill'}
            assert agent.active_skills == ()
            prompt = str(agent.state.system_prompt)
            assert 'procedure' in prompt and 'PACKAGED_PROCEDURE_BODY' not in prompt
            assert 'LOCAL_OVERRIDE' not in prompt
            skill = next(tool for tool in agent.local_tools if tool.name == 'skill')
            listing = await skill.execute('list', {'action': 'list'})
            assert [item['slug'] for item in json.loads(listing.content[0].text)['skills']] == ['procedure']
            loaded = await skill.execute('load', {'action': 'load', 'slug': 'procedure'})
            assert 'PACKAGED_PROCEDURE_BODY' in loaded.content[0].text
            assert 'LOCAL_OVERRIDE' not in loaded.content[0].text
            assert agent.active_skills == ()  # Progressive disclosure, not activation.
            denied = await skill.execute('load', {'action': 'load', 'slug': 'unselected'})
            assert denied.is_error
        assert agent.closed and events == ['open']
    assert events == ['open', 'close']
    assert (directories['procedure'] / 'SKILL.md').is_file()
    assert not (local / 'skills').exists()


async def test_explicit_activation_and_permission_hooks_use_real_agent_loop(monkeypatch, tmp_path):
    events, _ = install(monkeypatch, tmp_path)
    seen = []
    def stream(model, context, options):
        seen.append(context)
        response = (AssistantMessage(content=(ToolCall(id='one', name='feature', arguments={}),),
                                     stop_reason='toolUse') if len(seen) == 1
                    else AssistantMessage(content=(TextContent(text='done'),)))
        return EventStream.empty(response)
    async def deny(ctx, cancel=None):
        assert ctx.tool_call.name == 'feature'
        events.append('permission')
        return BeforeToolCallResult(block=True, reason='Host denied execution')
    async with open_capabilities(['feature']) as assembly:
        async with assembly.create_agent(stream, config=AgentConfig(model='offline'),
                                         active_skills=['procedure'], before_tool_call=deny) as agent:
            await (await agent.prompt('try')).collect()
            assert agent.active_skills == ('procedure',)
            assert 'PACKAGED_PROCEDURE_BODY' in (system_prompt_text(seen[0].system_prompt) or '')
            result = next(message for message in agent.messages if isinstance(message, ToolResultMessage))
            assert result.is_error and 'Host denied' in str(result.content)
    assert events == ['open', 'permission', 'close']


async def test_sdk_auto_catalog_restore_does_not_start_models_or_replay_tools(monkeypatch, tmp_path):
    events, _ = install(monkeypatch, tmp_path)
    async with open_capabilities(['feature']) as assembly:
        async with assembly.create_agent(no_model, active_skills=['procedure']) as original:
            snapshot = original.dump()
    async with open_capabilities(['feature']) as assembly:
        async with assembly.create_agent(no_model) as restored:
            assembly.restore_agent(restored, snapshot)
            assert restored.active_skills == ('procedure',)
            assert {tool.name for tool in restored.local_tools} == {'feature', 'skill'}
            assert 'PACKAGED_PROCEDURE_BODY' in str(restored.state.system_prompt)
            json.dumps(restored.dump())
    assert events == ['open', 'close', 'open', 'close']


async def test_supplied_tools_preserve_identity_and_capabilities_need_no_second_selection(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    async def execute(*args, **kwargs):
        return AgentToolResult.from_text('isolated')
    custom = AgentTool(name='workspace', description='host tool', parameters={'type': 'object'}, execute=execute)
    async with open_capabilities(['feature']) as assembly:
        async with assembly.create_agent(no_model, tool_repo=ToolRepo(*[custom], builtin=False)) as agent:
            assert any(item is custom for item in agent.local_tools)
            assert {item.name for item in agent.local_tools} == {'feature', 'skill', 'workspace'}


async def test_explicit_host_skill_reader_is_preserved_not_replaced(monkeypatch, tmp_path):
    events, _ = install(monkeypatch, tmp_path)
    custom = AgentTool(name='skill', description='custom', parameters={'type': 'object'})
    async with open_capabilities(['feature']) as assembly:
        async with assembly.create_agent(no_model, tool_repo=ToolRepo(*[custom], builtin=False)) as agent:
            assert next(tool for tool in agent.local_tools if tool.name == 'skill') is custom
    assert events == ['open', 'close']


async def test_missing_declared_packaged_skill_fails_and_closes_bindings(monkeypatch, tmp_path):
    events, _ = install(monkeypatch, tmp_path)
    monkeypatch.setattr('xdog.agent.skills.repo.packaged_skills', lambda: {})
    with pytest.raises(ValueError, match='procedure'):
        async with open_capabilities(['feature']) as assembly:
            assembly.create_agent(no_model)
    assert events == ['open', 'close']


async def test_tools_only_capability_needs_no_packaged_skill_or_skill_reader(monkeypatch, tmp_path):
    events, _ = install(monkeypatch, tmp_path, skill_names=())
    async with open_capabilities(['feature']) as assembly:
        async with assembly.create_agent(no_model) as agent:
            assert [item.name for item in agent.local_tools] == ['feature']
            assert not agent.active_skills
    assert events == ['open', 'close']


async def test_body_failure_and_cleanup_failure_are_both_reported(monkeypatch):
    class Provider:
        name = 'broken'
        scopes = ('application',)
        tool_names = requirements = ()
        async def open(self, host):
            class Binding:
                tools = ()
                async def aclose(self):
                    raise OSError('cleanup failed')
            return Binding()
    entry = SimpleNamespace(name='broken', load=Provider)
    monkeypatch.setattr('xdog.agent.capabilities.runtime.entry_points', lambda **_: (entry,))
    with pytest.raises(BaseExceptionGroup) as failure:
        async with open_capabilities(['broken']):
            raise RuntimeError('body failed')
    assert 'body failed' in str(failure.value.exceptions[0])
    assert 'cleanup failed' in str(failure.value.exceptions[1])


@pytest.mark.parametrize('scope', ['session', 'group'])
async def test_nonapplication_loading_requires_real_host_before_start(monkeypatch, scope):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.entry_points', lambda **_: ())
    with pytest.raises(ValueError, match='explicit host'):
        async with open_capabilities([], scope=scope):
            pytest.fail('No scope')


async def test_empty_selection_does_not_load_installed_providers(monkeypatch, tmp_path):
    events, _ = install(monkeypatch, tmp_path)
    async with open_capabilities([]) as assembly:
        async with assembly.create_agent(no_model) as agent:
            assert not agent.local_tools and not agent.active_skills
    assert not events


@pytest.mark.parametrize('names', [['missing'], ['feature', 'missing'], ['feature', 'feature'], ['../bad'], 'feature'])
def test_explicit_missing_duplicate_or_invalid_names_fail_before_start(monkeypatch, tmp_path, names):
    events, _ = install(monkeypatch, tmp_path)
    with pytest.raises((TypeError, ValueError)):
        CapabilitySet('application', names=names)
    assert not events


def test_selected_provider_in_wrong_scope_is_unavailable(monkeypatch, tmp_path):
    events, _ = install(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match='unavailable in group'):
        discover('group', names=['feature'])
    assert not events


def test_packaged_only_manager_cannot_read_or_write_local_skills(monkeypatch, tmp_path):
    _, directories = install(monkeypatch, tmp_path)
    manager = SkillRepo.from_packaged(['procedure'])
    assert manager.load_skill('procedure').directory == directories['procedure']
    assert not manager._directories
    before = (directories['procedure'] / 'SKILL.md').read_bytes()
    for operation in (lambda: manager.save_skill('procedure', 'changed'),
                      lambda: manager.patch_skill('procedure', 'changed')):
        with pytest.raises(ValueError, match='read-only'):
            operation()
    assert not manager.remove_skill('procedure')
    assert (directories['procedure'] / 'SKILL.md').read_bytes() == before


async def test_real_flow_capability_needs_no_manual_tool_or_skill_registration(monkeypatch):
    flow = pytest.importorskip('xdog.flow.capability')
    from xdog.agent.skills import packaged_skills

    if 'flow' not in packaged_skills():
        pytest.skip('Optional packaged Flow skill unavailable')
    def unexpected():
        pytest.fail('Loading Flow must not import/start Eyes')
    entries = (SimpleNamespace(name='flow', load=lambda: flow.provider),
               SimpleNamespace(name='eyes', load=unexpected))
    monkeypatch.setattr('xdog.agent.capabilities.runtime.entry_points', lambda **_: entries)
    async with open_capabilities(['flow']) as assembly:
        async with assembly.create_agent(no_model) as agent:
            assert {tool.name for tool in agent.local_tools} == {'flow', 'skill'}
            assert agent.active_skills == ()
            skill = next(tool for tool in agent.local_tools if tool.name == 'skill')
            result = await skill.execute('flow-instructions', {'action': 'load', 'slug': 'flow'})
            assert not result.is_error and 'xdog-flow' in result.content[0].text
            assert 'shell' in result.content[0].text
            binding = assembly.sets[0].get('flow')
            assert not binding._calls  # No workflow/CLI process was started by loading.
    assert binding.closed


def test_package_readme_capability_quick_start_runs_offline(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    pytest.importorskip('xdog.flow.capability')
    readme = (Path(__file__).parents[1] / 'README.md').read_text()
    section = readme.split('## Capability SDK quick start\n', 1)[1].split('\n## ', 1)[0]
    source = section.split('```python\n', 1)[1].split('```', 1)[0]
    result = subprocess.run([sys.executable, '-c', source], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SDK capabilities: ['current_time', 'flow', 'skill']" in result.stdout
