"""Application consumer drives the existing Agent, never an ACP-owned model loop."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputStore


def envelope(root, identifier='one', group='main'):
    return InputEnvelope(InputScope(group, root), SessionInput(identifier, 'human', 'TUI', identifier, (), {}))


async def test_consumer_runs_real_agent_and_keeps_each_result_and_history(tmp_path):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    calls = []
    def model(model_id, context, options=None):
        calls.append(context.messages)
        return EventStream.empty(AssistantMessage(content=(TextContent(text=f'result-{len(calls)}'),)))
    agent = Agent(model, config=AgentConfig(model='test/dummy'), tools=[])
    async def execute(record, admit):
        assert await admit('actual-session')
        stream = await agent.prompt(record.envelope.message.instruction)
        async for _ in stream:
            pass
        return InputOutcome(response=agent.messages[-1].content[0].text, session_id='actual-session')
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute)
    try:
        one, two = envelope(tmp_path), envelope(tmp_path, 'two')
        first, duplicate = await service.submit(one)
        assert first.status == 'pending' and not duplicate
        await service.submit(two)
        results = await asyncio.gather(service.wait(one.scope, 'one'), service.wait(two.scope, 'two'))
        assert [result.response for result in results] == ['result-1', 'result-2']
        assert [''.join(part.text for part in m.content if isinstance(part, TextContent))
                for m in agent.messages if isinstance(m, UserMessage)] == ['one', 'two']
        assert all(result.session_id == 'actual-session' for result in results)
        assert (await service.submit(one))[1] is True
        assert (await service.wait(one.scope, 'one')).response == 'result-1' and len(calls) == 2
    finally:
        await service.close()


async def test_cancelling_one_waiter_does_not_cancel_execution_or_other_waiters(tmp_path):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(record, admit):
        assert await admit()
        entered.set()
        await release.wait()
        return InputOutcome(response='finished once')
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute)
    value = envelope(tmp_path)
    try:
        await service.submit(value)
        await asyncio.wait_for(entered.wait(), 2)
        gone = asyncio.create_task(service.wait(value.scope, 'one'))
        remaining = asyncio.create_task(service.wait(value.scope, 'one'))
        await asyncio.sleep(0)
        gone.cancel()
        with pytest.raises(asyncio.CancelledError):
            await gone
        assert (await store.get(value.scope, 'one')).status == 'running'
        release.set()
        assert (await asyncio.wait_for(remaining, 2)).response == 'finished once'
    finally:
        release.set()
        await service.close()


async def test_withdrawal_while_executor_waits_prevents_admission_and_allows_next_input(tmp_path):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    preparing, proceed = asyncio.Event(), asyncio.Event()
    admitted = []
    async def execute(record, admit):
        if record.envelope.message.id == 'one':
            preparing.set()
            await proceed.wait()
        if await admit():
            admitted.append(record.envelope.message.id)
        return InputOutcome(response='result')
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute)
    value = envelope(tmp_path)
    try:
        await service.submit(value)
        await asyncio.wait_for(preparing.wait(), 2)
        await store.cancel(value.scope, 'one')
        await service.submit(envelope(tmp_path, 'two'))
        proceed.set()
        assert (await asyncio.wait_for(service.wait(value.scope, 'one'), 2)).status == 'cancelled'
        assert (await asyncio.wait_for(service.wait(value.scope, 'two'), 2)).status == 'succeeded'
        assert admitted == ['two']
    finally:
        proceed.set()
        await service.close()


@pytest.mark.parametrize('admitted', [False, True])
async def test_executor_failure_is_durable_and_does_not_spin_or_strand_group(tmp_path, admitted):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    calls = []
    async def execute(record, admit):
        calls.append(record.envelope.message.id)
        if record.envelope.message.id == 'one':
            if admitted:
                await admit()
            raise ValueError('preparation or execution failed')
        assert await admit()
        return InputOutcome(response='healthy next input')
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute)
    value = envelope(tmp_path)
    try:
        await service.submit(value)
        result = await asyncio.wait_for(service.wait(value.scope, 'one'), 2)
        assert result.status == 'failed' and 'failed' in result.error
        await service.submit(envelope(tmp_path, 'two'))
        assert (await asyncio.wait_for(service.wait(value.scope, 'two'), 2)).response == 'healthy next input'
        assert (await service.submit(value))[0].status == 'failed'
        assert calls == ['one', 'two']
    finally:
        await service.close()


async def test_executor_cannot_publish_success_without_admission(tmp_path):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    async def execute(record, admit):
        return InputOutcome(response='not actually run')
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute)
    value = envelope(tmp_path)
    try:
        await service.submit(value)
        result = await asyncio.wait_for(service.wait(value.scope, 'one'), 2)
        assert result.status == 'failed' and 'admission' in result.error
    finally:
        await service.close()


@pytest.mark.parametrize('started', [False, True])
async def test_close_releases_only_unstarted_claim_and_never_replays_started_work(tmp_path, started):
    from xdog.claw.core.inputs.consumer import InputService
    entered = asyncio.Event()
    async def execute(record, admit):
        if started:
            assert await admit('session')
        entered.set()
        await asyncio.Event().wait()
    path = tmp_path / 'private' / 'inputs.sqlite3'
    store = await InputStore.open(path)
    service = InputService(store, execute)
    value = envelope(tmp_path)
    await service.submit(value)
    await asyncio.wait_for(entered.wait(), 2)
    await service.close()
    reopened = await InputStore.open(path)
    try:
        result = await reopened.get(value.scope, 'one')
        assert result.status == ('interrupted' if started else 'pending')
        assert [attempt.status for attempt in await reopened.attempts(value.scope, 'one')] == [
            'interrupted' if started else 'released']
    finally:
        await reopened.close()


async def test_wake_restores_preexisting_pending_without_any_protocol_or_tui(tmp_path):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    path = tmp_path / 'private' / 'inputs.sqlite3'
    value = envelope(tmp_path)
    store = await InputStore.open(path)
    await store.enqueue(value)
    await store.close()
    store = await InputStore.open(path)
    calls = []
    async def execute(record, admit):
        assert await admit()
        calls.append(record.envelope.message.id)
        return InputOutcome(response='restored')
    service = InputService(store, execute)
    try:
        service.wake(value.scope)
        assert (await asyncio.wait_for(service.wait(value.scope, 'one'), 2)).response == 'restored'
        assert calls == ['one']
    finally:
        await service.close()


async def test_other_groups_continue_while_one_waits_for_admission(tmp_path):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    held, entered = asyncio.Event(), asyncio.Event()
    async def execute(record, admit):
        if record.envelope.scope.group_id == 'main':
            entered.set()
            await held.wait()
        assert await admit()
        return InputOutcome(response=record.envelope.scope.group_id)
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute)
    try:
        main, other = envelope(tmp_path), envelope(tmp_path, group='other')
        await service.submit(main)
        await asyncio.wait_for(entered.wait(), 2)
        await service.submit(other)
        assert (await asyncio.wait_for(service.wait(other.scope, 'one'), 2)).response == 'other'
        held.set()
        assert (await asyncio.wait_for(service.wait(main.scope, 'one'), 2)).response == 'main'
    finally:
        held.set()
        await service.close()


async def test_close_finishes_even_when_executor_handles_its_cancellation(tmp_path):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    entered = asyncio.Event()
    async def execute(record, admit):
        await admit()
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return InputOutcome(error='Executor settled cancellation')
    path = tmp_path / 'private' / 'inputs.sqlite3'
    store = await InputStore.open(path)
    service = InputService(store, execute)
    value = envelope(tmp_path)
    await service.submit(value)
    await entered.wait()
    closer = asyncio.create_task(service.close())
    try:
        done, _ = await asyncio.wait([closer], timeout=2)
        assert closer in done, 'Consumer waited for another input after shutdown was already requested'
        await closer
    finally:
        service._signals[value.scope].set()  # Release only the broken baseline's idle wait for test cleanup.
        await asyncio.wait_for(closer, 2)


async def test_withdrawal_winning_during_shutdown_release_is_not_a_storage_fault(tmp_path, monkeypatch):
    from xdog.claw.core.inputs.consumer import InputService
    entered, releasing, proceed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def execute(record, admit):
        entered.set()
        await asyncio.Event().wait()
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute)
    value = envelope(tmp_path)
    release = store.release
    async def held_release(record):
        releasing.set()
        await proceed.wait()
        return await release(record)
    monkeypatch.setattr(store, 'release', held_release)
    await service.submit(value)
    await entered.wait()
    closer = asyncio.create_task(service.close())
    try:
        await asyncio.wait_for(releasing.wait(), 2)
        await store.cancel(value.scope, 'one')
        proceed.set()
        await asyncio.wait_for(closer, 2)
    finally:
        proceed.set()
        await asyncio.gather(closer, return_exceptions=True)


@pytest.mark.parametrize('started', [False, True])
async def test_cancelled_executor_does_not_strand_later_input_or_implicitly_retry(tmp_path, started):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def execute(record, admit):
        calls.append(record.envelope.message.id)
        if record.envelope.message.id == 'one':
            if started:
                await admit()
            entered.set()
            await release.wait()
            raise asyncio.CancelledError
        assert await admit()
        return InputOutcome(response='next remains usable')
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute)
    value = envelope(tmp_path)
    later = None
    try:
        await service.submit(value)
        await entered.wait()
        await service.submit(envelope(tmp_path, 'two'))
        release.set()
        later = asyncio.create_task(service.wait(value.scope, 'two'))
        done, _ = await asyncio.wait([later], timeout=2)
        assert done, 'Cancelled executor stranded a separately accepted later input'
        assert next(iter(done)).result().response == 'next remains usable'
        assert (await store.get(value.scope, 'one')).status == ('interrupted' if started else 'failed')
        assert calls == ['one', 'two']
    finally:
        release.set()
        await service.close()
        if later is not None:
            await asyncio.gather(later, return_exceptions=True)


async def test_close_waits_for_claim_receipt_before_releasing_its_unstarted_attempt(tmp_path, monkeypatch):
    from xdog.claw.core.inputs.consumer import InputService
    claimed, release = asyncio.Event(), asyncio.Event()
    async def execute(record, admit):
        raise AssertionError('Closing owner must not start the model')
    path = tmp_path / 'private' / 'inputs.sqlite3'
    store = await InputStore.open(path)
    original = store.claim
    async def held(scope):
        receipt = await original(scope)
        claimed.set()
        await release.wait()
        return receipt
    monkeypatch.setattr(store, 'claim', held)
    service = InputService(store, execute)
    value = envelope(tmp_path)
    closer = None
    try:
        await service.submit(value)
        await claimed.wait()
        closer = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert not closer.done()
        release.set()
        await asyncio.wait_for(closer, 2)
        reopened = await InputStore.open(path)
        try:
            assert (await reopened.get(value.scope, 'one')).status == 'pending'
            assert [attempt.status for attempt in await reopened.attempts(value.scope, 'one')] == ['released']
        finally:
            await reopened.close()
    finally:
        release.set()
        if closer is not None:
            await asyncio.gather(closer, return_exceptions=True)
        await service.close()


async def test_shutdown_after_real_terminal_commit_preserves_success(tmp_path, monkeypatch):
    import threading

    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    loop = asyncio.get_running_loop()
    committed, release = asyncio.Event(), threading.Event()
    async def execute(record, admit):
        await admit('session')
        return InputOutcome(response='committed once')
    path = tmp_path / 'private' / 'inputs.sqlite3'
    store = await InputStore.open(path)
    transaction = store._transaction
    def held(operation):
        result = transaction(operation)
        if operation.__name__ == 'finish':
            loop.call_soon_threadsafe(committed.set)
            if not release.wait(5):
                raise TimeoutError('Missing terminal commit release')
        return result
    monkeypatch.setattr(store, '_transaction', held)
    service = InputService(store, execute)
    value = envelope(tmp_path)
    closer = None
    try:
        await service.submit(value)
        await asyncio.wait_for(committed.wait(), 2)
        closer = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert not closer.done()
        release.set()
        await asyncio.wait_for(closer, 2)
        reopened = await InputStore.open(path)
        try:
            receipt = await reopened.get(value.scope, 'one')
            assert receipt.status == 'succeeded' and receipt.response == 'committed once'
        finally:
            await reopened.close()
    finally:
        release.set()
        if closer is not None:
            await asyncio.gather(closer, return_exceptions=True)
        await service.close()


async def test_failed_cancellation_settlement_fences_new_admission_instead_of_hanging(tmp_path):
    from xdog.claw.core.inputs.consumer import InputService
    async def execute(record, admit):
        raise asyncio.CancelledError
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    # A real SQL failure in cleanup, not a mock receipt or mocked worker.
    await store._disk(lambda db: db.execute("CREATE TRIGGER refuse_rejection BEFORE UPDATE ON inputs "
        "WHEN NEW.status='failed' BEGIN SELECT RAISE(ABORT,'reject unavailable'); END"))
    service = InputService(store, execute)
    value = envelope(tmp_path)
    waiter = None
    try:
        await service.submit(value)
        waiter = asyncio.create_task(service.wait(value.scope, 'one'))
        done, _ = await asyncio.wait([waiter], timeout=2)
        assert waiter in done, 'Failed cleanup lost its owner fault and left receipt waiters hanging'
        with pytest.raises(RuntimeError, match='consumer|unavailable'):
            await waiter
        with pytest.raises(RuntimeError, match='unavailable'):
            await service.submit(envelope(tmp_path, 'two'))
        assert (await store.get(value.scope, 'one')).status == 'claimed'
        assert await store.get(value.scope, 'two') is None
    finally:
        await service.close()
        if waiter is not None:
            await asyncio.gather(waiter, return_exceptions=True)


async def test_settled_hint_runs_after_durable_result_and_cannot_replay_or_poison_execution(tmp_path):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    seen, calls = [], []
    async def execute(record, admit):
        await admit()
        calls.append(record.envelope.message.id)
        return InputOutcome(response='stored')
    def settled(scope):
        seen.append(scope)
        raise ValueError('Optional notification sink unavailable')
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute, on_settled=settled)
    value = envelope(tmp_path)
    try:
        await service.submit(value)
        first = await asyncio.wait_for(service.wait(value.scope, 'one'), 2)
        await service.submit(envelope(tmp_path, 'two'))
        second = await asyncio.wait_for(service.wait(value.scope, 'two'), 2)
        assert first.status == second.status == 'succeeded'
        assert calls == ['one', 'two'] and seen == [value.scope, value.scope]
    finally:
        await service.close()


async def test_bootstrap_pause_preserves_intake_without_model_work_until_capabilities_are_ready(tmp_path):
    from xdog.claw.core.inputs.consumer import InputOutcome, InputService
    calls = []
    async def execute(record, admit):
        await admit()
        calls.append(record.envelope.message.id)
        return InputOutcome(response='ready')
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute, paused=True)
    item = envelope(tmp_path)
    try:
        await service.submit(item)
        assert (await store.get(item.scope, 'one')).status == 'pending' and not calls
        service.resume()
        assert (await asyncio.wait_for(service.wait(item.scope, 'one'), 2)).status == 'succeeded'
        assert calls == ['one']
    finally:
        await service.close()
