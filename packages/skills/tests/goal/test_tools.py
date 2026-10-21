"""Model contributions are real AgentTools; no role/worker/review protocols remain."""
import json
from dataclasses import replace

import pytest
from xdog.ai.protocols.openai_responses import _convert_tools
from xdog.ai.types import Tool
from xdog.skills.goal.codec import decode_task, encode_task
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Owner
from xdog.skills.goal.service import GoalService
from xdog.skills.goal.store import GoalStore
from xdog.skills.goal.tools import create_goal_tool


def content(result):
    return json.loads(result.content[0].text)


async def setup(tmp_path):
    store = await GoalStore.open(tmp_path / 'data/registry.sqlite3', Owner('owner', tmp_path))
    service = GoalService(store)
    return service, create_goal_tool(service, lambda: OperationContext(True))


async def test_optional_action_schema_and_real_execution(tmp_path):
    service, tool = await setup(tmp_path)
    try:
        assert callable(tool.execute)
        wire = _convert_tools((Tool(name=tool.name, parameters=tool.parameters),))[0]
        assert wire['strict'] is False and wire['parameters']['required'] == ['action']
        for action, fields in [('create', {'title': 'Current context', 'outcome': 'Main knows'}),
                               ('plan', {'content': 'I will work on this myself'}),
                               ('task_update', {'task': {'id': 'one', 'title': 'Do it', 'expected_version': 0}}),
                               ('complete', {'summary': 'Main inspected actual evidence, with limitations'})]:
            result = await tool.execute('call', {'action': action, **fields})
            assert not result.is_error, content(result)
        assert await service.current() is None
        # Complete is a main-Agent decision, not an all-task-count/script verdict.
        assert (await service.list())[0].status == 'completed'
        actions = set(tool.parameters['properties']['action']['enum'])
        assert not actions & {'assign', 'request_complete', 'accept_task', 'cancel_review', 'reconcile'}
    finally:
        await service.close()


@pytest.mark.parametrize('extra', ['worker', 'owner', 'context', 'goal_id', 'summary'])
async def test_cross_action_and_forged_authority_fields_fail_closed(tmp_path, extra):
    service, tool = await setup(tmp_path)
    try:
        result = await tool.execute('call', {'action': 'create', 'title': 'x', extra: None})
        assert result.is_error and await service.current() is None
    finally:
        await service.close()


async def test_paged_read_rejects_task_file_changes_instead_of_mixing_snapshots(tmp_path):
    service, tool = await setup(tmp_path)
    try:
        goal = await service.create('Goal', 'Outcome', context=OperationContext(True))
        await service.task({'id': 'one', 'title': 'First', 'expected_version': 0})
        first = content(await tool.execute('call', {'action': 'status', 'limit': 20}))
        assert first['format'] == 'xdog.goal_data_page.v1' and first['next_query']
        path = service.files(goal).tasks_dir / 'one.json'
        task = decode_task(path.read_text())
        path.write_text(encode_task(replace(task, version=2, status='completed')))
        next_result = await tool.execute('call', first['next_query'])
        assert next_result.is_error and 'snapshot changed' in content(next_result)['error']
    finally:
        await service.close()


async def test_actual_storage_fault_is_not_swallowed_as_normal_tool_feedback(tmp_path, monkeypatch):
    service, tool = await setup(tmp_path)
    async def broken():
        raise OSError('real persistence fault')
    monkeypatch.setattr(service.store, 'current', broken)
    try:
        with pytest.raises(OSError, match='persistence'):
            await tool.execute('call', {'action': 'status'})
    finally:
        await service.close()
