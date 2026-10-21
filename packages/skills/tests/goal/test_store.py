"""Registry corruption, exact owner binding, live leases and non-replay recovery."""
import sqlite3
from dataclasses import replace

import pytest
from xdog.skills.goal.models import Goal, Owner
from xdog.skills.goal.store import GoalStore


@pytest.mark.parametrize('mismatch', ['key', 'workspace'])
async def test_owner_mismatch_refused_byte_preserving(tmp_path, mismatch):
    path = tmp_path / 'data/registry.sqlite3'
    owner = Owner('owner', tmp_path)
    store = await GoalStore.open(path, owner)
    goal = Goal('one', 'Title', 'Outcome')
    await store.put(goal, expected_version=0)
    with pytest.raises(BlockingIOError):
        await GoalStore.open(path, owner)
    await store.close()
    before = path.read_bytes()
    wrong = Owner('other' if mismatch == 'key' else owner.key,
                  tmp_path / 'other' if mismatch == 'workspace' else tmp_path)
    with pytest.raises(ValueError, match='owner'):
        await GoalStore.open(path, wrong)
    assert path.read_bytes() == before


@pytest.mark.parametrize('corrupt', ['binding', 'history', 'digest', 'format'])
async def test_validation_happens_before_recovery(tmp_path, corrupt):
    path = tmp_path / 'data/registry.sqlite3'
    owner = Owner('owner', tmp_path)
    store = await GoalStore.open(path, owner)
    goal = Goal('one', 'Title', 'Outcome')
    await store.put(goal, expected_version=0)
    await store.reminder(goal, {'id': 'reminder', 'instruction': 'data'})
    assert await store.claim_reminder('reminder', goal)
    await store.close()
    with sqlite3.connect(path) as db:
        db.execute({'binding': 'UPDATE current SET goal_id=NULL',
                    'history': 'DELETE FROM revisions', 'digest': "UPDATE goals SET digest='corrupt'",
                    'format': "UPDATE metadata SET format='unknown'"}[corrupt])
    before = path.read_bytes()
    with pytest.raises(ValueError):
        await GoalStore.open(path, owner)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT status FROM reminders').fetchone() == ('sending',)


async def test_outbox_recovery_does_not_repeat_or_replace_unknown_send(tmp_path):
    path, owner = tmp_path / 'data/registry.sqlite3', Owner('owner', tmp_path)
    store = await GoalStore.open(path, owner)
    goal = Goal('one', 'Title', 'Outcome')
    await store.put(goal, expected_version=0)
    payload = {'id': 'original', 'instruction': 'Continue'}
    await store.reminder(goal, payload)
    assert await store.claim_reminder('original', goal)
    await store.close()
    store = await GoalStore.open(path, owner)
    try:
        assert (await store.reminders())[0]['status'] == 'unknown'
        assert (await store.reminder(goal, payload))['status'] == 'unknown'
        with pytest.raises(ValueError, match='content conflict'):
            await store.reminder(goal, {**payload, 'instruction': 'different'})
        paused = replace(goal, version=2, status='paused')
        await store.put(paused, expected_version=1)
        with pytest.raises(ValueError):
            await store.reminder(goal, {'id': 'stale'})
    finally:
        await store.close()
