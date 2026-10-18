"""Permanent reproductions from the single unified-inbox independent review."""
from __future__ import annotations

import asyncio
import threading
from dataclasses import replace

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.sessions.inbox_models import MAX_MESSAGE_BYTES, InboxMessage, encoded, message_data
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.runtime import SessionRuntime


def message(session, identifier='one', instruction='Review'):
    return InboxMessage(session.session_id, session.working_dir, identifier, instruction, (), 'Eyes', {})


async def wait_for(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.01)


def test_native_cancel_shared_batch_settles_preparation_before_any_model_call(coding_session, monkeypatch):
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    ui = InteractiveMode(coding_session)
    host = ui._session_events
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    queued, admit = threading.Event(), threading.Event()
    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))

    async def prepare():
        entered.set()
        try:
            await asyncio.to_thread(release.wait)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def barrier():
        queued.set()
        await asyncio.to_thread(admit.wait)

    monkeypatch.setattr(coding_session, '_maybe_compact', prepare)
    host.start()
    controller = coding_session.runtime.controller
    control = controller.control(barrier, name='review-native-admission-barrier')
    try:
        assert queued.wait(3)
        tickets = [host.enqueue(f'message {i}', (), 'user') for i in range(2)]
        for ticket in tickets:
            ticket.accepted.result(timeout=3)
        admit.set()
        control.result(timeout=3)
        assert entered.wait(3) and len(controller._batch_jobs) == 2
        pending = host.enqueue('pending input restored to draft', (), 'user')
        pending.accepted.result(timeout=3)
        tickets.append(pending)
        ui._cancel_active_work()  # Actual Escape path, not a synthetic controller-only abort.
        assert cancelled.wait(3), 'Native cancellation must reach the actual preparation task'
        release.set()
        results = [ticket.result.result(timeout=5) for ticket in tickets]
        assert all(result.status == 'cancelled' and not result.execution_continues for result in results)
        assert calls == [] and 'pending input restored to draft' in ui._editor.get_text()
        assert {row.status for row in controller.store.records()} == {'cancelled'}
    finally:
        admit.set()
        release.set()
        ui._request_exit()
        host.close()


async def test_maximum_durable_input_survives_host_envelope_and_does_not_poison_following_work(coding_session):
    store = InboxStore(coding_session.session_manager._dir, coding_session.working_dir / 'inbox')
    seed = message(coding_session)
    overhead = len(encoded(message_data(replace(seed, instruction='')), MAX_MESSAGE_BYTES))
    large = replace(seed, instruction='x' * (MAX_MESSAGE_BYTES - overhead))
    assert len(encoded(message_data(large), MAX_MESSAGE_BYTES)) == MAX_MESSAGE_BYTES
    store.enqueue(large)
    store.enqueue(message(coding_session, 'two', 'Follow-up after maximum input'))
    calls = []
    coding_session.agent.set_stream_fn(lambda *args: calls.append(args) or EventStream.empty(
        AssistantMessage(content=(TextContent(text='done'),))))
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        await runtime.activate_inputs()
        def complete():
            rows = store.records(coding_session.session_id, coding_session.working_dir)
            assert not any(row.status == 'failed' for row in rows), [(row.status, row.error) for row in rows]
            assert runtime.controller._broken is None, runtime.controller._broken
            return (all(row.status == 'succeeded' for row in rows) and not runtime.inbox._bindings
                    and not runtime.controller._jobs)
        await wait_for(complete)
        assert runtime.controller._broken is None and len(calls) == 2
        history = runtime.controller.store.records()
        assert len(history) == 2 and {row.status for row in history} == {'succeeded'}
        assert history[0].input.instruction == large.instruction
        assert history[0].input.provenance['inbox']['message_id'] == 'one'
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_partial_reservation_failure_releases_only_unstarted_claims_and_allows_retry(coding_session, monkeypatch):
    store = InboxStore(coding_session.session_manager._dir, coding_session.working_dir / 'inbox')
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    await runtime.activate_inputs()
    original = store._save
    failed = False

    def fail_second(directory, row, path=None):
        nonlocal failed
        if row.status == 'claimed' and row.message.message_id == 'two' and not failed:
            failed = True
            raise OSError('second claim publication failed')
        return original(directory, row, path)

    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(
        AssistantMessage(content=(TextContent(text='done'),))))
    monkeypatch.setattr(store, '_save', fail_second)
    try:
        store.enqueue(message(coding_session, 'one'))
        store.enqueue(message(coding_session, 'two'))
        with pytest.raises(OSError, match='second claim publication'):
            await runtime.inbox.drain()
        assert [row.status for row in store.records(coding_session.session_id,
                                                   coding_session.working_dir)] == ['pending', 'pending']
        assert runtime.controller._broken is None
        await runtime.inbox.drain()
        await wait_for(lambda: all(row.status == 'succeeded' for row in store.records(
            coding_session.session_id, coding_session.working_dir)) and not runtime.inbox._bindings
            and not runtime.controller._jobs)
        assert len(calls) == 1 and not runtime.inbox._bindings
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_operator_cancel_during_claim_handoff_does_not_break_owner(coding_session, monkeypatch):
    store = InboxStore(coding_session.session_manager._dir, coding_session.working_dir / 'inbox')
    store.enqueue(message(coding_session))
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    original = store.reserve
    published, release = threading.Event(), threading.Event()

    def hold(*args, **kwargs):
        result = original(*args, **kwargs)
        published.set()  # Durable claim exists, but no coordinator binding/ticket does yet.
        assert release.wait(5)
        return result

    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(
        AssistantMessage(content=(TextContent(text='done'),))))
    monkeypatch.setattr(store, 'reserve', hold)
    activation = asyncio.create_task(runtime.activate_inputs())
    try:
        await wait_for(published.is_set)
        assert await runtime.inbox.cancel('one', peer_id='inbox-command')
        release.set()
        await activation
        await asyncio.sleep(.05)
        assert store.records(coding_session.session_id, coding_session.working_dir)[0].status == 'cancelled'
        assert runtime.controller._broken is None and calls == []
        store.enqueue(message(coding_session, 'two', 'Still usable after cancellation'))
        await runtime.inbox.drain()
        await wait_for(lambda: store.records(coding_session.session_id, coding_session.working_dir)[1].status
                       == 'succeeded' and not runtime.inbox._bindings)
        assert len(calls) == 1 and not runtime.inbox._bindings
    finally:
        release.set()
        await asyncio.gather(activation, return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


async def test_operator_withdrawal_fences_handoff_before_cancel_io_observer_returns(coding_session, monkeypatch):
    store = InboxStore(coding_session.session_manager._dir, coding_session.working_dir / 'inbox')
    store.enqueue(message(coding_session))
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    claim_published, claim_release = threading.Event(), threading.Event()
    cancel_published, cancel_release = threading.Event(), threading.Event()
    reserve, cancel_pending = store.reserve, store.cancel_pending

    def claimed(*args, **kwargs):
        result = reserve(*args, **kwargs)
        claim_published.set()
        assert claim_release.wait(5)
        return result

    def cancelled(*args, **kwargs):
        result = cancel_pending(*args, **kwargs)
        cancel_published.set()
        assert cancel_release.wait(5)
        return result

    monkeypatch.setattr(store, 'reserve', claimed)
    monkeypatch.setattr(store, 'cancel_pending', cancelled)
    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))
    activation = asyncio.create_task(runtime.activate_inputs())
    cancellation = None
    try:
        await wait_for(claim_published.is_set)
        cancellation = asyncio.create_task(runtime.inbox.cancel('one', peer_id='inbox-command'))
        await wait_for(cancel_published.is_set)
        claim_release.set()  # Admission resumes before the cancel caller receives its disk receipt.
        await activation
        await asyncio.sleep(.05)
        assert runtime.controller._broken is None and not runtime.controller._jobs
        assert calls == [] and not runtime.inbox._bindings
        cancel_release.set()
        assert await cancellation
    finally:
        claim_release.set()
        cancel_release.set()
        await asyncio.gather(activation, *([cancellation] if cancellation else []), return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


@pytest.mark.parametrize('cancel_observer', [False, True])
async def test_failed_reservation_cleanup_is_visible_and_owner_fails_closed(
        coding_session, monkeypatch, cancel_observer):
    from xdog.coding.core.sessions.inbox_store import InboxReservationRollbackError

    store = InboxStore(coding_session.session_manager._dir, coding_session.working_dir / 'inbox')
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    await runtime.activate_inputs()
    store.enqueue(message(coding_session, 'one'))
    store.enqueue(message(coding_session, 'two'))
    original = store._save
    failures = []
    failing, fail_release = threading.Event(), threading.Event()
    if not cancel_observer:
        fail_release.set()
    observed = []
    unsubscribe = runtime.controller.subscribe(observed.append)
    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))

    def fail(directory, row, path=None):
        if row.status == 'claimed' and row.message.message_id == 'two':
            failures.append('publish')
            failing.set()
            assert fail_release.wait(5)
            raise OSError('claim disk error')
        if row.status == 'pending' and row.message.message_id == 'one' and row.attempts:
            failures.append('rollback')
            raise OSError('rollback disk error')
        return original(directory, row, path)

    monkeypatch.setattr(store, '_save', fail)
    draining = None
    try:
        if cancel_observer:
            draining = asyncio.create_task(runtime.inbox.drain())
            await wait_for(failing.is_set)
            runtime.controller.cancel_control(runtime.inbox._draining)
            await asyncio.sleep(.01)
            fail_release.set()
            with pytest.raises(asyncio.CancelledError):
                await draining
        else:
            with pytest.raises(InboxReservationRollbackError, match='rollback failed'):
                await runtime.inbox.drain()
        assert failures == ['publish', 'rollback'] and calls == []
        assert isinstance(runtime.controller._broken, InboxReservationRollbackError)
        assert any(event.kind == 'inbox_error' and 'rollback failed' in event.data for event in observed)
    finally:
        fail_release.set()
        if draining is not None:
            await asyncio.gather(draining, return_exceptions=True)
        unsubscribe()
        monkeypatch.setattr(store, '_save', original)
        await asyncio.wrap_future(runtime.close())


def test_reservation_rollback_cannot_reset_another_running_attempt(coding_session, monkeypatch):
    store = InboxStore(coding_session.session_manager._dir, coding_session.working_dir / 'inbox')
    for key in ('running', 'one', 'two'):
        store.enqueue(message(coding_session, key))
    selected = store.reserve(coding_session.session_id, ('running',), cwd=coding_session.working_dir,
                             branch_id='', generation='owner')
    running, = store.begin_batch(coding_session.session_id, selected, batch_id='running-batch', generation='owner')
    original = store._save

    def fail(directory, row, path=None):
        if row.message.message_id == 'two' and row.status == 'claimed':
            raise OSError('partial claim failure')
        return original(directory, row, path)

    monkeypatch.setattr(store, '_save', fail)
    with pytest.raises(OSError, match='partial claim'):
        store.reserve(coding_session.session_id, ('one', 'two'), cwd=coding_session.working_dir,
                      branch_id='', generation='owner')
    rows = store.records(coding_session.session_id, coding_session.working_dir)
    assert rows[0] == running and [row.status for row in rows] == ['running', 'pending', 'pending']


async def test_unbound_peer_cannot_borrow_the_internal_transport_byte_allowance(coding_session):
    import json

    from xdog.agent.session_input import MAX_BYTES, SessionInput

    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start(setup_pending=True)
    await runtime.activate_inputs()
    body = {'id': 'original', 'origin': 'acp', 'source': 'peer', 'instruction': '', 'evidence': [], 'provenance': {}}
    text = 'x' * (MAX_BYTES - len(json.dumps(body, ensure_ascii=True).encode()))
    forged = SessionInput('not-a-bound-request', 'acp', 'peer', text, (), {
        'inbox': {'message_id': 'original', 'attempt_id': 'fake', 'branch_id': ''}, 'submitted': {}})
    try:
        ticket = runtime.controller.submit(forged, peer_id='foreign')
        with pytest.raises(ValueError, match='byte capacity'):
            await asyncio.wrap_future(ticket.accepted)
        with pytest.raises(ValueError, match='byte capacity'):
            await asyncio.wrap_future(ticket.result)
        assert runtime.controller._broken is None and not runtime.controller._jobs
        good = runtime.controller.submit(
            SessionInput('healthy', 'human', 'coding', 'Still usable', (), {}), peer_id='native')
        assert (await asyncio.wrap_future(good.result)).status == 'succeeded'
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_cancelled_reservation_observer_still_releases_its_committed_claim(coding_session, monkeypatch):
    store = InboxStore(coding_session.session_manager._dir, coding_session.working_dir / 'inbox')
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    await runtime.activate_inputs()
    store.enqueue(message(coding_session))
    original = store.reserve
    published, release = threading.Event(), threading.Event()

    def hold(*args, **kwargs):
        result = original(*args, **kwargs)
        published.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(store, 'reserve', hold)
    draining = asyncio.create_task(runtime.inbox.drain())
    try:
        await wait_for(published.is_set)
        future = runtime.inbox._draining
        assert future is not None
        runtime.controller.cancel_control(future)
        await asyncio.sleep(.01)
        assert not draining.done(), 'Reservation IO must settle before the control releases ownership'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await draining
        row, = store.records(coding_session.session_id, coding_session.working_dir)
        assert row.status == 'pending' and not runtime.inbox._bindings
        assert runtime.controller._broken is None
    finally:
        release.set()
        await asyncio.gather(draining, return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


def test_stale_release_cannot_reset_a_new_attempt_in_the_same_generation(coding_session):
    store = InboxStore(coding_session.session_manager._dir, coding_session.working_dir / 'inbox')
    store.enqueue(message(coding_session))
    previous = store.reserve(coding_session.session_id, ('one',), cwd=coding_session.working_dir,
                             branch_id='', generation='owner')
    store.release_unstarted(coding_session.session_id, ('one',), generation='owner', expected=previous)
    current = store.reserve(coding_session.session_id, ('one',), cwd=coding_session.working_dir,
                            branch_id='', generation='owner')
    store.release_unstarted(coding_session.session_id, ('one',), generation='owner', expected=previous)
    assert store.records(coding_session.session_id, coding_session.working_dir) == current


@pytest.mark.parametrize('claimed', [False, True])
def test_repeated_unstarted_withdrawal_is_idempotent_and_preserves_attempt_facts(coding_session, claimed):
    store = InboxStore(coding_session.session_manager._dir, coding_session.working_dir / 'inbox')
    store.enqueue(message(coding_session))
    if claimed:
        store.reserve(coding_session.session_id, ('one',), cwd=coding_session.working_dir,
                      branch_id='', generation='owner')
    first = store.cancel_pending(coding_session.session_id, 'one', cwd=coding_session.working_dir,
                                 branch_id='', generation='owner')
    second = store.cancel_pending(coding_session.session_id, 'one', cwd=coding_session.working_dir,
                                  branch_id='', generation='owner')
    assert first == second and first.status == 'cancelled'
    assert len(second.attempts) == int(claimed)
    if claimed:
        with pytest.raises(ValueError):
            store.cancel_pending(coding_session.session_id, 'one', cwd=coding_session.working_dir,
                                 branch_id='', generation='wrong-owner')
