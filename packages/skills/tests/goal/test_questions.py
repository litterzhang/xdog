"""Questions are atomic Goal data, never delivery receipts or wait-release authority."""
import hashlib
import json
from dataclasses import asdict, replace

import pytest
from xdog.skills.goal.codec import decode_goal, encode_goal, json_text
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Goal
from xdog.skills.goal.presentation import format_status
from xdog.skills.goal.service import GoalService
from xdog.skills.goal.store import GoalStore
from xdog.skills.goal.ui_views import GoalView

from packages.skills.tests.goal.test_runtime import close, setup
from packages.skills.tests.goal.test_service import USER, opened

QUESTION = {'question': '交付 A 还是 B？', 'impact': '不能确定最终交付目标', 'resume_when': '用户明确选择 A 或 B'}


@pytest.mark.parametrize('missing', ['question', 'impact', 'resume_when'])
@pytest.mark.parametrize('bad', [None, ''])
async def test_new_user_wait_requires_all_nonempty_text_atomically(tmp_path, missing, bad):
    service = await opened(tmp_path)
    try:
        original = await service.create('One', 'Actual outcome', context=USER)
        before_wake = await service.store.wake_state(original.id)
        values = {**QUESTION, missing: bad}
        with pytest.raises(ValueError):
            await service.change('wait', until='user', **values)
        values.pop(missing)
        with pytest.raises(ValueError, match=missing):
            await service.change('wait', **values)  # The default until is user, not an escape hatch.
        assert await service.current() == original
        assert await service.store.history(original.id) == (original,)
        assert await service.store.wake_state(original.id) == before_wake
    finally:
        await service.close()


async def test_question_state_history_and_transition_roll_back_as_one_transaction(tmp_path):
    service = await opened(tmp_path)
    try:
        original = await service.create('One', 'Transactional wait', context=USER)
        # Fail AFTER current/revision writes, at lifecycle provenance insertion.
        await service.store.database.disk(lambda db: db.execute(
            "CREATE TRIGGER fail_wait BEFORE INSERT ON transitions WHEN NEW.version=2 "
            "BEGIN SELECT RAISE(ABORT,'injected failure'); END"))
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError, match='injected'):
            await service.change('wait', **QUESTION)
        assert await service.current() == original
        assert await service.store.history(original.id) == (original,)
        await service.store.database.disk(lambda db: db.execute('DROP TRIGGER fail_wait'))
        waiting = await service.change('wait', **QUESTION)
        assert waiting.status == 'waiting' and waiting.wait_for == 'user'
        path, owner = service.store.path, service.store.owner
        await service.close()
        service = GoalService(await GoalStore.open(path, owner))
        assert await service.current() == waiting
        assert {key: getattr(waiting, key) for key in QUESTION} == QUESTION
        status = await service.summary()
        assert status['question']['availability'] == 'recorded'
        assert status['question']['delivery']['status'] == 'unavailable'
        assert status['last_transition']['action'] == 'wait'
    finally:
        await service.close()


async def test_unknown_record_fields_are_rejected_without_rewriting_storage(tmp_path):
    service = await opened(tmp_path)
    goal = await service.create('Old', 'Unsupported old shape', context=USER)
    def corrupt(db):
        raw = json.loads(db.execute('SELECT payload FROM goals WHERE id=?', (goal.id,)).fetchone()[0])
        raw['unknown'] = 'not a supported field'
        payload = json_text(raw)
        db.execute('UPDATE goals SET payload=?, digest=? WHERE id=?',
                   (payload, hashlib.sha256(payload.encode()).hexdigest(), goal.id))
        return payload
    try:
        payload = await service.store.database.disk(corrupt)
        with pytest.raises(ValueError, match='Unsupported Goal record'):
            await service.current()
        assert await service.store.database.disk(lambda db: db.execute(
            'SELECT payload FROM goals WHERE id=?', (goal.id,)).fetchone()[0]) == payload
    finally:
        await service.close()


def test_v2_codec_preserves_optional_metadata_but_requires_complete_nonempty_questions():
    goal = Goal('g', 'Title', 'Outcome')
    data = json.loads(encode_goal(goal))
    for name in QUESTION:
        del data[name]
    assert decode_goal(json_text(data)) == goal
    data['question'] = 'partial'
    with pytest.raises(ValueError):
        decode_goal(json_text(data))
    with pytest.raises(ValueError):
        replace(goal, question='not a waiting goal', impact='impact', resume_when='answer')


async def test_clock_task_events_and_automatic_calls_never_release_user_wait(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        state['state'] = 'running'
        goal = await runtime.service.create('One', 'No timer authority', context=USER)
        waiting = await runtime.service.change('wait', **QUESTION)
        await runtime.flush()
        authorized[0] = False
        state['state'] = 'idle'
        monkeypatch.setattr('xdog.skills.goal.service.time.time', lambda: waiting.updated_at + 10**8)
        for _ in range(3):
            listeners[0]({'type': 'owner.available'})
            listeners[0]({'type': 'execution.settled', 'execution_id': 'clock', 'status': 'succeeded'})
            await runtime._wake('clock:elapsed')
            await runtime.flush()
        for action, fields in [('activate', {'version': waiting.version, 'reason': 'Clock elapsed'}),
                               ('complete', {'summary': 'Timer said done'}), ('wait', {'until': 'tasks'})]:
            with pytest.raises(ValueError, match='user operation'):
                await runtime.service.change(action, goal.id, context=OperationContext(), **fields)
        assert await runtime.service.current() == waiting and not sent
        assert not await runtime.service.store.reminders()
        authorized[0] = True
        active = await runtime.service.change('activate', goal.id, version=waiting.version,
                                              reason='Actual user selected A; main verified', context=USER)
        assert active.status == 'active' and not any(getattr(active, name) for name in QUESTION)
        assert asdict((await runtime.service.store.history(goal.id))[1])['question'] == QUESTION['question']
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize(
    "status,until,owner",
    [
        ("active", "", "main_agent"),
        ("waiting", "tasks", "task_workers"),
        ("waiting", "user", "user"),
        ("paused", "", "user"),
        ("completed", "", "user"),
    ],
)
async def test_next_responsibility_and_shared_question_preview(tmp_path, status, until, owner):
    service = await opened(tmp_path)
    try:
        await service.create('One', 'Responsibility', context=USER)
        if status != 'active':
            fields = ({'until': until} if status == 'waiting' else
                      {'summary': 'Verified'} if status == 'completed' else {})
            if until == 'user':
                fields.update({name: value * 100 for name, value in QUESTION.items()})
            if status == 'waiting':
                await service.change('wait', **fields)
            else:
                await service.change({'paused': 'pause', 'completed': 'complete'}[status], **fields)
        facts = await service.summary()
        row = (await GoalView(service).read({}))['items'][0]
        assert facts['next']['owner'] == row['next']['owner'] == owner
        text = format_status(facts)
        assert 'Responsibility:' in text
        if status == 'active':
            assert 'active does not mean running' in text
        if until == 'user':
            assert all(len(row['question'][name]) == 160 and name in row['question']['preview_fields']
                       for name in ('text', 'impact', 'resume_when'))
            assert 'Question:' in text and 'Impact:' in text and 'Resume when:' in text
            assert 'saving is not proof of delivery' in text
    finally:
        await service.close()
