"""Action/field/type diagnostics fail closed before mutation, including nested paths."""
import pytest

from packages.skills.tests.goal.test_tools import content, setup


@pytest.mark.parametrize('args,code,action,fields', [
    ({}, 'missing_required', '', ['action']),
    ({'action': []}, 'type', '', ['action']),
    ({'action': 'launch_worker'}, 'unknown_action', 'launch_worker', ['action']),
    ({'action': 'update', 'version': 1, 'reason': 'not supported'}, 'unsupported_fields', 'update', ['reason']),
    ({'action': 'create', 'owner': 'forged'}, 'unsupported_fields', 'create', ['owner']),
    ({'action': 'create'}, 'missing_required', 'create', ['title']),
    ({'action': 'update', 'title': 'new'}, 'missing_required', 'update', ['version']),
    ({'action': 'create', 'title': 1}, 'type', 'create', ['title']),
    ({'action': 'update', 'version': True}, 'type', 'update', ['version']),
    ({'action': 'status', 'detail': 1}, 'type', 'status', ['detail']),
    ({'action': 'status', 'goal_id': None}, 'type', 'status', ['goal_id']),
    ({'action': 'wait'}, 'missing_required', 'wait', ['question', 'impact', 'resume_when']),
    ({'action': 'wait', 'until': 'user', 'reason': 'not a question'},
     'missing_required', 'wait', ['question', 'impact', 'resume_when']),
    ({'action': 'wait', 'question': {}, 'impact': 'blocked', 'resume_when': 'answer'}, 'type', 'wait', ['question']),
    ({'action': 'complete'}, 'missing_required', 'complete', ['summary']),
    ({'action': 'activate', 'version': 1}, 'missing_required', 'activate', ['goal_id', 'reason']),
    ({'action': 'task_update', 'task': []}, 'type', 'task_update', ['task']),
    ({'action': 'task_update', 'task': {'id': 't'}}, 'missing_required', 'task_update', ['task.expected_version']),
    ({'action': 'task_update', 'task': {'id': 't', 'expected_version': 0, 'authority': True}},
     'unsupported_fields', 'task_update', ['task.authority']),
    ({'action': 'task_update', 'task': {'id': 't', 'expected_version': False}},
     'type', 'task_update', ['task.expected_version']),
    ({'action': 'create', 'title': 'x', 'criteria': ['ok', 1]}, 'type', 'create', ['criteria[1]']),
    ({'action': 'report', 'report': {'summary': 'partial'}}, 'missing_required', 'report',
     ['report.expected_version', 'report.progress', 'report.completed', 'report.evidence',
      'report.blockers', 'report.next_steps']),
])
async def test_precise_errors_without_goal_or_task_side_effects(tmp_path, args, code, action, fields):
    service, tool = await setup(tmp_path)
    try:
        result = await tool.execute('bad', args)
        assert result.is_error
        data = content(result)
        assert data['code'] == code and data['action'] == action and data['fields'] == fields
        assert data['error'] and await service.current() is None
        assert not list(tmp_path.glob('.xdog/goals/*'))
    finally:
        await service.close()


async def test_update_reason_is_rejected_but_current_goal_id_remains_optional(tmp_path):
    service, tool = await setup(tmp_path)
    try:
        first = content(await tool.execute('create', {'action': 'create', 'title': 'Original'}))
        bad = await tool.execute('bad', {'action': 'update', 'version': first['version'],
                                         'title': 'Do not save', 'reason': 'illegal'})
        assert bad.is_error and 'reason is not accepted' in content(bad)['error']
        assert (await service.current()).title == 'Original'
        result = await tool.execute('good', {'action': 'update', 'version': first['version'], 'title': 'New'})
        assert not result.is_error and content(result)['id'] == first['id']
        assert content(result)['title'] == 'New'
        assert len(await service.store.history(first['id'])) == 2
    finally:
        await service.close()


@pytest.mark.parametrize('args,field', [
    ({'action': 'wait', 'until': 'tasks', 'question': 'Cannot apply'}, 'question'),
    ({'action': 'wait', 'until': 'host', 'resume_when': 'Not a host field'}, 'until'),
    ({'action': 'task_update', 'task': {'id': 't', 'expected_version': 0}}, 'task.title'),
])
async def test_conditional_fields_remain_fail_closed(tmp_path, args, field):
    service, tool = await setup(tmp_path)
    try:
        result = await tool.execute('conditional', args)
        assert result.is_error and content(result)['fields'] == [field]
        assert await service.current() is None
    finally:
        await service.close()
