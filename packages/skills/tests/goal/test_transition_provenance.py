"""Original host provenance is atomic with lifecycle revisions, not a permission grant."""
import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest
from xdog.skills.goal.codec import json_text
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Goal, Owner
from xdog.skills.goal.store import GoalStore

from packages.skills.tests.goal.test_runtime import close, setup


async def test_actual_native_resume_remains_visible_after_plan_and_model_report(tmp_path, monkeypatch):
    runtime, parent, _, state, sent, authorized, _ = await setup(tmp_path, monkeypatch)
    state['state'] = 'running'
    try:
        await parent.extensions.command('goal', 'create 明确用户目标')
        await runtime.service.change('wait', until='user', reason='需要决定')
        authorized[0] = False
        assert (await runtime.tools[0].execute('denied', {'action': 'resume'})).is_error
        authorized[0] = True
        await parent.extensions.command('goal', 'pause User explicitly pauses before resume')
        await parent.extensions.command('goal', 'resume')
        resumed = await runtime.service.current()
        authorized[0] = False
        await runtime.service.plan('继续核对证据，不代表故障已修复')
        report = {'expected_version': 0, 'progress': 'baseline', 'summary': '已确认用户恢复',
                  'completed': [], 'evidence': [], 'blockers': [], 'next_steps': ['核对任务']}
        assert not (await runtime.tools[0].execute('report', {'action': 'report', 'report': report})).is_error
        summary = await runtime.service.summary()
        last = summary['last_transition']
        assert last == {'availability': 'available', 'format': 'xdog.goal_transition.v1',
                        'goal_id': resumed.id, 'version': resumed.version, 'action': 'resume',
                        'origin': 'native_command', 'actor': 'user', 'user_authorized': True,
                        'command': '/goal resume', 'from': {'status': 'paused', 'wait_for': ''},
                        'to': {'status': 'active', 'wait_for': ''}}
        result = await runtime.tools[0].execute('status', {'action': 'status'})
        assert json.loads(result.content[0].text)['last_transition'] == last
        assert (await runtime.service.status())['last_transition'] == last
        before = await runtime.service.store.history(resumed.id)
        for args in ['', 'status', 'status --json', 'history', 'history --json']:
            await parent.extensions.command('goal', args)
        assert await runtime.service.store.history(resumed.id) == before
        assert not sent
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('user_turn', [False, True])
async def test_model_and_runtime_origins_cannot_be_confused_with_user_slash_command(tmp_path, monkeypatch, user_turn):
    runtime, parent, _, state, sent, authorized, _ = await setup(tmp_path, monkeypatch)
    state['state'] = 'running'
    try:
        await parent.extensions.command('goal', 'create User intent')
        authorized[0] = user_turn
        assert not (await runtime.tools[0].execute('pause', {'action': 'pause', 'reason': 'No progress'})).is_error
        paused = (await runtime.service.status())['last_transition']
        assert paused['origin'] == 'model_tool' and paused['actor'] == 'agent'
        assert paused['command'] == '' and paused['user_authorized'] is user_turn
        for forged in [{'actor': 'user'}, {'origin': 'native_command'}, {'user_authorized': True},
                       {'last_transition': {'origin': 'native_command'}}]:
            result = await runtime.tools[0].execute('forged', {'action': 'resume', **forged})
            assert result.is_error
        assert (await runtime.service.current()).status == 'paused'
        authorized[0] = True
        await parent.extensions.command('goal', 'resume')
        await runtime._host_wait('failed')
        last = (await runtime.service.summary())['last_transition']
        assert last['origin'] == 'runtime' and last['actor'] == 'host'
        assert last['action'] == 'wait' and last['to'] == {'status': 'waiting', 'wait_for': 'host'}
        assert not last['user_authorized'] and last['command'] == ''
    finally:
        await close(runtime, parent)


async def test_transition_insert_failure_rolls_back_state_history_binding_and_wake_signal(tmp_path):
    owner = Owner('original', tmp_path)
    store = await GoalStore.open(tmp_path / 'db/registry.sqlite3', owner)
    try:
        first = Goal('one', 'Original', 'Outcome')
        user = OperationContext(True, 'native_command')
        await store.put(first, expected_version=0, action='create', context=user)
        before = await store.wake_state(first.id)
        def inject(db):
            db.execute("CREATE TRIGGER reject_transition BEFORE INSERT ON transitions "
                       "BEGIN SELECT RAISE(ABORT,'injected provenance failure'); END")
        await store.database.disk(inject)
        with pytest.raises(sqlite3.IntegrityError, match='injected provenance failure'):
            await store.put(replace(first, version=2, status='cleared'), expected_version=1,
                            action='clear', context=user)
        assert await store.current() == first
        assert await store.history(first.id) == (first,)
        assert await store.wake_state(first.id) == before
        assert (await store.last_transition(first))['action'] == 'create'
    finally:
        await store.close()


async def test_reopen_preserves_provenance_without_touching_unknown_reminder(tmp_path):
    path, owner = tmp_path / 'db/registry.sqlite3', Owner('original', tmp_path)
    store = await GoalStore.open(path, owner)
    goal = Goal('one', 'Original', 'Outcome')
    await store.put(goal, expected_version=0, action='create', context=OperationContext(True, 'native_command'))
    await store.reminder(goal, {'id': 'unknown', 'instruction': 'not replayable'})
    assert await store.claim_reminder('unknown', goal)
    before = await store.last_transition(goal)
    await store.close()
    store = await GoalStore.open(path, owner)
    try:
        assert await store.last_transition(goal) == before
        assert (await store.reminders())[0]['status'] == 'unknown'
    finally:
        await store.close()


async def test_existing_v2_without_provenance_is_unknown_and_not_backfilled(tmp_path):
    path, owner = tmp_path / 'db/registry.sqlite3', Owner('original', tmp_path)
    store = await GoalStore.open(path, owner)
    goal = Goal('one', 'Preexisting', 'Do not infer actor')
    await store.put(goal, expected_version=0)
    await store.database.disk(lambda db: db.execute('DROP TABLE transitions'))
    await store.close()
    store = await GoalStore.open(path, owner)
    try:
        assert (await store.last_transition(goal))['availability'] == 'unavailable'
        user = OperationContext(True, 'native_command')
        paused = replace(goal, version=2, status='paused')
        await store.put(paused, expected_version=1, action='pause', context=user)
        assert (await store.last_transition(paused))['origin'] == 'native_command'
        # A later legacy/internal state mutation without a recorded cause cannot borrow that origin.
        resumed = replace(paused, version=3, status='active')
        await store.put(resumed, expected_version=2)
        assert (await store.last_transition(resumed))['availability'] == 'unavailable'
        rows = await store.database.disk(lambda db: db.execute('SELECT version FROM transitions').fetchall())
        assert [row[0] for row in rows] == [2]
    finally:
        await store.close()


@pytest.mark.parametrize('field,value', [('actor', 'host'), ('command', '/goal clear'), ('version', True),
                                         ('from', {'status': 'waiting', 'wait_for': 'user'})])
async def test_forged_or_corrupt_provenance_is_rejected_before_recovery(tmp_path, field, value):
    path, owner = tmp_path / 'db/registry.sqlite3', Owner('original', tmp_path)
    store = await GoalStore.open(path, owner)
    goal = Goal('one', 'Original', 'Outcome')
    await store.put(goal, expected_version=0, action='create', context=OperationContext(True, 'native_command'))
    await store.reminder(goal, {'id': 'sending', 'instruction': 'Must not recover corrupt DB'})
    assert await store.claim_reminder('sending', goal)
    await store.close()
    with sqlite3.connect(path) as db:
        data = json.loads(db.execute('SELECT payload FROM transitions').fetchone()[0])
        data[field] = value
        encoded = json_text(data)
        db.execute('UPDATE transitions SET payload=?,digest=?',
                   (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='transition'):
        await GoalStore.open(path, owner)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT status FROM reminders').fetchone()[0] == 'sending'
