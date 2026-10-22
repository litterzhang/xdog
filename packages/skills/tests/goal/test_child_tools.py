"""Restricted child Goal tools against actual registries/task files, not fake storage."""
import asyncio
import json
import threading
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from xdog.agent.tool_effects import ToolEffect
from xdog.ai.protocols.openai_responses import _convert_tools
from xdog.ai.types import Tool
from xdog.skills.goal.child_tools import child_tool_effect, create_child_goal_tool
from xdog.skills.goal.codec import decode_task, encode_task
from xdog.skills.goal.files import TaskFiles, _locked, directory
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Owner
from xdog.skills.goal.service import GoalService
from xdog.skills.goal.store import GoalStore

from packages.skills.tests.goal.legacy import wait_goal

USER = OperationContext(True)


def content(result):
    return json.loads(result.content[0].text)


def update(task_id='one', version=1, **changes):
    return {'action': 'task_update', 'task': {'id': task_id, 'expected_version': version, **changes}}


@pytest.fixture
async def scope(tmp_path):
    store = await GoalStore.open(tmp_path / 'data/registry.sqlite3', Owner('original-owner', tmp_path))
    service = GoalService(store)
    goal = await service.create('Main Goal', 'Original outcome', context=USER)
    await service.task({'id': 'one', 'title': 'Original title', 'worker': 'main',
                        'description': 'Original instructions', 'expected_version': 0})
    await service.task({'id': 'two', 'title': 'Private task', 'expected_version': 0})
    live = [True]
    tool = create_child_goal_tool(service, goal.id, ('one',), valid=lambda: live[0])
    try:
        yield service, goal, tool, live
    finally:
        await service.close()


async def test_real_file_results_preserve_ungranted_and_structural_facts(scope):
    service, goal, tool, _ = scope
    files = service.files(goal)
    private = (files.tasks_dir / 'two.json').read_bytes()
    result = await tool.execute('read', {'action': 'tasks', 'task_id': 'one'})
    assert not result.is_error
    observed = content(result)['tasks']
    assert len(observed) == 1 and observed[0]['id'] == 'one'
    result = await tool.execute('write', update(status='completed', summary='已验证', evidence=['test.log']))
    assert not result.is_error and content(result)['version'] == 2
    saved = decode_task((files.tasks_dir / 'one.json').read_text())
    assert (saved.status, saved.summary, saved.evidence) == ('completed', '已验证', ('test.log',))
    assert (saved.id, saved.title, saved.description, saved.worker, saved.depends_on) == (
        'one', 'Original title', 'Original instructions', 'main', ())
    assert (files.tasks_dir / 'two.json').read_bytes() == private
    assert await service.current() == goal
    assert not files.progress_path.exists() and not (files.root / 'plan.md').exists()


@pytest.mark.parametrize('args', [
    {'action': 'tasks'}, {'action': 'tasks', 'task_id': 'two'},
    {'action': 'tasks', 'task_id': '../one'}, {'action': 'tasks', 'task_id': 'one', 'goal_id': 'other'},
    {'action': 'tasks', 'task_id': 'one', 'worker': 'main'},
    {'action': 'tasks', 'task_id': 'one', 'status': 'pending'},
    {'action': 'tasks', 'task_id': 'one', 'task': {}},
    {'action': 'tasks', 'task_id': 'one', 'owner': 'original-owner'},
    {'action': 'tasks', 'task_id': 'one', 'valid': True},
    update('two', summary='cross task'), update('other', summary='new'),
    {**update(summary='cross goal'), 'goal_id': 'other'},
    {**update(summary='not read'), 'task_id': 'one'},
    {**update(summary='not paged'), 'limit': 10},
    update(version=0, summary='create'), update(version=-1, summary='bad'),
    update(version=True, summary='bad'), update(version=1.0, summary='bad'),
    update(version='1', summary='bad'), update(version=2**53, summary='bad'),
    {'action': 'task_update', 'task': {**update(summary='no')['task'], 'version': 2}},
    update(), update(status='unknown'), update(status=[]), update(summary=1),
    update(evidence='not an array'), update(evidence=[1]),
    *[update(**{field: value}) for field, value in (
        ('worker', 'child'), ('depends_on', ['two']), ('title', 'replaced'),
        ('description', 'replace instructions'), ('owner', 'forged'),
    )],
    {'action': 'task_update', 'task': {'id': 'one', 'summary': 'no CAS'}},
    {'action': 'task_update', 'task': None},
    *[{'action': action} for action in (
        'create', 'status', 'summary', 'history', 'plan', 'report', 'update',
        'wait', 'pause', 'resume', 'complete', 'clear', 'assign', 'worker',
    )],
])
async def test_executor_rejects_scope_lifecycle_extra_fields_and_bad_versions_without_schema_gate(scope, args):
    service, goal, tool, _ = scope
    before = {p.name: p.read_bytes() for p in service.files(goal).tasks_dir.glob('*.json')}
    result = await tool.execute('invalid', args)
    assert result.is_error, content(result)
    assert {p.name: p.read_bytes() for p in service.files(goal).tasks_dir.glob('*.json')} == before
    assert await service.current() == goal


async def test_cas_conflict_with_real_main_and_external_file_updates(scope):
    service, goal, tool, _ = scope
    await service.task({'id': 'one', 'expected_version': 1, 'summary': 'main wrote'})
    result = await tool.execute('stale', update(summary='overwrite main'))
    assert result.is_error and 'version conflict' in content(result)['error']
    path = service.files(goal).tasks_dir / 'one.json'
    path.write_text(encode_task(replace(decode_task(path.read_text()), version=3, evidence=('external',))))
    result = await tool.execute('external-stale', update(version=2, summary='overwrite CLI'))
    assert result.is_error and 'version conflict' in content(result)['error']
    results = await asyncio.gather(*[tool.execute(str(i), update(version=3, summary=str(i))) for i in range(2)])
    assert sum(not result.is_error for result in results) == 1
    assert decode_task(path.read_text()).version == 4


async def test_does_not_scan_or_parse_other_tasks(scope, monkeypatch):
    service, goal, tool, _ = scope
    (service.files(goal).tasks_dir / 'two.json').write_text('private malformed task')

    def forbidden(*args, **kwargs):
        raise AssertionError('Restricted calls must never scan other task files')

    monkeypatch.setattr(TaskFiles, 'list', forbidden)
    for args in ({'action': 'tasks', 'task_id': 'one'}, update(summary='single file only')):
        result = await tool.execute('single', args)
        assert not result.is_error, content(result)


async def test_deleted_task_cannot_be_recreated(scope):
    service, goal, tool, _ = scope
    path = service.files(goal).tasks_dir / 'one.json'
    path.unlink()
    for args in ({'action': 'tasks', 'task_id': 'one'}, update(summary='restore'), update(version=0, summary='new')):
        assert (await tool.execute('missing', args)).is_error
        assert not path.exists()


@pytest.mark.parametrize(('action', 'fields'), [
    ('pause', {'reason': 'user stopped Goal reminders'}),
    ('wait', {'until': 'user', 'reason': 'need user', 'question': 'Choose target?',
              'impact': 'Target unknown', 'resume_when': 'User selects target'}),
    ('wait', {'until': 'host', 'reason': 'need host'}),
    ('wait', {'until': 'tasks', 'reason': 'await original child'}),
])
async def test_original_child_results_do_not_release_pause_or_wait(scope, action, fields):
    service, goal, tool, _ = scope
    paused = (await wait_goal(service, goal.id, context=USER, **fields) if action == 'wait'
              else await service.change(action, goal.id, context=USER, **fields))
    assert not (await tool.execute('read', {'action': 'tasks', 'task_id': 'one'})).is_error
    result = await tool.execute('result', update(status='completed', summary='accepted child finished'))
    assert not result.is_error, content(result)
    assert await service.current() == paused


@pytest.mark.parametrize('action', ['complete', 'clear'])
async def test_terminal_or_replaced_goal_cannot_read_or_write_history(scope, action):
    service, goal, tool, _ = scope
    path = service.files(goal).tasks_dir / 'one.json'
    before = path.read_bytes()
    await service.change(action, goal.id, context=USER, **({'summary': 'main done'} if action == 'complete' else {}))
    for replaced in (False, True):
        if replaced:
            await service.create('New Goal', 'new authority', context=USER)
        for args in ({'action': 'tasks', 'task_id': 'one'}, update(summary='late historical result')):
            assert (await tool.execute('old', args)).is_error
            assert path.read_bytes() == before


async def test_child_and_service_retirement_are_both_checked(scope):
    service, _, tool, live = scope
    live[0] = False
    assert (await tool.execute('retired-child', update(summary='late'))).is_error
    live[0] = True
    service.valid = lambda: False
    assert (await tool.execute('retired-owner', {'action': 'tasks', 'task_id': 'one'})).is_error
    service.valid = lambda: True
    await service.close()
    assert (await tool.execute('closed', update(summary='late'))).is_error


async def test_rechecks_grant_and_current_goal_after_waiting_for_service_lock(scope):
    service, goal, tool, live = scope
    path = service.files(goal).tasks_dir / 'one.json'
    before = path.read_bytes()
    async with service._lock:
        pending = asyncio.create_task(tool.execute('queued', update(summary='stale grant')))
        await asyncio.sleep(0)
        live[0] = False
    assert (await pending).is_error
    assert path.read_bytes() == before
    live[0] = True
    async with service._lock:
        pending = asyncio.create_task(tool.execute('queued', update(summary='stale Goal')))
        await asyncio.sleep(0)
        # A real transition sharing the owner lock wins before the pending call.
        await service.store.put(replace(goal, status='completed', version=goal.version + 1),
                                expected_version=goal.version)
    assert (await pending).is_error
    assert path.read_bytes() == before


async def test_nested_original_invocation_cannot_be_bypassed(scope):
    service, _, tool, _ = scope
    live = [True]
    with service.invocation(lambda: live[0]):
        live[0] = False
        assert (await tool.execute('nested', update(summary='cannot widen original grant'))).is_error


async def test_rechecks_valid_after_real_taskfiles_os_lock_wait_without_blocking_loop(scope, monkeypatch):
    service, goal, tool, live = scope
    files = service.files(goal)
    path = files.tasks_dir / 'one.json'
    before = path.read_bytes()
    waiting = threading.Event()
    import xdog.skills.goal.child_tools as module

    original = module._locked

    def observed(*args, **kwargs):
        waiting.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(module, '_locked', observed)
    with directory(files.root) as root, _locked(root, '.task-write.lock'):
        pending = asyncio.create_task(tool.execute('OS-lock', update(summary='revoked while waiting')))
        async with asyncio.timeout(5):
            while not waiting.is_set():
                await asyncio.sleep(0.001)
        # Reaching here proves the real blocking flock is not on the owner loop.
        live[0] = False
    assert (await pending).is_error
    assert path.read_bytes() == before


async def test_cancellation_before_admission_and_after_owner_lock_wait(scope):
    service, goal, tool, _ = scope
    path = service.files(goal).tasks_dir / 'one.json'
    before = path.read_bytes()
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await tool.execute('cancelled', update(summary='no'), cancel)
    cancel.clear()
    async with service._lock:
        pending = asyncio.create_task(tool.execute('queued-cancel', update(summary='no'), cancel))
        await asyncio.sleep(0)
        cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert path.read_bytes() == before


@pytest.mark.parametrize('event_cancel', [False, True])
async def test_cancellation_during_owned_io_settles_worker_and_never_publishes_success(
    scope, monkeypatch, event_cancel,
):
    service, goal, tool, _ = scope
    path = service.files(goal).tasks_dir / 'one.json'
    before = path.read_bytes()
    entered, release = threading.Event(), threading.Event()
    import xdog.skills.goal.child_tools as module

    original = module._read

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, '_read', blocked)
    cancel = asyncio.Event()
    pending = asyncio.create_task(tool.execute('cancel-IO', update(summary='no success'), cancel))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        if event_cancel:
            cancel.set()
        else:
            pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    if event_cancel:
        assert path.read_bytes() == before
    # Actual task cancellation can arrive after an admitted atomic effect. It
    # must settle the worker before releasing the owner lock, never claim success.
    assert not service._lock.locked()


@pytest.mark.parametrize('operation', ['current', 'read', 'write'])
async def test_real_storage_errors_propagate(scope, monkeypatch, operation):
    service, _, tool, _ = scope

    def broken(*args, **kwargs):
        raise OSError('actual disk fault')

    async def broken_current():
        raise OSError('actual registry fault')

    if operation == 'current':
        monkeypatch.setattr(service.store, 'current', broken_current)
    else:
        monkeypatch.setattr('xdog.skills.goal.child_tools._' + operation, broken)
    with pytest.raises(OSError, match='actual'):
        await tool.execute('fault', update(summary='not successful'))


async def test_schema_scope_action_validation_and_real_openai_wire_conversion(scope):
    _, goal, tool, _ = scope
    Draft202012Validator.check_schema(tool.parameters)
    validator = Draft202012Validator(tool.parameters)
    wire, = _convert_tools((Tool(name=tool.name, parameters=tool.parameters),))
    assert tool.name == wire['name'] == 'goal'
    assert wire['strict'] is False
    assert wire['parameters'] == tool.parameters
    for args in ({'action': 'tasks', 'task_id': 'one'},
                 {'action': 'tasks', 'task_id': 'one', 'goal_id': goal.id, 'limit': 10},
                 update(summary='result'), update(status='completed', evidence=['test'])):
        assert validator.is_valid(args), args
    for args in ({'action': 'tasks'}, {'action': 'tasks', 'task_id': 'two'},
                 {'action': 'tasks', 'task_id': 'one', 'goal_id': 'other'},
                 {'action': 'tasks', 'task_id': 'one', 'task': {}},
                 {'action': 'report'}, {'action': 'complete'}, update('two', summary='no'),
                 update(version=0, summary='create'), update(version=True, summary='bad'),
                 update(worker='child'), update(title='new'), update(description='new'), update(),
                 {**update(summary='no'), 'task_id': 'one'},
                 {**update(summary='no'), 'limit': 10}):
        assert not validator.is_valid(args), args


async def test_paged_single_task_read_is_scope_pinned_and_rejects_cas_changes(scope):
    _, _, tool, _ = scope
    args = {'action': 'tasks', 'task_id': 'one', 'limit': 16}
    first = content(await tool.execute('first', args))
    assert first['format'] == 'xdog.goal_data_page.v1'
    query = first['next_query']
    assert query['task_id'] == 'one'
    assert not (await tool.execute('next', query)).is_error
    assert (await tool.execute('switch', {**query, 'task_id': 'two'})).is_error
    assert not (await tool.execute('change', update(summary='new snapshot'))).is_error
    result = await tool.execute('stale-page', query)
    assert result.is_error and 'snapshot changed' in content(result)['error']


async def test_tool_cannot_run_on_another_loop(scope):
    _, _, tool, _ = scope

    def other_loop():
        return asyncio.run(tool.execute('wrong-loop', update(summary='wrong loop')))

    result = await asyncio.to_thread(other_loop)
    assert result.is_error and 'original owner loop' in content(result)['error']


@pytest.mark.parametrize(('args', 'expected'), [
    ({'action': 'tasks', 'task_id': 'one'}, ToolEffect('read', 'tool_data')),
    (update(summary='result'), ToolEffect('write', 'tool_data')),
    ({'action': 'tasks'}, None), ({'action': 'report'}, None), ({'action': 'complete'}, None),
    (update(version=0, summary='create'), None), (update(worker='new'), None),
    ({'action': 'tasks', 'task_id': 'one', 'offset': True}, None),
    ({'action': 'tasks', 'task_id': 'one', 'offset': 1}, None),
    ({'action': 'tasks', 'task_id': 'one', 'owner': 'forged'}, None),
])
def test_effect_classification_is_only_pure_shape_data(args, expected):
    assert child_tool_effect(args) == expected


async def test_shares_cas_and_lock_with_actual_taskfiles_writer(scope):
    service, goal, tool, _ = scope
    files = service.files(goal)
    path = files.tasks_dir / 'one.json'
    original = decode_task(path.read_text())
    external, child = await asyncio.gather(
        asyncio.to_thread(files.write, replace(original, summary='external writer', version=2), expected_version=1),
        tool.execute('racing-child', update(summary='child writer')),
        return_exceptions=True,
    )
    assert not isinstance(child, BaseException)
    if isinstance(external, BaseException):
        assert isinstance(external, ValueError) and 'version conflict' in str(external)
        assert not child.is_error
    else:
        assert child.is_error and 'version conflict' in content(child)['error']
    assert decode_task(path.read_text()).version == 2


async def test_owner_binding_is_not_just_the_current_goal_id(scope, monkeypatch):
    service, goal, tool, _ = scope
    with monkeypatch.context() as changed:
        changed.setattr(service.store, 'owner', Owner('successor-owner', service.store.owner.workspace))
        result = await tool.execute('wrong-owner', {'action': 'tasks', 'task_id': 'one', 'goal_id': goal.id})
        assert result.is_error


async def test_filename_identity_mismatch_fails_instead_of_reading_or_overwriting_other_task(scope):
    service, goal, tool, _ = scope
    path = service.files(goal).tasks_dir / 'one.json'
    foreign = encode_task(replace(decode_task(path.read_text()), id='two'))
    path.write_text(foreign)
    for args in ({'action': 'tasks', 'task_id': 'one'}, update(summary='no overwrite')):
        result = await tool.execute('foreign-file', args)
        assert result.is_error and 'identity disagree' in content(result)['error']
        assert path.read_text() == foreign


@pytest.mark.parametrize('task_ids', [(), ('one', 'one'), ('../one',), ('one', 1), ['one']])
async def test_factory_requires_explicit_valid_task_scope(scope, task_ids):
    service, goal, _, _ = scope
    with pytest.raises(ValueError):
        create_child_goal_tool(service, goal.id, task_ids, valid=lambda: True)


async def test_grant_can_include_multiple_explicit_tasks_but_never_list_them(scope):
    service, goal, _, _ = scope
    tool = create_child_goal_tool(service, goal.id, ('one', 'two'), valid=lambda: True)
    assert (await tool.execute('list', {'action': 'tasks'})).is_error
    for identifier in ('one', 'two'):
        result = await tool.execute('single', {'action': 'tasks', 'task_id': identifier})
        assert not result.is_error
        assert [row['id'] for row in content(result)['tasks']] == [identifier]
