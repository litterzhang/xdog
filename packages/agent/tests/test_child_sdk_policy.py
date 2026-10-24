"""Inert child SDK and live authority checks need no installed application runtime."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import threading

import pytest
from xdog.agent import Agent, AgentConfig, AgentContext, AgentTool, BeforeToolCallContext, ParentHandle
from xdog.agent.children import ChildClient, ChildConfig
from xdog.agent.skills import SkillRepo
from xdog.ai.types import AssistantMessage, ToolCall


def context(tool, action='read'):
    args = {'action': action}
    return BeforeToolCallContext(AssistantMessage(content=()), ToolCall('call', tool.name, args), args,
                                 AgentContext(system_prompt='', messages=[], tools=[tool]))


def test_parent_tool_grant_checks_subset_identity_actions_and_live_revocation():
    tool = AgentTool('fs', parameters={'type': 'object', 'properties': {'action': {'enum': ['read', 'write']}}})
    active = [tool]
    live = True
    parent = ParentHandle(alive=lambda: live, tools=lambda: active, skills=lambda: None)
    grant = parent.capture_tools(None, (('fs', ('read',)),))
    parent.check_tool(grant, context(tool))
    with pytest.raises(ValueError, match='action'):
        parent.check_tool(grant, context(tool, 'write'))
    with pytest.raises(ValueError, match='exceeds'):
        parent.capture_tools(('fs', 'secret'))
    active.append(AgentTool('later'))
    with pytest.raises(ValueError, match='delegation'):
        parent.check_tool(grant, context(active[-1]))
    active[0] = dataclasses.replace(tool)
    with pytest.raises(ValueError, match='delegation'):
        parent.check_tool(grant, context(tool))
    live = False
    with pytest.raises(ValueError, match='retired'):
        parent.capture_tools(None)
    with pytest.raises(ValueError, match='retired'):
        parent.check_tool(grant, context(tool), capability_bound=True)


def test_original_schema_cannot_expand_in_place_after_delegation():
    tool = AgentTool('fs', parameters={'type': 'object', 'properties': {'action': {'enum': ['read']}}})
    parent = ParentHandle(alive=lambda: True, tools=lambda: [tool], skills=lambda: None)
    grant = parent.capture_tools(None)
    tool.parameters['properties']['action']['enum'].append('write')
    with pytest.raises(ValueError, match='schema'):
        parent.check_tool(grant, context(tool, 'write'))


def write_skill(root, slug, text):
    directory = root / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'SKILL.md').write_text(f'---\nname: {slug}\ndescription: example\n---\n{text}\n')


async def test_skills_are_selected_detached_readonly_and_rechecked(tmp_path):
    write_skill(tmp_path, 'allowed', 'ALLOW BODY')
    write_skill(tmp_path, 'private', 'PRIVATE BODY')
    manager = SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")
    parent = ParentHandle(alive=lambda: True, tools=lambda: [], skills=lambda: manager)
    child = parent.capture_skills(('allowed',))
    assert [s.slug for s in child.list_skills()] == ['allowed']
    assert child.load_skill('private') is None
    assert 'PRIVATE' not in child.skills_summary()
    assert not parent.capture_skills(()).list_skills()
    with pytest.raises(ValueError, match='exceed'):
        parent.capture_skills(('allowed', 'missing'))
    for action in (lambda: child.save_skill('new', 'x'), lambda: child.patch_skill('allowed', 'x'),
                   lambda: child.remove_skill('allowed')):
        with pytest.raises(PermissionError):
            action()
    tool = child.tool(AgentTool('skill'), lambda: parent.live)
    loaded = await tool.execute('call', {'action': 'load', 'slug': 'allowed'})
    assert 'ALLOW BODY' in str(loaded.content)
    with pytest.raises(ValueError, match='delegated'):
        await tool.execute('call', {'action': 'load', 'slug': 'private'})
    with pytest.raises(ValueError, match='list/load'):
        await tool.execute('call', {'action': 'remove', 'slug': 'allowed'})
    write_skill(tmp_path, 'allowed', 'CHANGED')
    assert 'ALLOW BODY' in child.load_skill('allowed').content
    with pytest.raises(ValueError, match='changed'):
        parent.check_skills(child)


async def test_parent_handle_never_comes_from_snapshot_and_is_readonly():
    def no_model(*args):
        pytest.fail('No model call from SDK construction/dump/restore')
    parent = ParentHandle(alive=lambda: True, tools=lambda: [], skills=lambda: None)
    agent = Agent(no_model, config=AgentConfig(model='test/fake'), parent=parent)
    assert agent.parent is parent
    with pytest.raises(AttributeError):
        agent.parent = None
    snapshot = agent.dump()
    assert 'parent' not in snapshot
    assert '_alive' not in json.dumps(snapshot)
    fresh = Agent(no_model, config=AgentConfig(model='test/fake'))
    fresh.restore(snapshot)
    assert fresh.parent is None
    with pytest.raises(ValueError):
        fresh.restore({**snapshot, "parent": {"tools": ["bash"], "owner": "pretend"}})
    assert fresh.parent is None
    agent.restore(snapshot)
    assert agent.parent is parent
    await agent.aclose()
    await fresh.aclose()


async def test_sdk_uses_only_explicit_backend_and_starts_no_scheduler():
    events = []
    class Backend:
        def snapshot(self):
            return {'children': []}
        async def start(self, task, **kwargs):
            events.append(('spawn', task, kwargs))
            return '0123456789ab'
        async def control(self, action, identifier, text='', **kwargs):
            events.append((action, identifier, text))
            return {'status': action}
    before = set(threading.enumerate())
    tasks = set(asyncio.all_tasks())
    sdk = ChildClient(Backend())
    assert sdk.list() == {'children': []} and not events
    handle = await sdk.spawn('TASK', config=ChildConfig(model='test/other', thinking='high'))
    assert events[0][2]['config']['model'] == 'test/other'
    assert await handle.wait() == {'status': 'wait'}
    await handle.set_system_prompt('ROLE')
    await handle.cancel()
    assert set(threading.enumerate()) == before and set(asyncio.all_tasks()) == tasks


def test_independent_model_preserves_own_signatures_and_refuses_withdrawn_catalog():
    from xdog.agent.child_model import resolve_child_model
    from xdog.ai.types import Context, Model, StreamOptions, TextContent, ThinkingContent
    from xdog.ai.utils.event_stream import EventStream
    class Adapter:
        metadata = Model(id='test/child', context_window=64000, max_tokens=2000, reasoning=True)
        calls = []
        def resolve_model(self, model):
            return self.metadata if model == 'test/child' else Model(id='test/parent')
        def __call__(self, model, context, options):
            self.calls.append((model, context, options))
            return EventStream.empty(AssistantMessage())
    adapter = Adapter()
    selected = resolve_child_model(adapter, 'test/parent', StreamOptions(),
                                   ChildConfig(model='test/child', thinking='high'), 128000, True)
    assistant = AssistantMessage(content=(ThinkingContent(thinking='CHILD THOUGHT', thinking_signature='CHILD SIG'),
                                         TextContent(text='answer', text_signature='CHILD TEXT SIG')))
    request = selected.stream(adapter)
    request(selected.id, Context(messages=(assistant,)), selected.options)
    assert adapter.calls[0][0] == 'test/child'
    assert adapter.calls[0][1].messages[0] is assistant
    assert adapter.calls[0][2].thinking == 'high'
    adapter.metadata = None
    with pytest.raises(ValueError, match='unavailable'):
        request(selected.id, Context(messages=(assistant,)), selected.options)
    assert len(adapter.calls) == 1


def test_agent_child_sdk_has_no_host_runtime_import_or_autostart_in_fresh_process():
    import subprocess
    import sys
    script = '''
import importlib.abc
import importlib.util
import sys
import threading
class NoApplications(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('xdog.coding', 'xdog.claw', 'xdog.flow', 'xdog.eyes', 'xdog.skills')):
            raise AssertionError('Agent SDK imported a host: ' + fullname)
sys.meta_path.insert(0, NoApplications())
before = set(threading.enumerate())
from xdog.agent import Agent, AgentConfig, ChildClient, ParentHandle
import xdog.agent.children as sdk
assert not hasattr(sdk, 'ChildSupervisor')
for old in ('supervision_store', 'supervision_extensions', 'supervision_return',
            'child_store', 'child_extensions', 'child_return', 'child_runtime',
            'supervision', 'supervision_compaction', 'supervision_config', 'supervision_images',
            'supervision_model', 'supervision_policy', 'supervision_queue', 'supervision_transfer'):
    assert importlib.util.find_spec('xdog.agent.' + old) is None
agent = Agent(lambda *args: (_ for _ in ()).throw(AssertionError('model')), config=AgentConfig(model='fake'))
assert agent.parent is None
assert set(threading.enumerate()) == before
'''
    result = subprocess.run([sys.executable, '-I', '-c', script], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_default_parent_skill_catalog_never_follows_fifo_or_symlink(tmp_path):
    import os
    root = tmp_path / 'skills'
    directory = root / 'unsafe'
    directory.mkdir(parents=True)
    os.mkfifo(directory / 'SKILL.md')
    manager = SkillRepo(builtin=False, packaged={}).register(directory=root, name="shared")
    handle = ParentHandle(alive=lambda: True, tools=lambda: [], skills=lambda: manager)
    with pytest.raises(ValueError, match='unsafe|unavailable'):
        handle.capture_skills(('unsafe',))
    with pytest.raises(ValueError, match='unsafe|unavailable'):
        handle.skill_catalog()
    (directory / 'SKILL.md').unlink()
    private = tmp_path / 'private'
    private.write_text('PRIVATE')
    (directory / 'SKILL.md').symlink_to(private)
    with pytest.raises(ValueError, match='unsafe|unavailable'):
        handle.capture_skills(('unsafe',))


def test_host_restricted_parent_catalog_is_authority_not_all_files_on_disk(tmp_path):
    write_skill(tmp_path, 'allowed', 'OK')
    write_skill(tmp_path, 'private', 'PRIVATE')
    manager = SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")
    allowed = manager.load_skill('allowed')
    manager.list_skills = lambda: [allowed]
    parent = ParentHandle(alive=lambda: True, tools=lambda: [], skills=lambda: manager)
    assert parent.skill_catalog() == ('allowed',)
    with pytest.raises(ValueError, match='catalog'):
        parent.capture_skills(('private',))
