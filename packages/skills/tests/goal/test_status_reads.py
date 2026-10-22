"""On-demand Goal facts, exact snapshot reads and main-Agent progress records."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
from xdog.skills.goal.codec import decode_task, encode_task
from xdog.skills.goal.files import TaskFiles
from xdog.skills.goal.host import OperationContext, OwnerRetired
from xdog.skills.goal.models import Owner
from xdog.skills.goal.service import GoalService
from xdog.skills.goal.store import GoalStore
from xdog.skills.goal.tools import create_goal_tool
from xdog.skills.goal.ui_views import GoalView

from packages.skills.tests.goal.legacy import wait_goal

USER = OperationContext(True)


@pytest.fixture
async def service(tmp_path):
    store = await GoalStore.open(tmp_path / 'data/registry.sqlite3', Owner('status-owner', tmp_path))
    value = GoalService(store)
    try:
        yield value
    finally:
        await value.close()


def unpack(result):
    value = json.loads(result.content[0].text)
    assert not result.is_error, value
    return value


def fail(*args, **kwargs):
    raise AssertionError('Unrequested IO')


def report_data(expected=0, progress='baseline', **fields):
    return {'expected_version': expected, 'progress': progress, 'completed': [], 'evidence': [],
            'blockers': [], 'next_steps': ['Inspect the actual workspace'],
            'summary': 'Main Agent assessment', **fields}


async def create(service):
    return await service.create('Target', 'Actual outcome', ('Check evidence',), context=USER)


async def task(service, identifier='one', **fields):
    return await service.task({'id': identifier, 'title': identifier, 'expected_version': 0, **fields})


async def read_pages(tool, query):
    pieces, first = [], None
    while query is not None:
        frame = unpack(await tool.execute('read', query))
        if first is None:
            first = frame
        assert frame['sha256'] == first['sha256']
        assert frame['selector']['revision'] == first['selector']['revision']
        pieces.append(frame['text'])
        query = frame['next_query']
    payload = ''.join(pieces)
    assert hashlib.sha256(payload.encode()).hexdigest() == first['sha256']
    assert len(payload.encode()) == first['total_bytes']
    return json.loads(payload), first


async def test_summary_keeps_legacy_status_and_never_reads_plan(service, monkeypatch):
    goal = await create(service)
    await service.plan('# Full plan')
    for index in range(18):
        await task(service, f't{index:02}', description='Do not include this task body',
                   status='completed' if index == 0 else 'pending')
    legacy = await service.status()
    assert legacy['plan'] == '# Full plan' and len(legacy['tasks']) == 18
    assert legacy == await service.status(detail=True)
    monkeypatch.setattr(TaskFiles, 'plan', fail)
    summary = await service.summary()
    assert summary == await service.status(detail=False)
    assert 'tasks' not in summary and 'plan' not in summary
    assert 'Do not include this task body' not in json.dumps(summary)
    assert summary['task_count'] == 18 and summary['task_counts']['completed'] == 1
    assert sum(summary['task_counts'].values()) == 18
    assert len(summary['tasks_preview']) == 16 and summary['tasks_preview_only']
    assert summary['current']['status'] == 'active'  # Counts never infer Goal completion.
    assert summary['report_path'] == str(service.files(goal).progress_path)
    assert summary['latest_report'] is None


async def test_no_goal_summary_does_not_touch_files(service, monkeypatch):
    monkeypatch.setattr(TaskFiles, 'list', fail)
    monkeypatch.setattr(TaskFiles, 'plan', fail)
    monkeypatch.setattr(TaskFiles, 'latest_report', fail)
    assert (await service.summary())['current'] is None
    tool = create_goal_tool(service, lambda: USER)
    assert unpack(await tool.execute('read', {'action': 'status'}))['current'] is None


async def test_model_status_defaults_to_summary_and_explicit_detail_is_compatible(service, monkeypatch):
    await create(service)
    await task(service, description='Full body')
    await service.plan('Plan body')
    tool = create_goal_tool(service, lambda: USER)
    legacy = unpack(await tool.execute('read', {'action': 'status', 'detail': True}))
    assert legacy['plan'] == 'Plan body' and legacy['tasks'][0]['description'] == 'Full body'
    monkeypatch.setattr(TaskFiles, 'plan', fail)
    summary = unpack(await tool.execute('read', {'action': 'status'}))
    assert summary == unpack(await tool.execute('read', {'action': 'summary'}))
    assert summary['task_count'] == 1 and 'tasks' not in summary and 'plan' not in summary


async def test_single_task_reads_only_selected_file_and_checks_identity(service, monkeypatch):
    goal = await create(service)
    await task(service, 'selected')
    # An unrelated invalid task must not break an explicit single-file read.
    (service.files(goal).tasks_dir / 'unrelated.json').write_text('{broken')
    monkeypatch.setattr(TaskFiles, 'list', fail)
    monkeypatch.setattr(TaskFiles, 'plan', fail)
    monkeypatch.setattr(TaskFiles, 'latest_report', fail)
    selected = await service.tasks(task_id='selected')
    assert selected['tasks'][0]['id'] == 'selected' and len(selected['tasks']) == 1
    with pytest.raises(ValueError, match='Unknown task'):
        await service.tasks(task_id='missing')
    path = service.files(goal).tasks_dir / 'selected.json'
    original = decode_task(path.read_text())
    path.write_text(encode_task(replace(original, id='other')))
    with pytest.raises(ValueError, match='identity disagree'):
        await service.tasks(task_id='selected')


async def test_task_filters_and_unicode_pages_preserve_exact_selection(service, monkeypatch):
    await create(service)
    await task(service, 'a', status='blocked', worker='child-label', description='证据🐶' * 200)
    await task(service, 'b', status='blocked', worker='main')
    await task(service, 'c', status='completed', worker='child-label')
    monkeypatch.setattr(TaskFiles, 'plan', fail)
    monkeypatch.setattr(TaskFiles, 'latest_report', fail)
    tool = create_goal_tool(service, lambda: USER)
    query = {'action': 'tasks', 'status': 'blocked', 'worker': 'child-label', 'limit': 512}
    value, first = await read_pages(tool, query)
    assert [row['id'] for row in value['tasks']] == ['a']
    assert value['tasks'][0]['description'] == '证据🐶' * 200
    assert first['next_query']['status'] == 'blocked'
    assert first['next_query']['worker'] == 'child-label'
    assert (await service.tasks(task_id='a', worker='main'))['tasks'] == []
    assert (await service.tasks(status='cancelled'))['tasks'] == []


async def test_large_tasks_auto_page_without_truncation(service):
    await create(service)
    for index in range(5):
        await task(service, f't{index}', description='x' * 60000)
    value, first = await read_pages(create_goal_tool(service, lambda: USER), {'action': 'tasks'})
    assert first['total_bytes'] > 256 * 1024
    assert len(value['tasks']) == 5 and all(len(row['description']) == 60000 for row in value['tasks'])


@pytest.mark.parametrize('change', ['body_without_version', 'filter_membership'])
async def test_task_pages_reject_external_file_edits_not_just_goal_versions(service, change):
    goal = await create(service)
    await task(service, status='blocked', description='original')
    tool = create_goal_tool(service, lambda: USER)
    first = unpack(await tool.execute('read', {'action': 'tasks', 'status': 'blocked', 'limit': 40}))
    path = service.files(goal).tasks_dir / 'one.json'
    original = decode_task(path.read_text())
    updated = replace(original, description='external edit') if change == 'body_without_version' else replace(
        original, status='completed')
    path.write_text(encode_task(updated))
    result = await tool.execute('read', first['next_query'])
    assert result.is_error and 'snapshot changed' in json.loads(result.content[0].text)['error']
    assert (await service.current()).version == goal.version


async def test_snapshot_hash_also_binds_selection_even_if_results_match(service):
    await create(service)
    tool = create_goal_tool(service, lambda: USER)
    first = unpack(await tool.execute('read', {'action': 'tasks', 'status': 'blocked', 'limit': 10}))
    changed = {**first['next_query'], 'status': 'completed'}
    result = await tool.execute('read', changed)
    assert result.is_error and 'snapshot changed' in json.loads(result.content[0].text)['error']


async def test_plan_is_independently_readable_and_paged_without_task_scan(service, monkeypatch):
    goal = await create(service)
    await service.plan('# Plan\n' + '明确的步骤' * 100)
    monkeypatch.setattr(TaskFiles, 'list', fail)
    monkeypatch.setattr(TaskFiles, 'latest_report', fail)
    tool = create_goal_tool(service, lambda: USER)
    value, first = await read_pages(tool, {'action': 'plan', 'limit': 512})
    assert value == await service.read_plan()
    assert 'content' not in first['next_query']
    (service.files(goal).root / 'plan.md').write_text('Externally changed')
    result = await tool.execute('read', first['next_query'])
    assert result.is_error and 'snapshot changed' in json.loads(result.content[0].text)['error']


async def test_detailed_status_pages_keep_detail_flag(service):
    await create(service)
    await task(service)
    await service.plan('Detailed plan')
    value, first = await read_pages(create_goal_tool(service, lambda: USER),
                                    {'action': 'status', 'detail': True, 'limit': 512})
    assert value['plan'] == 'Detailed plan' and value['tasks'][0]['id'] == 'one'
    assert first['next_query']['detail'] is True


@pytest.mark.parametrize('fields', [
    {'offset': 1}, {'offset': True}, {'limit': True}, {'limit': 0}, {'limit': 65537},
    {'revision': 'invalid'}, {'offset': -1},
])
async def test_bad_page_queries_fail_before_file_io(service, monkeypatch, fields):
    await create(service)
    monkeypatch.setattr(TaskFiles, 'list', fail)
    result = await create_goal_tool(service, lambda: USER).execute('read', {'action': 'tasks', **fields})
    assert result.is_error


@pytest.mark.parametrize('fields', [
    {'task_id': '../outside'}, {'task_id': None}, {'status': 'invented'}, {'status': []}, {'worker': False},
])
async def test_task_read_filters_are_strict(service, fields):
    await create(service)
    with pytest.raises(ValueError):
        await service.tasks(**fields)


async def test_plan_write_cannot_use_read_page_fields_or_commit_on_invalid_query(service):
    await create(service)
    tool = create_goal_tool(service, lambda: USER)
    result = await tool.execute('write', {'action': 'plan', 'content': 'Must not commit', 'limit': 10})
    assert result.is_error and (await service.read_plan())['plan'] == ''


async def test_report_tool_schema_and_compact_record_with_latest_summary(service, monkeypatch):
    goal = await create(service)
    changes = []
    service.on_change = lambda: changes.append('changed')
    tool = create_goal_tool(service, lambda: OperationContext(False))
    schema = tool.parameters['properties']['report']
    assert set(schema['required']) == set(report_data())
    assert not schema['additionalProperties']
    assert schema['properties']['expected_version']['type'] == 'integer'
    assert schema['properties']['progress']['enum'] == ['baseline', 'advanced', 'unchanged', 'regressed']
    first = unpack(await tool.execute('report', {'action': 'report', 'report': report_data()}))
    assert first['version'] == 1 and 'expected_version' not in first
    assert changes == ['changed'] and service.files(goal).progress_path.is_file()
    # No completion/evidence heuristic: this is the Agent's assessment, not a verdict.
    second = unpack(await tool.execute('report', {'action': 'report', 'report': report_data(
        1, 'advanced', summary='Agent judged progress with no completed task', completed=[], evidence=[])}))
    assert second['version'] == 2 and 'history' not in second
    monkeypatch.setattr(TaskFiles, 'plan', fail)
    summary = await service.summary()
    assert summary['latest_report'] == second and summary['current']['status'] == 'active'
    assert summary['report_path'] == str(service.files(goal).progress_path)
    stale = await tool.execute('report', {'action': 'report', 'report': report_data(1, 'unchanged')})
    assert stale.is_error and changes == ['changed', 'changed']


@pytest.mark.parametrize('fields', [
    {'expected_version': True}, {'progress': 'probably'}, {'completed': 'not an array'},
    {'evidence': [1]}, {'blockers': None}, {'next_steps': {}}, {'summary': 1}, {'extra': 'forged'},
])
async def test_report_validation_prevents_mutation(service, monkeypatch, fields):
    await create(service)
    monkeypatch.setattr(TaskFiles, 'report', fail)
    result = await create_goal_tool(service, lambda: USER).execute(
        'report', {'action': 'report', 'report': report_data(**fields)})
    assert result.is_error


@pytest.mark.parametrize('until', ['user', 'host', 'tasks'])
async def test_reporting_a_wait_does_not_release_or_rewrite_it(service, until):
    await create(service)
    await wait_goal(service, until=until, reason='Original wait',
            **({'question': 'Which delivery target?', 'impact': 'Final target unknown',
                'resume_when': 'User selects target'} if until == 'user' else {}))
    before = await service.current()
    result = await service.report(report_data())
    assert result['version'] == 1 and await service.current() == before


async def test_report_cannot_mutate_paused_historical_or_replaced_goal(service):
    goal = await create(service)
    await service.change('pause', context=USER)
    with pytest.raises(ValueError, match='paused'):
        await service.report(report_data())
    await service.change('clear', context=USER)
    with pytest.raises(ValueError, match='current Goal'):
        await service.report(report_data(), goal.id)
    await create(service)
    with pytest.raises(ValueError, match='current Goal'):
        await service.report(report_data(), goal.id)
    assert service.files(goal).latest_report() is None


async def test_retired_report_invocation_is_fenced_before_owned_worker_write(service, monkeypatch):
    from xdog.skills.goal import service as service_module
    goal = await create(service)
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = service_module.run_owned
    valid = True

    async def work(operation, *args, **kwargs):
        if getattr(operation, '__name__', '') == 'report':
            entered.set()
            await proceed.wait()
        return await original(operation, *args, **kwargs)

    async def report():
        with service.invocation(lambda: valid):
            return await service.report(report_data())

    monkeypatch.setattr(service_module, 'run_owned', work)
    pending = asyncio.create_task(report())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        valid = False
        proceed.set()
        with pytest.raises(OwnerRetired):
            await pending
        assert service.files(goal).latest_report() is None
    finally:
        proceed.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize('wait', ['', 'tasks'])
async def test_main_agent_can_self_pause_but_never_automatically_resume(service, wait):
    await create(service)
    if wait:
        await wait_goal(service, until=wait)
    paused = await service.change('pause', reason='Main Agent reports no progress')
    assert paused.status == 'paused' and paused.reason == 'Main Agent reports no progress'
    for action, fields in [('resume', {}), ('pause', {'reason': 'Overwrite'}),
                           ('wait', {'until': 'tasks'}), ('update', {'title': 'Changed'}), ('clear', {})]:
        with pytest.raises(ValueError, match='user operation'):
            await service.change(action, **fields)
    assert await service.current() == paused
    assert (await service.change('resume', context=USER)).status == 'active'


@pytest.mark.parametrize('until', ['user', 'host'])
async def test_self_pause_cannot_overwrite_protected_wait(service, until):
    await create(service)
    waiting = await wait_goal(service, until=until, reason='Explicit original hold',
            **({'question': 'Which delivery target?', 'impact': 'Final target unknown',
                'resume_when': 'User selects target'} if until == 'user' else {}))
    with pytest.raises(ValueError, match='user operation'):
        await service.change('pause', reason='Automatic override')
    with pytest.raises(ValueError, match='user operation'):
        await wait_goal(service, until='tasks')
    assert await service.current() == waiting


async def test_view_prepares_wait_counts_and_recorded_report_facts_without_plan(service, monkeypatch):
    await create(service)
    await task(service, status='blocked')
    await service.report(report_data(summary='Summary ' * 40, blockers=['Needs actual input']))
    await wait_goal(service, until='tasks', reason='Wait ' * 50)
    monkeypatch.setattr(TaskFiles, 'plan', fail)
    row = (await GoalView(service).read({}))['items'][0]
    assert row['status'] == 'waiting' and row['wait_for'] == 'tasks'
    assert len(row['reason']) == 160 and 'reason' in row['preview_fields']
    assert row['tasks']['total'] == 1 and row['tasks']['completed'] == 0
    assert row['tasks']['counts']['blocked'] == 1
    assert row['latest_report']['progress'] == 'baseline'
    assert len(row['latest_report']['summary']) == 160
    assert row['latest_report']['preview_fields'] == ['summary']
    service.valid = lambda: False
    with pytest.raises(OwnerRetired):
        await GoalView(service).read({})


async def test_report_returns_one_record_without_reading_unrelated_data(service, monkeypatch):
    await create(service)
    monkeypatch.setattr(TaskFiles, 'list', fail)
    monkeypatch.setattr(TaskFiles, 'plan', fail)
    monkeypatch.setattr(TaskFiles, 'latest_report', fail)
    result = unpack(await create_goal_tool(service, lambda: OperationContext(False)).execute(
        'report', {'action': 'report', 'report': report_data()}))
    assert set(result) == (set(report_data()) - {'expected_version'}) | {'version'}


async def test_report_storage_failure_propagates_without_notification(service, monkeypatch):
    await create(service)
    changes = []
    service.on_change = lambda: changes.append('changed')

    def broken(*args, **kwargs):
        raise OSError('actual report storage failure')

    monkeypatch.setattr(TaskFiles, 'report', broken)
    with pytest.raises(OSError, match='report storage failure'):
        await create_goal_tool(service, lambda: USER).execute('report', {'action': 'report', 'report': report_data()})
    assert not changes


@pytest.mark.parametrize('execution,delivery,action,source,requires_user', [
    ({'state': 'idle'}, {'status': 'idle'}, 'main_agent_work', 'goal', False),
    ({'state': 'running'}, {'status': 'idle'}, 'await_execution', 'execution', False),
    ({'state': 'unavailable'}, {'status': 'idle'}, 'await_owner_available', 'execution', False),
    ({'state': 'idle', 'user_pending': True}, {'status': 'idle'}, 'await_user_turn', 'execution', False),
    ({'state': 'idle', 'children_active': True}, {'status': 'idle'}, 'await_children', 'execution', False),
    ({'state': 'idle'}, {'status': 'unknown'}, 'inspect_delivery', 'delivery', True),
    ({'state': 'idle'}, {'status': 'unavailable'}, 'restore_delivery', 'delivery', False),
    ({'state': 'idle'}, {'status': 'deferred'}, 'await_owner_available', 'delivery', False),
])
async def test_summary_preserves_independent_local_host_facts_and_next_display(
        service, monkeypatch, execution, delivery, action, source, requires_user):
    await create(service)
    info = {'automation': {'status': 'watching'}, 'delivery': delivery,
            'execution': execution, 'legacy_storage': ['/untouched/old/journal.sqlite3']}
    snapshots = []

    def snapshot():
        snapshots.append('local')
        return info

    service.info = snapshot
    service.on_change = fail
    monkeypatch.setattr(TaskFiles, 'plan', fail)
    summary = await service.summary()
    assert snapshots == ['local']
    assert all(summary[key] == value for key, value in info.items())
    assert summary['next']['action'] == action and summary['next']['source'] == source
    assert summary['next']['requires_user'] is requires_user
    assert summary['current']['status'] == 'active'
    view = (await GoalView(service).read({}))['items'][0]
    assert view['automation_status'] == 'watching'
    assert view['delivery_status'] == delivery['status'] and view['execution_state'] == execution['state']
    assert view['next']['action'] == action


@pytest.mark.parametrize('state,until,action', [
    ('pause', '', 'user_resume'), ('wait', 'user', 'user_input'), ('wait', 'host', 'host_recovery'),
])
async def test_protected_state_next_facts_take_precedence_over_busy_or_delivery(service, state, until, action):
    await create(service)
    operation = wait_goal if state == 'wait' else lambda svc, **kw: svc.change(state, **kw)
    await operation(service, context=USER, reason='Explicit protected state',
        **({'until': until, **({'question': 'Choose target?', 'impact': 'Target unknown',
             'resume_when': 'User selects target'} if until == 'user' else {})} if until else {}))
    service.info = lambda: {'execution': {'state': 'running'}, 'delivery': {'status': 'unknown'}}
    summary = await service.summary()
    assert summary['next'] == {'action': action, 'owner': 'host' if until == 'host' else 'user', 'source': 'goal',
                               'reason': 'Explicit protected state', 'requires_user': True}


async def test_next_task_wait_and_empty_goal_are_only_display_guidance(service):
    assert (await service.summary())['next']['action'] == 'create'
    await create(service)
    await wait_goal(service, until='tasks', reason='Inspect actual task evidence')
    waiting = await service.current()
    summary = await service.summary()
    assert summary['next'] == {'action': 'inspect_tasks', 'owner': 'task_workers', 'source': 'goal',
                               'reason': 'Inspect actual task evidence', 'requires_user': False}
    assert await service.current() == waiting  # Reading status must not resume or enqueue anything.


@pytest.mark.parametrize('action,until', [('wait', 'user'), ('wait', 'host'), ('pause', ''), ('complete', '')])
async def test_disabled_reminders_show_idle_not_stale_deferred_and_retain_last_check(service, action, until):
    from copy import deepcopy

    goal = await create(service)
    delivery = {'status': 'deferred', 'message_id': 'original-not-sent', 'reason': 'Owner busy'}
    info = {'delivery': delivery, 'execution': {'state': 'idle'}, 'automation': {'status': 'idle'}}
    before_info = deepcopy(info)
    service.info = lambda: info
    fields = ({'until': until, **({'question': 'Choose target?', 'impact': 'Target unknown',
                                  'resume_when': 'User selects target'} if until == 'user' else {})} if until
              else {'summary': 'Verified completion'} if action == 'complete' else {})
    if action == 'wait':
        await wait_goal(service, context=USER, **fields)
    else:
        await service.change(action, context=USER, **fields)
    before = await service.current()
    summary = await service.summary()
    assert summary['delivery']['status'] == 'idle'
    assert 'automatic reminders disabled' in summary['delivery']['reason']
    assert summary['last_delivery'] == delivery
    assert info == before_info  # A status read never mutates the runtime's diagnostic or outbox.
    assert await service.current() == before
    view = (await GoalView(service).read({'goal_id': goal.id}))['items'][0]
    assert view['delivery_status'] == 'idle'
    assert (await service.status())['last_delivery'] == delivery


@pytest.mark.parametrize('action', [None, 'clear'])
async def test_no_current_goal_cannot_advertise_a_deferred_reminder(service, action):
    if action:
        await create(service)
        await service.change(action, context=USER)
    service.info = lambda: {'delivery': {'status': 'deferred', 'reason': 'Old check'}}
    result = await service.summary()
    assert result['current'] is None
    assert result['delivery'] == {'status': 'idle',
        'reason': 'No current Goal; automatic reminders disabled'}
    assert result['last_delivery']['status'] == 'deferred'


@pytest.mark.parametrize('status', ['accepted', 'pending', 'running', 'succeeded', 'unknown', 'unavailable'])
async def test_waiting_state_does_not_hide_original_transport_outcomes(service, status):
    await create(service)
    await wait_goal(service, until='user', reason='Actual business decision', context=USER,
            question='Which delivery target should be used?', impact='The final target cannot be selected',
            resume_when='User selects the delivery target')
    delivery = {'status': status, 'message_id': 'original', 'error': 'Keep exact receipt diagnostics'}
    service.info = lambda: {'delivery': delivery}
    result = await service.summary()
    assert result['delivery'] == delivery and 'last_delivery' not in result
    assert result['next']['action'] == 'user_input'


@pytest.mark.parametrize('action', ['status', 'summary'])
@pytest.mark.parametrize('until', ['', 'tasks'])
async def test_model_status_does_not_tell_the_executing_main_agent_to_wait_for_itself(service, action, until):
    await create(service)
    if until:
        await wait_goal(service, until=until, reason='Inspect returned evidence',
            **({'question': 'Which delivery target?', 'impact': 'Final target unknown',
                'resume_when': 'User selects target'} if until == 'user' else {}))
    info = {'execution': {'state': 'running', 'user_pending': True, 'children_active': True},
            'delivery': {'status': 'deferred', 'reason': 'Owner busy'}}
    service.info = lambda: info
    before = await service.current()
    observer = await service.summary()
    assert observer['next']['action'] == 'await_execution'
    tool = create_goal_tool(service, lambda: USER)
    result = unpack(await tool.execute('current-main', {'action': action}))
    assert result['execution'] == observer['execution']
    assert result['delivery'] == observer['delivery']
    assert result['next']['action'] == ('inspect_tasks' if until else 'main_agent_work')
    assert not result['next']['requires_user']
    assert await service.current() == before  # No activation, permission grant or new execution.


@pytest.mark.parametrize('action,until,expected', [
    ('pause', '', 'user_resume'), ('wait', 'user', 'user_input'), ('wait', 'host', 'host_recovery'),
    ('complete', '', 'create'),
])
async def test_model_audience_cannot_release_lifecycle_protection(service, action, until, expected):
    await create(service)
    fields = ({'until': until, **({'question': 'Choose target?', 'impact': 'Target unknown',
                                  'resume_when': 'User selects target'} if until == 'user' else {})} if until
              else {'summary': 'Verified completion'} if action == 'complete' else {})
    if action == 'wait':
        await wait_goal(service, context=USER, **fields)
    else:
        await service.change(action, context=USER, **fields)
    service.info = lambda: {'execution': {'state': 'running'}, 'delivery': {'status': 'deferred'}}
    before = await service.current()
    tool = create_goal_tool(service, lambda: OperationContext(False))
    result = unpack(await tool.execute('automatic', {'action': 'status'}))
    assert result['next']['action'] == expected and result['next']['requires_user']
    assert await service.current() == before


async def test_running_model_must_still_inspect_an_unknown_original_delivery(service):
    await create(service)
    service.info = lambda: {'execution': {'state': 'running'},
                           'delivery': {'status': 'unknown', 'message_id': 'original'}}
    result = unpack(await create_goal_tool(service, lambda: USER).execute('read', {'action': 'status'}))
    assert result['next']['action'] == 'inspect_delivery'
    assert result['delivery']['message_id'] == 'original'
