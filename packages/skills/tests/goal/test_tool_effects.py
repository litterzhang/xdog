"""Goal classification is pure data, not authority or execution admission."""
from copy import copy
from dataclasses import replace

import pytest
from xdog.agent import Agent
from xdog.agent.capabilities.runtime import ParentPort
from xdog.agent.core import AgentTool
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.tool_effects import ToolEffect

from packages.skills.tests.goal.test_runtime import close, setup

REPORT = {'expected_version': 0, 'progress': 'baseline', 'completed': [], 'evidence': [],
          'blockers': [], 'next_steps': [], 'summary': 'Starting'}


@pytest.mark.parametrize(('args', 'effect'), [
    *[({'action': action}, ToolEffect('read', 'tool_data'))
      for action in ('status', 'summary', 'tasks', 'history', 'plan')],
    ({'action': 'status', 'detail': True}, ToolEffect('read', 'tool_data')),
    ({'action': 'tasks', 'status': 'completed', 'worker': 'main'}, ToolEffect('read', 'tool_data')),
    ({'action': 'plan', 'content': ''}, ToolEffect('write', 'tool_data')),
    ({'action': 'report', 'report': REPORT}, ToolEffect('write', 'tool_data')),
    ({'action': 'task_update', 'task': {'id': 'one', 'expected_version': 0}}, ToolEffect('write', 'tool_data')),
    *[({'action': action, **fields}, ToolEffect('write', 'tool_data', True)) for action, fields in (
        ('create', {'title': 'Goal'}), ('update', {'version': 1}), ('wait', {'until': 'user'}),
        ('pause', {}), ('resume', {}), ('complete', {'summary': 'done'}), ('clear', {}))],
])
async def test_current_goal_effects_do_not_do_io_or_change_authority(tmp_path, monkeypatch, args, effect):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        await runtime.flush()
        authorized[0] = False
        tool, = runtime.tools

        def forbidden(*args, **kwargs):
            raise AssertionError('Effect query must not read/create/authorize a Goal')

        with monkeypatch.context() as pure:
            pure.setattr(runtime.service, 'current', forbidden)
            pure.setattr(runtime.service, 'create', forbidden)
            pure.setattr(runtime.service, 'files', forbidden)
            pure.setitem(parent.__dict__, 'user_authorized', forbidden)
            pure.setattr('xdog.skills.goal.runtime.Draft202012Validator', forbidden)
            assert runtime.tool_effect(tool, args) == effect
        assert authorized == [False]
        assert await runtime.service.current() is None
        assert not sent
        assert not list(tmp_path.glob('.xdog/goals/*'))
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('args', [
    {}, {'action': []}, {'action': 'unknown'}, {'action': 'status', 'content': 'write'},
    {'action': 'summary', 'detail': True}, {'action': 'plan', 'report': REPORT},
    {'action': 'report', 'report': REPORT, 'content': 'other'},
    {'action': 'plan', 'content': 'write', 'limit': 1}, {'action': 'status', 'goal_id': 1},
    {'action': 'status', 'detail': 'true'}, {'action': 'tasks', 'task': {}},
    {'action': 'task_update', 'task': {'id': 'one', 'expected_version': 0, 'authority': True}},
    {'action': 'task_update', 'task': {'id': 'one', 'expected_version': True}},
    {'action': 'report', 'report': {'expected_version': 0}}, {'action': 'report'},
    {'action': 'create'}, {'action': 'update'}, {'action': 'task_update', 'task': None},
    {'action': 'status', 'offset': True}, {'action': 'plan', 'content': None},
    {'action': 'plan', 'revision': 'invalid'}, {'action': 'tasks', 'offset': 1},
])
async def test_invalid_or_cross_action_fields_never_receive_effect(tmp_path, monkeypatch, args):
    runtime, parent, *_ = await setup(tmp_path, monkeypatch)
    try:
        assert runtime.tool_effect(runtime.tools[0], args) is None
    finally:
        await close(runtime, parent)


async def test_actual_parent_identity_retirement_and_execution_guard(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    tool, = runtime.tools
    successor = None
    try:
        for forged in (copy(tool), replace(tool), AgentTool('goal')):
            assert runtime.tool_effect(forged, {'action': 'status'}) is None
        with monkeypatch.context() as stale:
            stale.setitem(parent.__dict__, 'valid', lambda: False)
            assert runtime.tool_effect(tool, {'action': 'status'}) is None
        original_valid, original_accepting = host.valid, host.accepting
        host.valid = lambda: False
        assert runtime.tool_effect(tool, {'action': 'status'}) is None
        host.valid, host.accepting = original_valid, lambda: False
        assert runtime.tool_effect(tool, {'action': 'status'}) is None
        host.accepting = original_accepting
        successor = ParentPort(Agent(lambda *args: None), ExtensionRuntime((), tmp_path / 'next', tmp_path),
                               parent.children, lambda: True, lambda: False)
        host.owns_parent = lambda selected: selected is successor
        assert runtime.tool_effect(tool, {'action': 'status'}) is None
        runtime.attach(successor)
        assert runtime.tool_effect(tool, {'action': 'status'}) is None
        current, = runtime.tools
        assert runtime.tool_effect(current, {'action': 'status'}) == ToolEffect('read', 'tool_data')
        assert runtime.tool_effect(current, {'action': 'create', 'title': 'No authority'}) == ToolEffect(
            'write', 'tool_data', True)
        assert (await current.execute('create', {'action': 'create', 'title': 'No authority'})).is_error
        assert await runtime.service.current() is None
        assert (await tool.execute('stale', {'action': 'status'})).is_error
        await runtime.aclose()
        assert runtime.tool_effect(current, {'action': 'status'}) is None
    finally:
        await close(runtime, parent)
        if successor is not None:
            await successor.extensions.aclose()
            await successor.agent.aclose()
