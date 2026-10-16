"""Online/offline intake automatically joins the sole owner without message approval."""
import asyncio
from dataclasses import replace

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.sessions.inbox_models import InboxMessage
from xdog.coding.core.sessions.inbox_store import InboxStore


def seed(session, count):
    store = InboxStore(session.session_manager._dir, session.session_manager._dir.parent / 'inbox')
    for index in range(count):
        store.enqueue(InboxMessage(session.session_id, session.working_dir, f'queued-{index}', f'message{index}',
                                   (), 'callback', {'index': index}))
    return store


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize('bound', [True, False])
async def test_owner_activation_waits_for_setup_not_message_approval(coding_session, bound):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 5)
    calls, approvals = [], []
    coding_session.agent.set_stream_fn(lambda model, context, options: calls.append(tuple(context.messages))
                                       or EventStream.empty(AssistantMessage(content=(TextContent(text='processed'),))))
    coding_session.permissions.set_request_handler(approvals.append)
    runtime = SessionRuntime(coding_session, bound=bound, inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        initial = await runtime.inbox.snapshot()
        assert [row.status for row in initial] == ['pending'] * 5
        assert calls == [] and approvals == []
        await runtime.activate_inputs()
        await wait_for(lambda: all(row.status == 'succeeded' for row in store.records(
            coding_session.session_id, coding_session.working_dir)))
        assert approvals == [] and len(calls) == 1
        assert len([message for message in calls[0] if isinstance(message, UserMessage)]) == 5
        rows = store.records(coding_session.session_id, coding_session.working_dir)
        assert len({row.batch_id for row in rows}) == 1 and rows[0].batch_id
        assert [row.message.message_id for row in rows] == [f'queued-{i}' for i in range(5)]
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_sdk_only_transient_owner_consumes_pending_with_its_human_message(coding_session):
    store = seed(coding_session, 2)
    calls = []
    coding_session.agent.set_stream_fn(lambda model, context, options: calls.append(tuple(context.messages))
                                       or EventStream.empty(AssistantMessage(content=(TextContent(text='processed'),))))
    response = await coding_session.send_message('human')
    assert response.content == (TextContent(text='processed'),)
    assert coding_session.runtime is None and coding_session._sdk_controller is None
    assert len(calls) == 1 and len([m for m in calls[0] if isinstance(m, UserMessage)]) == 3
    assert [row.status for row in store.records(coding_session.session_id, coding_session.working_dir)] == [
        'succeeded', 'succeeded']


async def test_online_busy_owner_keeps_messages_pending_then_consumes_next_batch(coding_session):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 0)
    calls, entered, release = [], asyncio.Event(), asyncio.Event()

    def stream(model, context, options):
        calls.append(tuple(context.messages))
        if len(calls) > 1:
            return EventStream.empty(AssistantMessage())
        future = asyncio.get_running_loop().create_future()

        async def events():
            entered.set()
            await release.wait()
            result = AssistantMessage()
            future.set_result(result)
            yield DoneEvent(message=result)

        return EventStream.from_async_generator(events(), result_future=future)

    coding_session.agent.set_stream_fn(stream)
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start()
    try:
        ticket = runtime.controller.submit(SessionInput('human', 'human', 'coding', 'work', (), {}), peer_id='human')
        await asyncio.wait_for(entered.wait(), 5)
        message = InboxMessage(coding_session.session_id, coding_session.working_dir, 'one', 'one', (), 'callback', {})
        store.enqueue(message)
        store.enqueue(replace(message, message_id='two', instruction='two'))
        assert await asyncio.wait_for(runtime.inbox.drain(), 1) == ()
        assert [row.status for row in store.records(coding_session.session_id, coding_session.working_dir)] == [
            'pending', 'pending']
        release.set()
        await asyncio.wait_for(asyncio.wrap_future(ticket.result), 5)
        await wait_for(lambda: len(calls) == 2 and all(row.status == 'succeeded' for row in store.records(
            coding_session.session_id, coding_session.working_dir)))
        assert len([m for m in calls[-1] if isinstance(m, UserMessage)]) == 3
        rows = store.records(coding_session.session_id, coding_session.working_dir)
        assert rows[0].batch_id == rows[1].batch_id
    finally:
        release.set()
        await asyncio.wrap_future(runtime.close())


async def test_old_branch_pending_message_is_not_silently_retargeted(coding_session):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 1)
    coding_session.session_data.active_branch = 'different-branch'
    coding_session.session_manager.save_session(coding_session.session_data)
    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))
    runtime = SessionRuntime(coding_session, bound=False, inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        await runtime.activate_inputs()
        assert calls == []
        row = (await runtime.inbox.snapshot())[0]
        assert row.status == 'pending' and row.branch_id == ''
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_transient_sdk_owner_keeps_unconsumed_backlog_pending(coding_session):
    store = seed(coding_session, 5)
    coding_session.settings.set_session_max_messages_per_turn(2)
    calls = []
    coding_session.agent.set_stream_fn(lambda model, context, options: calls.append(tuple(context.messages))
                                       or EventStream.empty(AssistantMessage()))
    await coding_session.send_message('human')
    rows = store.records(coding_session.session_id, coding_session.working_dir)
    assert len(calls) == 1
    assert [row.status for row in rows] == ['succeeded', 'pending', 'pending', 'pending', 'pending']


async def test_concurrent_drain_requests_join_one_attempt_and_one_turn(coding_session):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 0)
    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))
    runtime = SessionRuntime(coding_session, bound=False, inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        await runtime.activate_inputs()
        seed(coding_session, 3)
        batches = await asyncio.gather(*(runtime.inbox.drain() for _ in range(5)))
        await wait_for(lambda: all(row.status == 'succeeded' for row in store.records(
            coding_session.session_id, coding_session.working_dir)))
        assert len(calls) == 1
        assert len({ticket.id for batch in batches for ticket in batch}) <= 3
        assert [len(row.attempts) for row in store.records(coding_session.session_id,
                                                         coding_session.working_dir)] == [1, 1, 1]
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_activation_without_writer_lease_cannot_consume(coding_session):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 1)
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    runtime.session._lease.close()
    try:
        with pytest.raises(RuntimeError, match='lease'):
            await runtime.activate_inputs()
        assert coding_session.messages == []
        assert store.records(coding_session.session_id, coding_session.working_dir)[0].status == 'pending'
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_native_owner_installs_handlers_before_automatic_consumption(coding_session):
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    store = seed(coding_session, 2)
    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))
    ui = InteractiveMode(coding_session)
    try:
        await asyncio.to_thread(ui._session_events.start)
        await wait_for(lambda: all(row.status == 'succeeded' for row in store.records(
            coding_session.session_id, coding_session.working_dir)))
        assert len(calls) == 1 and coding_session.runtime.session is coding_session
        assert not coding_session.permissions.has_pending_requests
    finally:
        ui._request_exit()
        await asyncio.to_thread(ui._session_events.close)


async def test_activation_storage_failure_reports_and_settles_already_accepted_input(coding_session, monkeypatch):
    import threading

    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 1)
    entered, release = threading.Event(), threading.Event()
    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))

    def fail(*args):
        entered.set()
        release.wait(5)
        raise OSError('recover failed')

    monkeypatch.setattr(store, 'recover', fail)
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start()
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        ticket = runtime.controller.submit(SessionInput('human', 'human', 'coding', 'hello', (), {}), peer_id='human')
        await asyncio.wait_for(asyncio.wrap_future(ticket.accepted), 3)
        release.set()
        result = await asyncio.wait_for(asyncio.wrap_future(ticket.result), 3)
        assert result.status == 'failed' and calls == []
        assert isinstance(runtime.controller._broken, OSError)
    finally:
        release.set()
        await asyncio.wrap_future(runtime.close())


async def test_unrelated_peer_cannot_cancel_unowned_durable_message(coding_session):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 1)
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        assert not await runtime.inbox.cancel('queued-0', peer_id='unrelated-acp-peer')
        assert store.records(coding_session.session_id, coding_session.working_dir)[0].status == 'pending'
        assert await runtime.inbox.cancel('queued-0', peer_id='inbox-command')
        assert store.records(coding_session.session_id, coding_session.working_dir)[0].status == 'cancelled'
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_waiting_initializer_cannot_start_disk_work_after_owner_retirement(coding_session, monkeypatch):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 1)
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    entered, recovered = asyncio.Event(), []
    context = runtime.inbox._context
    recover = store.recover

    def observe_context():
        context()
        entered.set()

    def observe_recover(*args):
        recovered.append(True)
        return recover(*args)

    monkeypatch.setattr(runtime.inbox, '_context', observe_context)
    monkeypatch.setattr(store, 'recover', observe_recover)
    await runtime.inbox._initializing.acquire()
    task = asyncio.create_task(runtime.inbox.snapshot())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.wait_for(asyncio.wrap_future(runtime.close()), 3)
        runtime.inbox._initializing.release()
        with pytest.raises(RuntimeError):
            await task
        assert recovered == [], 'retirement must revoke a producer before it creates another IO worker'
    finally:
        if runtime.inbox._initializing.locked():
            runtime.inbox._initializing.release()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


async def test_obsolete_approve_command_is_rejected_without_requesting_permission(coding_session):
    from xdog.coding.core.inbox_commands import inbox_action
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 1)
    requests = []
    coding_session.permissions.set_request_handler(requests.append)
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        with pytest.raises(ValueError, match='Usage|obsolete|automatic'):
            await inbox_action(coding_session, 'approve', ('queued-0',))
        assert requests == [] and coding_session.messages == []
        result = await inbox_action(coding_session, 'cancel', ('queued-0',))
        assert result['status'] == 'cancelled'
        assert store.records(coding_session.session_id, coding_session.working_dir)[0].status == 'cancelled'
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_cancel_command_does_not_claim_a_running_shared_message_was_withdrawn(coding_session):
    from xdog.coding.core.inbox_commands import inbox_action
    from xdog.coding.core.sessions.models import TurnResult
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 2)
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(batch):
        entered.set()
        await release.wait()
        return TurnResult('succeeded', None)

    runtime.controller._execute_batch = execute
    runtime.start(setup_pending=True)
    try:
        await runtime.activate_inputs()
        await asyncio.wait_for(entered.wait(), 3)
        response = await inbox_action(coding_session, 'cancel', ('queued-0',))
        assert response['status'] == 'cancel_requested'
        assert response['message_state'] == 'running' and response['execution_continues']
        assert [row.status for row in store.records(coding_session.session_id, coding_session.working_dir)] == [
            'running', 'running']
        release.set()
        await wait_for(lambda: all(row.status == 'succeeded' for row in store.records(
            coding_session.session_id, coding_session.working_dir)))
    finally:
        release.set()
        await asyncio.wrap_future(runtime.close())


async def test_stopping_intake_prevents_new_claims_before_owner_resource_shutdown(coding_session):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 0)
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        await runtime.activate_inputs()
        await runtime.inbox.stop_polling()
        seed(coding_session, 1)
        with pytest.raises(RuntimeError, match='stopp|unavailable'):
            await runtime.inbox.drain()
        assert coding_session.messages == []
        assert store.records(coding_session.session_id, coding_session.working_dir)[0].status == 'pending'
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_direct_sdk_prompt_activates_the_borrowed_owner(coding_session):
    assert await asyncio.wait_for(coding_session.send_message('human'), 1) is not None
    assert coding_session._sdk_controller is None


async def test_duplicate_prompt_intake_joins_one_controller_attempt(coding_session):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = seed(coding_session, 0)
    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        await runtime.activate_inputs()
        message = InboxMessage(coding_session.session_id, coding_session.working_dir, 'prompt', 'hello', (), 'peer', {})
        first, second = await asyncio.gather(runtime.inbox.submit_prompt(message, peer_id='peer'),
                                              runtime.inbox.submit_prompt(message, peer_id='peer'))
        assert first.id == second.id
        outcomes = await asyncio.gather(asyncio.wrap_future(first.result), asyncio.wrap_future(second.result))
        assert [result.status for result in outcomes] == ['succeeded', 'succeeded']
        rows = store.records(coding_session.session_id, coding_session.working_dir)
        assert len(calls) == 1 and len(rows) == 1 and len(rows[0].attempts) == 1
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_idle_inbox_poll_does_not_reserve_a_prompt_control(coding_session,monkeypatch):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session,bound=True)
    runtime.start(setup_pending=True)
    admissions,reads = [],[]
    polled = asyncio.Event()
    admit,snapshot = runtime.inbox._admit_pending,runtime.inbox._snapshot

    async def counted_admit():
        admissions.append(True)
        return await admit()

    async def counted_snapshot():
        result = await snapshot()
        reads.append(True)
        if len(reads)>=2:
            polled.set()
        return result

    monkeypatch.setattr(runtime.inbox,'_admit_pending',counted_admit)
    try:
        await runtime.activate_inputs()
        baseline = len(admissions)
        monkeypatch.setattr(runtime.inbox,'_snapshot',counted_snapshot)
        await asyncio.wait_for(polled.wait(),2)
        await asyncio.sleep(0.05)
        assert len(admissions)==baseline, 'an empty poll must not masquerade as busy prompt/control work'
        assert not runtime.controller.busy
    finally:
        await asyncio.wrap_future(runtime.close())
