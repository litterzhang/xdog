"""Goal delegates only from its actual parent tool and exact explicit task scope."""
from dataclasses import replace

import pytest
from xdog.agent.children import ChildSpec
from xdog.skills.goal.host import OperationContext

from packages.skills.tests.goal.test_runtime import close, setup


async def ready(tmp_path, monkeypatch):
    values = await setup(tmp_path, monkeypatch)
    runtime, parent, _, state, *_ = values
    state['state'] = 'running'
    parent.agent.set_tools(runtime.tools)
    goal = await runtime.service.create('Parent', 'Explicit task grant', context=OperationContext(True))
    await runtime.service.task({'id': 'assigned', 'title': 'Assigned task', 'expected_version': 0})
    await runtime.flush()
    return values, goal


def spec(scope):
    return ChildSpec(id='actual-child', task='Update assigned task', access='inherit', tools=('goal',),
                     tool_scopes={'goal': scope})


async def test_actual_goal_binding_returns_only_task_tool_without_starting_worker(tmp_path, monkeypatch):
    values, goal = await ready(tmp_path, monkeypatch)
    runtime, parent, *_ = values
    try:
        scope = {'goal_id': goal.id, 'task_ids': ['assigned']}
        tool = await runtime.delegate_child_tool(runtime.tools[0], spec(scope), scope)
        assert tool.name == 'goal'
        assert tool.parameters['properties']['action']['enum'] == ['tasks', 'task_update']
        assert tool.parameters['properties']['task_id']['enum'] == ['assigned']
        assert not (await tool.execute('read', {'action': 'tasks', 'task_id': 'assigned'})).is_error
        parent.agent.set_tools(())
        result = await tool.execute('revoked', {'action': 'task_update',
            'task': {'id': 'assigned', 'expected_version': 1, 'status': 'completed'}})
        assert result.is_error
        assert runtime.service.files(goal).list()[0].status == 'pending'
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('patch', [
    {}, {'task_ids': []}, {'task_ids': ['assigned', 'assigned']}, {'task_ids': ['missing']},
    {'task_ids': ['../assigned']}, {'task_ids': 'assigned'}, {'goal_id': 'other'},
    {'goal_id': False}, {'untrusted_owner': 'user'},
])
async def test_bad_or_ungranted_scope_fails_before_any_child_work(tmp_path, monkeypatch, patch):
    values, goal = await ready(tmp_path, monkeypatch)
    runtime, parent, *_ = values
    try:
        scope = {} if not patch else {'goal_id': goal.id, 'task_ids': ['assigned'], **patch}
        with pytest.raises(ValueError):
            await runtime.delegate_child_tool(runtime.tools[0], spec(scope), scope)
        assert runtime.service.files(goal).list()[0].version == 1
    finally:
        await close(runtime, parent)


async def test_copied_tool_and_mismatched_spec_scope_are_not_delegation_authority(tmp_path, monkeypatch):
    values, goal = await ready(tmp_path, monkeypatch)
    runtime, parent, *_ = values
    try:
        scope = {'goal_id': goal.id, 'task_ids': ['assigned']}
        with pytest.raises(ValueError, match='Original Goal parent'):
            await runtime.delegate_child_tool(replace(runtime.tools[0]), spec(scope), scope)
        with pytest.raises(ValueError, match='scope requires'):
            await runtime.delegate_child_tool(runtime.tools[0], spec({}), scope)
        with pytest.raises(ValueError, match='scope requires'):
            await runtime.delegate_child_tool(runtime.tools[0], replace(spec(scope), tools=()), scope)
        await runtime.service.change('complete', summary='Parent closed the Goal')
        with pytest.raises(ValueError, match='completed/historical'):
            await runtime.delegate_child_tool(runtime.tools[0], spec(scope), scope)
    finally:
        await close(runtime, parent)
