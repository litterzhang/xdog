"""Main-Agent decisions, single current Goal and authoritative files across processes."""
from dataclasses import replace

import pytest
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Owner
from xdog.skills.goal.service import GoalService
from xdog.skills.goal.store import GoalStore

USER = OperationContext(True)


async def opened(tmp_path):
    owner = Owner('host:namespace:main', tmp_path)
    return GoalService(await GoalStore.open(tmp_path / 'data/registry.sqlite3', owner))


async def test_main_agent_plans_executes_and_completes_without_child_support(tmp_path):
    service = await opened(tmp_path)
    try:
        goal = await service.create('Outcome', 'Work in the real conversation', ('Check the result',), context=USER)
        await service.plan('# Plan\nI execute this myself; no child port is present.')
        task = await service.task({'id': 'one', 'title': 'Implement', 'expected_version': 0})
        path = service.files(goal).tasks_dir / 'one.json'
        assert path.is_file()
        task = await service.task({'id': 'one', 'expected_version': 1, 'status': 'completed',
                                   'summary': 'I checked the actual result', 'evidence': ['result.txt']})
        status = await service.status()
        assert status['tasks'][0]['status'] == 'completed' and task.version == 2
        assert '# Plan' in status['plan']
        completed = await service.change('complete', summary='Main Agent inspected result.txt')
        assert completed.status == 'completed' and await service.current() == completed
        assert await service.store.history(goal.id)
        new = await service.create('Next', 'New task', context=USER)
        assert new.id != goal.id and new.status == 'active'
        assert not (await service.status())['tasks']  # No previous task/completion inheritance.
        with pytest.raises(ValueError, match='Historical'):
            await service.store.put(replace(completed, version=completed.version + 1, status='active'),
                                    expected_version=completed.version)
    finally:
        await service.close()


@pytest.mark.parametrize('state,fields', [('pause', {}), ('wait', {'until': 'user'}), ('wait', {'until': 'tasks'})])
async def test_paused_and_waiting_goals_still_occupy_single_current_slot(tmp_path, state, fields):
    service = await opened(tmp_path)
    try:
        goal = await service.create('One', 'Only one current', context=USER)
        await service.change(state, context=USER, reason='Hold', **fields)
        with pytest.raises(ValueError, match='already'):
            await service.create('Two', 'Cannot overwrite', context=USER)
        assert (await service.current()).id == goal.id
        await service.change('clear', context=USER)
        assert await service.current() is None
        assert (await service.list())[0].status == 'cleared'
        assert service.files(goal).tasks_dir.is_dir()  # Clear archives, never removes work.
    finally:
        await service.close()


@pytest.mark.parametrize('action,fields', [('resume', {}), ('wait', {'until': 'tasks'}),
                                         ('complete', {'summary': 'Cannot bypass pause'}),
                                         ('update', {'outcome': 'Replacement'}), ('clear', {})])
async def test_automatic_tool_cannot_bypass_user_pause_or_change_contract(tmp_path, action, fields):
    service = await opened(tmp_path)
    try:
        await service.create('One', 'Original', context=USER)
        await service.change('pause', context=USER)
        with pytest.raises(ValueError, match='user operation'):
            await service.change(action, **fields)
        assert (await service.current()).status == 'paused'
    finally:
        await service.close()


async def test_only_task_wait_can_be_released_by_an_automatic_input(tmp_path):
    service = await opened(tmp_path)
    try:
        await service.create('One', 'Original', context=USER)
        for until in ['user', 'host']:
            await service.change('wait', until=until, reason='Needs actual input')
            with pytest.raises(ValueError):
                await service.change('resume')
            waiting = await service.current()
            await service.change('activate', waiting.id, version=waiting.version,
                                 reason='Actual user input', context=USER)
        waiting = await service.change('wait', until='tasks')
        assert (await service.change('activate', waiting.id, version=waiting.version,
                                     reason='Main verified the task result')).status == 'active'
    finally:
        await service.close()


async def test_external_worker_file_is_truth_not_a_database_copy(tmp_path):
    from xdog.skills.goal.codec import decode_task, encode_task
    service = await opened(tmp_path)
    try:
        goal = await service.create('One', 'Task files', context=USER)
        await service.task({'id': 'one', 'title': 'Worker work', 'expected_version': 0, 'worker': 'opaque-child-label'})
        path = service.files(goal).tasks_dir / 'one.json'
        original = decode_task(path.read_text())
        path.write_text(encode_task(replace(original, version=2, status='blocked', summary='Missing material')))
        assert (await service.status())['tasks'][0]['status'] == 'blocked'
        with pytest.raises(ValueError, match='version conflict'):
            await service.task({'id': 'one', 'expected_version': 1, 'status': 'completed'})
        assert (await service.status())['tasks'][0]['summary'] == 'Missing material'
        with pytest.raises(ValueError, match='version'):
            await service.change('update', version=0, outcome='Stale contract', context=USER)
    finally:
        await service.close()


async def test_goal_and_task_state_survive_actual_new_process(tmp_path):
    import subprocess
    import sys
    service = await opened(tmp_path)
    goal = await service.create('Persistent', 'New process', context=USER)
    await service.task({'id': 'one', 'title': 'Saved', 'expected_version': 0})
    await service.change('pause', context=USER)
    await service.close()
    code = '''
import asyncio,sys
from pathlib import Path
from xdog.skills.goal.models import Owner
from xdog.skills.goal.store import GoalStore
from xdog.skills.goal.service import GoalService
async def main():
 root=Path(sys.argv[1])
 s=GoalService(await GoalStore.open(root/'data/registry.sqlite3',Owner('host:namespace:main',root)))
 try:
  state=await s.status(); assert state['current']['status']=='paused'; assert state['tasks'][0]['id']=='one'
  print(state['current']['id'])
 finally: await s.close()
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == goal.id
