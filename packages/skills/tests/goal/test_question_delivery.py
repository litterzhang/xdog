"""Optional host correlation is read-only and owner-bound, never a Goal sender."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from xdog.skills.goal.host import OwnerRetired
from xdog.skills.goal.presentation import format_status
from xdog.skills.goal.ui_views import GoalView

from packages.skills.tests.goal.test_questions import QUESTION
from packages.skills.tests.goal.test_runtime import close, setup
from packages.skills.tests.goal.test_service import USER, opened


async def test_question_reference_tracks_wait_revision_not_plan_or_target_updates(tmp_path):
    service = await opened(tmp_path)
    try:
        goal = await service.create('One', 'Stable question identity', context=USER)
        waiting = await service.change('wait', **QUESTION)
        reference = f'goal-question:{goal.id}:{waiting.version}'
        for mutate in (lambda: service.plan('Additional recorded context'),
                       lambda: service.change('update', context=USER, title='Explicit revised label')):
            await mutate()
            facts = await service.summary()
            assert facts['question']['id'] == reference
            assert facts['question']['delivery']['status'] == 'unavailable'
        same = await service.change('wait', context=USER, **QUESTION)
        assert (await service.summary())['question']['id'] == reference
        assert same == await service.current()
        replaced = await service.change('wait', context=USER, **{**QUESTION, 'question': 'Choose C or D?'})
        assert (await service.summary())['question']['id'] == f'goal-question:{goal.id}:{replaced.version}'
        path, owner = service.store.path, service.store.owner
        await service.close()
        from xdog.skills.goal.service import GoalService
        from xdog.skills.goal.store import GoalStore
        service = GoalService(await GoalStore.open(path, owner))
        assert (await service.summary())['question']['id'] == f'goal-question:{goal.id}:{replaced.version}'
    finally:
        await service.close()


@pytest.mark.parametrize('status', [
    'delivered', 'pending', 'unknown', 'partial', 'cancelled', 'untracked', 'unavailable',
])
async def test_actual_host_facts_are_displayed_without_goal_mutation_or_sending(tmp_path, monkeypatch, status):
    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    try:
        state['state'] = 'running'
        goal = await runtime.service.create('One', 'Query actual output only', context=USER)
        waiting = await runtime.service.change('wait', **QUESTION)
        await runtime.flush()
        before = await runtime.service.store.history(goal.id)
        facts = {'status': status, 'outputs': [{'id': 'original-output', 'status': status}]}
        host.message_delivery = AsyncMock(return_value=facts)
        # Bind the same original host port that production open() discovers.
        runtime.service.message_delivery = host.message_delivery
        result = await runtime.service.summary()
        reference = f'goal-question:{goal.id}:{waiting.version}'
        host.message_delivery.assert_awaited_once_with(reference)
        assert result['question']['delivery'] == facts
        assert result['question_record']['id'] == reference
        assert result['question_record']['delivery_status'] == status
        assert result['next']['owner'] == 'user'
        row = (await GoalView(runtime.service).read({}))['items'][0]
        assert row['question']['id'] == reference and row['question']['delivery'] == facts
        text = format_status(result)
        assert f'Question delivery: {status};' in text
        assert 'saving is not proof of delivery' in text
        assert await runtime.service.current() == waiting
        assert await runtime.service.store.history(goal.id) == before
        assert not sent and not await runtime.service.store.reminders()
        facts['outputs'][0]['status'] = 'tampered-later'
        assert result['question']['delivery']['outputs'][0]['status'] == status  # Detached snapshot.
    finally:
        await close(runtime, parent)


async def test_runtime_open_discovers_optional_original_host_port(tmp_path, monkeypatch):
    from xdog.skills.goal.runtime import GoalRuntime

    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    try:
        state['state'] = 'running'
        await runtime.service.create('One', 'Optional host port', context=USER)
        waiting = await runtime.service.change('wait', **QUESTION)
        await runtime.flush()
        await runtime.aclose()
        host.message_delivery = AsyncMock(return_value={
            'status': 'untracked', 'reason': 'No correlated receipt; not proof nothing was sent'})
        runtime = await GoalRuntime.open(host)
        runtime.attach(parent)
        result = await runtime.service.summary()
        assert result['question']['delivery'] == {
            'status': 'untracked', 'outputs': [], 'reason': 'No correlated receipt; not proof nothing was sent'}
        assert result['question_record']['delivery_status'] == 'untracked'
        host.message_delivery.assert_awaited_with(f'goal-question:{waiting.id}:{waiting.version}')
        assert not sent
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('value', [None, {}, {'status': 'accepted', 'outputs': []},
    {'status': 'delivered', 'outputs': []},
    {'status': 'delivered', 'outputs': 'not a list'}, {'status': 'delivered', 'outputs': [1]},
    {'status': 'delivered', 'outputs': [], 'extra': 'x' * 65536},
    {'status': 'unknown', 'outputs': [], 'extra': float('nan')},
])
async def test_invalid_host_facts_are_unavailable_not_delivered_or_proven_not_sent(tmp_path, value):
    service = await opened(tmp_path)
    try:
        await service.create('One', 'Finite receipt facts', context=USER)
        waiting = await service.change('wait', **QUESTION)
        service.message_delivery = AsyncMock(return_value=value)
        result = await service.summary()
        assert result['question']['delivery']['status'] == 'unavailable'
        assert await service.current() == waiting
    finally:
        await service.close()


async def test_port_failure_cancellation_and_owner_retirement_do_not_forge_delivery(tmp_path):
    service = await opened(tmp_path)
    try:
        await service.create('One', 'Actual query lifecycle', context=USER)
        waiting = await service.change('wait', **QUESTION)
        service.message_delivery = AsyncMock(side_effect=OSError('query unavailable'))
        assert (await service.summary())['question']['delivery']['status'] == 'unavailable'
        service.message_delivery = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await service.summary()
        assert not service._lock.locked() and await service.current() == waiting

        async def retired(reference):
            service.valid = lambda: False
            return {'status': 'delivered', 'outputs': []}
        service.message_delivery = retired
        with pytest.raises(OwnerRetired):
            await service.summary()
    finally:
        await service.close()


async def test_read_does_not_query_host_without_actual_recorded_question_reference(tmp_path):
    service = await opened(tmp_path)
    try:
        service.message_delivery = AsyncMock(return_value={'status': 'delivered', 'outputs': []})
        await service.create('One', 'No fabricated association', context=USER)
        assert (await service.summary())['question'] is None
        await service.change('wait', until='tasks')
        assert (await service.summary())['question'] is None
        service.message_delivery.assert_not_awaited()
    finally:
        await service.close()


async def test_identical_wait_ack_loss_retry_is_readonly_and_reason_edit_preserves_reference(tmp_path):
    from xdog.skills.goal.host import OperationContext

    service = await opened(tmp_path)
    try:
        goal = await service.create('One', 'Idempotent question', context=USER)
        waiting = await service.change('wait', reason='Original blocker', **QUESTION)
        reference = (await service.summary())['question']['id']
        history = await service.store.history(goal.id)
        wake = await service.store.wake_state(goal.id)
        changes = []
        service.on_change = lambda: changes.append('changed')
        # Original model invocation may have no user authority. Repeating its
        # exact committed wait is read-only; it cannot release or rewrite it.
        for params in (QUESTION, {**QUESTION, 'until': 'user', 'reason': 'Original blocker'}):
            assert await service.change('wait', context=OperationContext(), **params) == waiting
        assert await service.store.history(goal.id) == history
        assert await service.store.wake_state(goal.id) == wake and changes == []
        with pytest.raises(ValueError, match='user operation'):
            await service.change('wait', reason='New rationale', **QUESTION)
        updated = await service.change('wait', reason='New rationale', context=USER, **QUESTION)
        assert updated.version == waiting.version + 1 and updated.reason == 'New rationale'
        assert (await service.summary())['question']['id'] == reference
        assert changes == ['changed']
        with pytest.raises(ValueError, match='user operation'):
            await service.change('wait', **{**QUESTION, 'question': 'Different request'})
        assert await service.current() == updated
    finally:
        await service.close()


@pytest.mark.parametrize('outputs', [
    [{}], [{'status': 'delivered'}], [{'id': '', 'status': 'delivered'}],
    [{'id': '  ', 'status': 'delivered'}], [{'id': 42, 'status': 'delivered'}],
    [{'id': 'bad\x00id', 'status': 'delivered'}], [{'id': 'output'}],
    [{'id': 'output', 'status': 'unknown'}], [{'id': 'output', 'status': 'pending'}],
    [{'id': 'output', 'status': 'cancelled'}],
    [{'id': 'one', 'status': 'delivered'}, {'id': 'two', 'status': 'unknown'}],
])
async def test_delivered_aggregate_requires_valid_delivered_output_rows(tmp_path, outputs):
    service = await opened(tmp_path)
    try:
        goal = await service.create('One', 'Validate transport facts, not task acceptance', context=USER)
        waiting = await service.change('wait', **QUESTION)
        history = await service.store.history(goal.id)
        service.message_delivery = AsyncMock(return_value={'status': 'delivered', 'outputs': outputs})
        facts = await service.summary()
        assert facts['question']['delivery']['status'] == 'unavailable'
        assert facts['question_record']['delivery_status'] == 'unavailable'
        assert 'Question delivery: delivered;' not in format_status(facts)
        assert await service.current() == waiting
        assert await service.store.history(goal.id) == history
    finally:
        await service.close()
