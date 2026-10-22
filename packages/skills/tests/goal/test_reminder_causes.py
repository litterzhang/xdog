"""The accepted through boundary filters NEW cause previews, not execution evidence."""
import asyncio

import pytest
from xdog.skills.goal.codec import read_json

from packages.skills.tests.goal.legacy import wait_goal
from packages.skills.tests.goal.test_reminder_coalescing import receipt_facts
from packages.skills.tests.goal.test_runtime import close, setup
from packages.skills.tests.goal.test_service import USER


async def advance(store, goal, sequence):
    wake = await store.wake_state(goal.id)
    for index in range(wake['sequence'] + 1, sequence + 1):
        assert await store.signal(goal, f'tasks:change-{index}')
    return await store.wake_state(goal.id)


async def test_8_12_17_22_filter_before_last16_and_keep_ack_execution_separate(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    receipts = await receipt_facts(monkeypatch)
    try:
        state['state'] = 'running'
        goal = await runtime.service.create('One', 'Only new causes', context=USER)
        await runtime.flush()
        await advance(runtime.service.store, goal, 8)
        state['state'] = 'idle'
        await runtime._wake(None)
        assert len(sent) == 1 and sent[0].provenance['wake_through'] == 8
        receipts[sent[0].id] = 'pending'
        await advance(runtime.service.store, goal, 12)
        await runtime._wake(None)
        assert len(sent) == 1  # ACK does not mean executed, even with NEW causes.
        wake = await runtime.service.store.wake_state(goal.id)
        assert wake['through'] == 8 and wake['cause_count'] == 4
        assert set(wake['causes']) == {f'tasks:change-{i}' for i in range(9, 13)}
        receipts[sent[0].id] = 'succeeded'
        await runtime._wake(None)
        assert len(sent) == 2
        assert set(sent[1].provenance['triggers']) == set(wake['causes'])
        for boundary, target in [(12, 17), (17, 22)]:
            await advance(runtime.service.store, goal, target)
            await runtime._wake(None)
            assert sent[-1].provenance['wake_through'] == target
            assert set(sent[-1].provenance['triggers']) == {
                f'tasks:change-{i}' for i in range(boundary + 1, target + 1)}
            assert sent[-1].provenance['triggers_count'] == target - boundary
            assert sent[-1].provenance['triggers_preview_only'] is False
        assert len(sent) == 4
        for _ in range(3):
            await runtime._available()
        assert len(sent) == 4
        assert (await runtime.service.current()).status == 'active'  # No service progress verdict.
    finally:
        await close(runtime, parent)


async def test_bounded_preview_keeps_full_durable_sequence_and_reopen_does_not_replay(tmp_path, monkeypatch):
    from xdog.skills.goal.runtime import GoalRuntime

    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        state['state'] = 'running'
        goal = await runtime.service.create('One', 'Many coalesced changes', context=USER)
        await runtime.flush()
        wake = await advance(runtime.service.store, goal, 40)
        assert wake['cause_count'] == 40 and len(wake['causes']) == 16
        assert set(wake['causes']) == {f'tasks:change-{i}' for i in range(25, 41)}
        count = await runtime.service.store.database.disk(lambda db: db.execute(
            'SELECT COUNT(*) FROM wake_signals WHERE goal_id=?', (goal.id,)).fetchone()[0])
        assert count == 40  # Preview is not deletion or an execution cursor.
        state['state'] = 'idle'
        await runtime._wake(None)
        assert len(sent) == 1 and sent[0].provenance['triggers_count'] == 40
        assert sent[0].provenance['triggers_preview_only'] is True
        payload = (await runtime.service.store.reminders())[0]['payload']
        await runtime.aclose()
        runtime = await GoalRuntime.open(host)
        runtime.attach(parent)
        await runtime._available()
        await runtime.flush()
        assert len(sent) == 1
        assert (await runtime.service.store.reminders())[0]['payload'] == payload
        wake = await runtime.service.store.wake_state(goal.id)
        assert wake['through'] == 40 and wake['causes'] == [] and wake['cause_count'] == 0
    finally:
        await close(runtime, parent)


async def test_cause_arriving_during_original_enqueue_survives_accepted_boundary(tmp_path, monkeypatch):
    import xdog.agent.acp.client as client

    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = client.enqueue

    async def held(target, message):
        entered.set()
        await release.wait()
        return await original(target, message)

    pending = None
    try:
        state['state'] = 'running'
        goal = await runtime.service.create('One', 'Original race boundary', context=USER)
        await runtime.flush()
        await advance(runtime.service.store, goal, 8)
        monkeypatch.setattr(client, 'enqueue', held)
        state['state'] = 'idle'
        pending = asyncio.create_task(runtime._wake(None))
        await asyncio.wait_for(entered.wait(), 2)
        await advance(runtime.service.store, goal, 12)
        release.set()
        await asyncio.wait_for(pending, 2)
        first = (await runtime.service.store.reminders())[0]
        assert read_json(first['payload'])['provenance']['wake_through'] == 8
        await runtime._wake(None)
        assert len(sent) == 2 and sent[1].provenance['wake_through'] == 12
        assert set(sent[1].provenance['triggers']) == {f'tasks:change-{i}' for i in range(9, 13)}
        assert len({message.id for message in sent}) == 2
        assert (await runtime.service.store.reminders())[0]['payload'] == first['payload']
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(runtime, parent)


@pytest.mark.parametrize('status', ['pending', 'claimed', 'running', 'approved', 'pending_review'])
async def test_original_nonterminal_receipt_does_not_consume_new_causes(tmp_path, monkeypatch, status):
    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    receipts = await receipt_facts(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Backpressure is not processing', context=USER)
        await runtime._wake('tasks:first')
        receipts[sent[0].id] = status
        assert await runtime.service.store.signal(goal, 'tasks:later')
        for _ in range(3):
            await runtime._wake(None)
        wake = await runtime.service.store.wake_state(goal.id)
        assert len(sent) == 1 and wake['causes'] == ['tasks:later']
        receipts[sent[0].id] = 'succeeded'
        await runtime._wake(None)
        assert len(sent) == 2 and sent[1].provenance['triggers'] == ['tasks:later']
    finally:
        await close(runtime, parent)


async def test_legacy_accepted_payload_and_digest_stay_immutable_when_delta_is_rendered(tmp_path, monkeypatch):
    from xdog.agent.session_input import SessionInput, input_data

    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        state['state'] = 'running'
        goal = await runtime.service.create('One', 'Legacy accepted is not processed', context=USER)
        await runtime.flush()
        await advance(runtime.service.store, goal, 8)
        original = SessionInput('legacy-accepted', 'automatic', 'goal', 'Original reminder body', (),
                                {'goal_id': goal.id, 'goal_version': goal.version, 'wake_through': 8,
                                 'triggers': ['goal-state:1', 'tasks:historical']})
        await runtime.service.store.reminder(goal, input_data(original))
        assert await runtime.service.store.claim_reminder(original.id, goal)
        await runtime.service.store.settle_reminder(original.id, 'accepted')
        before = (await runtime.service.store.reminders())[0]
        assert 'triggers_count' not in read_json(before['payload'])['provenance']
        await advance(runtime.service.store, goal, 12)
        state['state'] = 'idle'
        await runtime._wake(None)
        assert len(sent) == 1
        assert set(sent[0].provenance['triggers']) == {f'tasks:change-{i}' for i in range(9, 13)}
        assert (await runtime.service.store.reminders())[0] == before  # Includes original status, payload, digest.
        assert before['status'] == 'accepted'  # Never rewritten as processed.
        assert (await runtime.service.current()).status == 'active'
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('boundary', ['user', 'host', 'paused'])
async def test_new_epoch_keeps_scheduling_through_but_body_delta_uses_all_accepted(tmp_path, monkeypatch, boundary):
    from packages.skills.tests.goal.test_questions import QUESTION

    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    receipts = await receipt_facts(monkeypatch)
    try:
        state['state'] = 'running'
        goal = await runtime.service.create('One', 'Separate recovery and body boundaries', context=USER)
        for index in range(6):
            goal = await runtime.service.plan(f'Initial plan {index}')
        assert goal.version == 7
        await runtime.flush()
        await advance(runtime.service.store, goal, 17)
        state['state'] = 'idle'
        await runtime._wake(None)
        assert len(sent) == 1 and sent[0].provenance['goal_version'] == 7
        assert sent[0].provenance['wake_through'] == 17
        original = (await runtime.service.store.reminders())[0]
        old_causes = set(sent[0].provenance['triggers'])
        state['state'] = 'running'
        if boundary == 'paused':
            await runtime.service.change('pause', context=USER)
            await parent.extensions.command('goal', 'resume')
            await runtime.service.plan('Post-resume context')
            active = await runtime.service.plan('Current plan')
        else:
            await wait_goal(runtime.service, until=boundary, **(QUESTION if boundary == 'user' else {}))
            await runtime.service.plan('Waiting context')
            waiting = await runtime.service.plan('Ready to inspect recovery')
            active = await runtime.service.change('activate', goal.id, version=waiting.version,
                                                  reason='Actual authorized reply/recovery checked', context=USER)
        assert active.version == 11
        await runtime.flush()
        wake = await advance(runtime.service.store, active, 22)
        assert wake['epoch'] > 7
        assert wake['through'] == 0  # Recovery scheduling still has its new epoch.
        assert wake['cause_through'] == 17  # Body coverage never forgets the old accepted input.
        assert wake['cause_count'] == 5 and len(wake['causes']) == 5
        assert not old_causes & set(wake['causes'])
        assert all(row['id'] != original['id'] for row in wake['reminders'])
        assert wake['accepted'] == [original]
        # A new epoch/body delta still cannot turn an old ACK into execution completion.
        receipts[sent[0].id] = 'pending'
        state['state'] = 'idle'
        await runtime._wake(None)
        assert len(sent) == 1
        receipts[sent[0].id] = 'succeeded'
        await runtime._wake(None)
        assert len(sent) == 2 and sent[1].provenance['goal_version'] == 11
        assert sent[1].provenance['wake_through'] == 22
        assert set(sent[1].provenance['triggers']) == set(wake['causes'])
        assert sent[1].provenance['triggers_count'] == 5
        assert (await runtime.service.store.reminders())[0] == original
        after = await runtime.service.store.wake_state(goal.id)
        assert after['through'] == after['cause_through'] == 22
        assert after['causes'] == [] and after['cause_count'] == 0
    finally:
        await close(runtime, parent)


async def test_explicit_resume_still_wakes_without_new_task_or_execution_cause(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        state['state'] = 'running'
        goal = await runtime.service.create('One', 'Explicit resume remains a wake', context=USER)
        await runtime.flush()
        await advance(runtime.service.store, goal, 17)
        state['state'] = 'idle'
        await runtime._wake(None)
        original = (await runtime.service.store.reminders())[0]
        await parent.extensions.command('goal', 'pause User paused')
        await runtime.flush()
        assert len(sent) == 1
        await parent.extensions.command('goal', 'resume')
        await runtime.flush()
        current = await runtime.service.current()
        assert len(sent) == 2 and current.status == 'active'
        assert sent[1].provenance['triggers'] == [f'goal-state:{current.version}']
        assert sent[1].provenance['triggers_count'] == 1
        assert sent[1].provenance['wake_through'] == 18
        assert (await runtime.service.store.reminders())[0] == original
    finally:
        await close(runtime, parent)
