"""The sole controller selects fixed batches and keeps per-message execution facts."""
import asyncio

import pytest
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.sessions.models import TurnResult


def value(identifier, origin='human', instruction=None):
    return SessionInput(identifier, origin, 'batch test', instruction or identifier, (), {})


async def admit(controller, values, peers=None, on_accepted=None):
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold():
        entered.set()
        await release.wait()

    action = controller.control(hold, name='test-admission-barrier')
    await asyncio.wait_for(entered.wait(), 3)
    try:
        tickets = [controller.submit(item, peer_id=peers[i] if peers else 'test') for i, item in enumerate(values)]
        await asyncio.wait_for(asyncio.gather(*(asyncio.wrap_future(t.accepted) for t in tickets)), 3)
        if on_accepted is not None:
            on_accepted(tickets)
        return tickets
    finally:
        release.set()
        await asyncio.wrap_future(action)


async def outcomes(tickets):
    return await asyncio.wait_for(asyncio.gather(*(asyncio.wrap_future(t.result) for t in tickets)), 3)


async def test_five_queued_messages_reach_the_actual_agent_as_five_inputs_in_one_turn(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    calls = []

    def stream(model, context, options):
        calls.append(tuple(context.messages))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='one shared answer'),)))

    coding_session.agent.set_stream_fn(stream)
    controller = SessionController(coding_session, bound=True)
    events = []
    controller.subscribe(events.append)
    try:
        tickets = await admit(controller, [value(f'message{i}') for i in range(1, 6)])
        results = await outcomes(tickets)
        assert len(calls) == 1
        assert [m.content for m in calls[0] if isinstance(m, UserMessage)] == [
            (TextContent(text='message1'),), (TextContent(text='message2'),), (TextContent(text='message3'),),
            (TextContent(text='message4'),), (TextContent(text='message5'),)]
        assert len({r.batch_id for r in results}) == 1 and results[0].batch_id
        assert [r.status for r in results] == ['succeeded'] * 5
        rows = controller.store.records()
        assert [r.input.id for r in rows] == [t.id for t in tickets]
        assert {row.batch_id for row in rows} == {results[0].batch_id}
        shared = [e for e in events if e.kind == 'agent_event']
        assert shared and all(e.request_ids == tuple(t.id for t in tickets) for e in shared)
        assert all(not e.request_id for e in shared)  # Shared output is not attributed to one arbitrary input.
    finally:
        await asyncio.wrap_future(controller.close())


@pytest.mark.parametrize('limit,expected', [(3, [('one', 'two', 'three'), ('four', 'five')]),
                                           (1, [('one',), ('two',), ('three',), ('four',), ('five',)])])
async def test_count_limit_selects_ordered_batches_without_human_credit(coding_session, limit, expected):
    from xdog.coding.core.sessions.controller import SessionController

    seen = []

    async def execute(batch):
        seen.append(batch.request_ids)
        return TurnResult('succeeded', None)

    controller = SessionController(coding_session, bound=True, max_messages_per_turn=limit, execute_batch=execute)
    try:
        tickets = await admit(controller, [value(name, 'acp') for name in ('one', 'two', 'three', 'four', 'five')])
        assert all(result.status == 'succeeded' for result in await outcomes(tickets))
        assert seen == expected
    finally:
        await asyncio.wrap_future(controller.close())


async def test_arrivals_after_batch_selection_wait_for_next_batch(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def execute(batch):
        seen.append(batch.request_ids)
        if len(seen) == 1:
            entered.set()
            await release.wait()
        return TurnResult('succeeded', None)

    controller = SessionController(coding_session, bound=False, execute_batch=execute)
    try:
        first = await admit(controller, [value('one'), value('two')])
        await asyncio.wait_for(entered.wait(), 3)
        late = controller.submit(value('late'), peer_id='test')
        await asyncio.wrap_future(late.accepted)
        assert seen == [('one', 'two')]
        release.set()
        await outcomes([*first, late])
        assert seen == [('one', 'two'), ('late',)]
    finally:
        release.set()
        await asyncio.wrap_future(controller.close())


async def test_human_priority_and_fifo_are_preserved_inside_selected_batch(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    seen = []

    async def execute(batch):
        seen.append(batch.request_ids)
        return TurnResult('succeeded', None)

    controller = SessionController(coding_session, bound=True, execute_batch=execute)
    try:
        tickets = await admit(controller, [value('auto1', 'acp'), value('human1'),
                                           value('auto2', 'acp'), value('human2')])
        await outcomes(tickets)
        assert seen == [('human1', 'human2', 'auto1', 'auto2')]
    finally:
        await asyncio.wrap_future(controller.close())


async def test_active_shared_batch_peer_cancel_detaches_only_its_waiter(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(batch):
        entered.set()
        await release.wait()
        return TurnResult('succeeded', None)

    controller = SessionController(coding_session, bound=False, execute_batch=execute)
    try:
        first, second = await admit(controller, [value('one'), value('two')], peers=('a', 'b'))
        await asyncio.wait_for(entered.wait(), 3)
        assert not await asyncio.wrap_future(controller.cancel(first.id, peer_id='b'))
        assert await asyncio.wrap_future(controller.cancel(first.id, peer_id='a', wait=True))
        detached = await asyncio.wrap_future(first.result)
        assert detached.execution_continues and detached.status == 'cancelled'
        assert not second.result.done()
        assert [row.status for row in controller.store.records()] == ['running', 'running']
        release.set()
        assert (await outcomes([second]))[0].status == 'succeeded'
        assert [row.status for row in controller.store.records()] == ['succeeded', 'succeeded']
    finally:
        release.set()
        await asyncio.wrap_future(controller.close())


async def test_local_abort_waits_for_cleanup_and_cancels_entire_batch(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    entered, cleaning, settle = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(batch):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await settle.wait()
            raise

    controller = SessionController(coding_session, bound=False, execute_batch=execute)
    try:
        tickets = await admit(controller, [value('one'), value('two')])
        await asyncio.wait_for(entered.wait(), 3)
        aborted = controller.abort()
        await asyncio.wait_for(cleaning.wait(), 3)
        assert not aborted.done() and controller.busy
        settle.set()
        await asyncio.wrap_future(aborted)
        assert [r.status for r in await outcomes(tickets)] == ['cancelled', 'cancelled']
    finally:
        settle.set()
        await asyncio.wrap_future(controller.close())


async def test_last_member_terminal_failure_reports_failure_to_all_waiters(coding_session, monkeypatch):
    from xdog.coding.core.sessions.controller import SessionController

    calls = []

    async def execute(batch):
        calls.append(batch.request_ids)
        return TurnResult('succeeded', AssistantMessage(content=(TextContent(text='actual partial answer'),)))

    controller = SessionController(coding_session, bound=False, execute_batch=execute)
    original = controller.store._commit

    def fail(records):
        if any(row.status == 'succeeded' for row in records):
            raise OSError('terminal batch fsync failed')
        original(records)

    monkeypatch.setattr(controller.store, '_commit', fail)
    try:
        tickets = await admit(controller, [value('one'), value('two')])
        results = await outcomes(tickets)
        assert calls == [('one', 'two')]
        assert [r.status for r in results] == ['failed', 'failed']
        assert all(isinstance(r.cause, OSError) and 'fsync' in r.error for r in results)
        assert controller._broken is not None
    finally:
        await asyncio.wrap_future(controller.close())


async def test_aggregate_bytes_split_without_merging_or_truncating_messages(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    seen = []

    async def execute(batch):
        seen.append(tuple(item.value.instruction for item in batch.items))
        return TurnResult('succeeded', None)

    controller = SessionController(coding_session, bound=False, execute_batch=execute)
    try:
        texts = ('a' * 600000, 'b' * 600000)
        tickets = await admit(controller, [value('one', instruction=texts[0]), value('two', instruction=texts[1])])
        await outcomes(tickets)
        assert seen == [(texts[0],), (texts[1],)]
    finally:
        await asyncio.wrap_future(controller.close())


async def test_repeated_singleton_cancel_waits_for_the_same_actual_cleanup(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    entered, cleaning, settle = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(batch):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await settle.wait()
            raise

    controller = SessionController(coding_session, bound=False, execute_batch=execute)
    first = second = None
    try:
        ticket = (await admit(controller, [value('one')]))[0]
        await asyncio.wait_for(entered.wait(), 3)
        first = controller.cancel(ticket.id, peer_id='test', wait=True)
        await asyncio.wait_for(cleaning.wait(), 3)
        second = controller.cancel(ticket.id, peer_id='test', wait=True)
        await asyncio.sleep(0.02)
        assert not first.done() and not second.done(), 'a second cancel cannot shortcut actual producer cleanup'
        settle.set()
        assert await asyncio.wrap_future(first) and await asyncio.wrap_future(second)
        assert (await outcomes([ticket]))[0].status == 'cancelled'
    finally:
        settle.set()
        await asyncio.wrap_future(controller.close())


async def test_partial_running_publication_never_starts_batch(coding_session, tmp_path, monkeypatch):
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = InboxStore(coding_session.session_manager._dir, tmp_path / 'inbox')
    for key in ('one', 'two'):
        store.enqueue(InboxMessage(coding_session.session_id, tmp_path, key, key, (), 'external', {}))
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    calls, hooks, completed = [], [], []
    done = asyncio.Event()
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))

    async def before(batch):
        hooks.append('before')

    async def after(batch, result):
        hooks.append('after')

    def observe(event):
        if event.kind == 'input_completed':
            completed.append(event.data)
            if len(completed) == 2:
                done.set()

    runtime.controller.before_batch, runtime.controller.after_batch = before, after
    runtime.controller.subscribe(observe)
    save = store._save

    def fail(directory, record, old=None):
        if record.status == 'running' and record.message.message_id == 'two':
            raise OSError('last member running fsync failed')
        return save(directory, record, old)

    monkeypatch.setattr(store, '_save', fail)
    try:
        await runtime.activate_inputs()
        await asyncio.wait_for(done.wait(), 3)
        assert calls == [] and hooks == []
        assert [result.status for result in completed] == ['failed', 'failed']
        assert [row.status for row in store.records(coding_session.session_id, tmp_path)] == ['failed', 'failed']
    finally:
        await asyncio.wrap_future(runtime.close())


@pytest.mark.parametrize('invalid', [True, 0, -1, 129])
async def test_invalid_count_limit_is_rejected_at_controller_construction(coding_session, invalid):
    from xdog.coding.core.sessions.controller import SessionController

    with pytest.raises(ValueError):
        SessionController(coding_session, bound=True, max_messages_per_turn=invalid)


@pytest.mark.parametrize('constraint', ['context', 'images'])
async def test_known_model_limits_reduce_batch_without_dropping_members(coding_session, constraint):
    from xdog.ai.types import ImageContent, Model, VisionLimits
    from xdog.coding.core.sessions.controller import SessionController

    seen = []

    async def execute(batch):
        seen.append(batch.request_ids)
        return TurnResult('succeeded', None)

    coding_session.agent.set_tools([])
    if constraint == 'context':
        coding_session.context_window = 1000
        coding_session.max_prompt_tokens = 600
        coding_session.agent.set_model_capabilities(context_window=1000, max_prompt_tokens=600)
        coding_session.model_info = Model(id='small', context_window=1000, max_prompt_tokens=600, max_tokens=100)
        items = [value('one', instruction='a' * 1600), value('two', instruction='b' * 1600)]
        image = None
    else:
        coding_session.model_info = Model(id='vision', input=('text', 'image'),
                                          vision_limits=VisionLimits(max_prompt_images=1))
        image = ImageContent(data=('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE'
                                   'QVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), mime_type='image/png')
        items = [value('one'), value('two')]
    controller = SessionController(coding_session, bound=False, execute_batch=execute)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold():
        entered.set()
        await release.wait()

    action = controller.control(hold, name='model-limit-admission')
    await asyncio.wait_for(entered.wait(), 3)
    try:
        tickets = [controller.submit(item, peer_id='test', images=(image,) if image else ()) for item in items]
        await asyncio.gather(*(asyncio.wrap_future(t.accepted) for t in tickets))
        release.set()
        await asyncio.wrap_future(action)
        await outcomes(tickets)
        assert seen == [('one',), ('two',)]
    finally:
        release.set()
        await asyncio.wrap_future(controller.close())


async def test_malformed_attachment_cannot_poison_following_queue_work(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    controller = SessionController(coding_session, bound=False)
    try:
        bad = controller.submit(value('bad'), peer_id='test', images=(object(),))
        with pytest.raises((ValueError, TypeError)):
            await asyncio.wrap_future(bad.result)
        if bad.accepted.done():
            try:
                bad.accepted.result()
            except (ValueError, TypeError):
                pass
        good = controller.submit(value('good'), peer_id='test')
        assert (await outcomes([good]))[0].status == 'succeeded'
        assert not any(row.input.id == 'bad' for row in controller.store.records())
    finally:
        await asyncio.wrap_future(controller.close())


@pytest.mark.parametrize('bad_id', [True, 3, 'bad\x00batch'])
async def test_corrupt_saved_batch_id_is_not_accepted_as_history(coding_session, bad_id):
    import json

    from xdog.coding.core.sessions.controller import SessionController
    from xdog.coding.core.sessions.store import InputStore

    controller = SessionController(coding_session, bound=False)
    try:
        await outcomes([controller.submit(value('one'), peer_id='test')])
        data = json.loads(controller.store.path.read_text())
        data[0]['batch_id'] = bad_id
        controller.store.path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match='batch|history'):
            InputStore(coding_session)
    finally:
        await asyncio.wrap_future(controller.close())


def test_default_controller_reads_the_current_owners_effective_limit(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    controller = SessionController(coding_session, bound=True)
    assert controller.max_messages_per_turn == 10
    coding_session.settings.set_session_max_messages_per_turn(3)
    assert controller.max_messages_per_turn == 3
    explicit = SessionController(coding_session, bound=True, max_messages_per_turn=1)
    assert explicit.max_messages_per_turn == 1


@pytest.mark.parametrize('observer', ['event', 'result'])
async def test_passive_observers_cannot_inherit_execution_owner_authority(coding_session, observer):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session, bound=False)
    runtime.start()
    refusals = []

    def mutate():
        try:
            coding_session.set_thinking_level('high')
        except RuntimeError as exc:
            refusals.append(str(exc))

    def receive(event):
        if event.kind == 'input_running':
            mutate()

    if observer == 'event':
        runtime.controller.subscribe(receive)
    try:
        callback = ((lambda tickets: tickets[0].result.add_done_callback(lambda _: mutate()))
                    if observer == 'result' else None)
        tickets = await admit(runtime.controller, [value('one')], on_accepted=callback)
        await outcomes(tickets)
        assert refusals and 'owner' in refusals[0]
        assert coding_session.agent.options.thinking is None
    finally:
        await asyncio.wrap_future(runtime.close())


@pytest.mark.parametrize('initial', ['succeeded', 'failed'])
async def test_close_during_batch_finalization_keeps_batch_identity_and_releases_owner(coding_session, initial):
    from xdog.coding.core.sessions.controller import SessionController

    entered, cleaning, settle = asyncio.Event(), asyncio.Event(), asyncio.Event()
    batches = []

    async def execute(batch):
        batches.append(batch.id)
        return TurnResult(initial, None, 'known batch failure' if initial == 'failed' else '')

    async def after(batch, outcome):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await settle.wait()
            raise

    controller = SessionController(coding_session, bound=False, execute_batch=execute)
    controller.after_batch = after
    try:
        tickets = await admit(controller, [value('one'), value('two')])
        await asyncio.wait_for(entered.wait(), 3)
        closing = controller.close()
        await asyncio.wait_for(cleaning.wait(), 3)
        assert not closing.done() and controller.busy
        settle.set()
        await asyncio.wait_for(asyncio.wrap_future(closing), 3)
        results = await outcomes(tickets)
        assert not controller.busy and controller._owner_token is None
        assert [row.batch_id for row in controller.store.records()] == [batches[0], batches[0]]
        assert all(result.batch_id == batches[0] for result in results)
        assert all(result.status == ('failed' if initial == 'failed' else 'cancelled') for result in results)
        if initial == 'failed':
            assert all(result.error == 'known batch failure' for result in results)
    finally:
        settle.set()
        await asyncio.wrap_future(controller.close())


def test_controller_exposes_batch_hooks_without_representative_input_fallback(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    controller = SessionController(coding_session,bound=True)
    assert hasattr(controller,'before_batch') and hasattr(controller,'after_batch')
    assert not hasattr(controller,'before_turn') and not hasattr(controller,'after_turn')
