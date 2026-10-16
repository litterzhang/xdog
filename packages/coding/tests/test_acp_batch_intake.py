"""Real Unix ACP peers share a durable batch, never a second execution path."""
import asyncio
from contextlib import AsyncExitStack

import pytest
from acp.schema import TextContentBlock
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, TextDeltaEvent
from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.test_acp_ingress import connected


@pytest.mark.parametrize('through_ingress', [False, True])
@pytest.mark.parametrize('fault', ['none', 'cancel_waiter', 'finish_io'])
async def test_unix_prompts_and_enqueue_share_one_durable_batch(
    coding_session, tmp_path, through_ingress, fault, monkeypatch,
):
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.sessions.runtime import SessionRuntime

    entered, release_control, model_started, release_model = (asyncio.Event() for _ in range(4))
    calls = []

    def stream(model, context, options):
        calls.append(tuple(context.messages))
        message = AssistantMessage(content=(TextContent(text='shared answer'),))
        result = asyncio.get_running_loop().create_future()
        result.set_result(message)

        async def events():
            yield StartEvent(partial=AssistantMessage())
            model_started.set()
            await release_model.wait()
            yield TextDeltaEvent(index=0, delta='shared answer', partial=message)
            yield DoneEvent(message=message)

        return EventStream(events(), result)

    coding_session.agent.set_stream_fn(stream)
    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start()
    await runtime.activate_inputs()
    await runtime.open_endpoint()
    ingress = None
    tasks = []

    async def control():
        entered.set()
        await release_control.wait()

    barrier = runtime.controller.control(control, name='test-fixed-batch')
    try:
        await asyncio.wait_for(entered.wait(), 3)
        address = runtime.record.address
        if through_ingress:
            # The existing test fixture uses a short path for the Unix socket limit.
            import tempfile
            from pathlib import Path
            short = tempfile.TemporaryDirectory(prefix='acp-batch-')
            ingress = await IngressServer.start(IngressConfig(coding_session.session_manager._dir,
                runtime.inbox.store.root, Path(short.name) / 'acp.sock'))
            address = ingress.config.address
        async with AsyncExitStack() as stack:
            peers = [await stack.enter_async_context(connected(address)) for _ in range(3)]
            for conn, _, _ in peers:
                await conn.load_session(session_id=coding_session.session_id, cwd=str(tmp_path), mcp_servers=[])
            for index, (conn, _, _) in enumerate(peers[:2]):
                tasks.append(asyncio.create_task(conn.prompt(session_id=coding_session.session_id,
                    prompt=[TextContentBlock(type='text', text=f'prompt-{index}')], xdog={'source': f'peer-{index}'})))
            receipt = await peers[2][0].ext_method('xdog/enqueue', {
                'session_id': coding_session.session_id, 'cwd': str(tmp_path), 'message_id': 'enqueue-one',
                'instruction': 'enqueue', 'source': 'Eyes', 'evidence': [], 'provenance': {},
            })
            assert receipt['status'] == 'queued' and not model_started.is_set()
            async with asyncio.timeout(3):
                while True:
                    rows = runtime.inbox.store.records(coding_session.session_id, tmp_path)
                    if len(rows) == 3 or len(runtime.controller.store.records()) == 2:
                        break
                    await asyncio.sleep(0.01)
            assert len(rows) == 3, 'standard prompts must enter the same durable Inbox as enqueue'
            assert len({row.message.message_id for row in rows}) == 3
            assert all(not task.done() for task in tasks)
            release_control.set()
            await asyncio.wait_for(model_started.wait(), 3)
            assert all(not task.done() for task in tasks)
            if fault == 'cancel_waiter':
                await peers[0][0].cancel(session_id=coding_session.session_id)
                cancelled = await asyncio.wait_for(asyncio.shield(tasks[0]), 2)
                assert cancelled.stop_reason == 'cancelled'
                assert cancelled.field_meta['xdog']['execution_continues'] is True
                assert not tasks[1].done()
            if fault == 'finish_io':
                def fail_finish(*args, **kwargs):
                    raise OSError('terminal fsync failed')
                monkeypatch.setattr(runtime.inbox.store, 'finish', fail_finish)
            release_model.set()
            if fault == 'finish_io':
                from acp import RequestError
                outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
                assert all(isinstance(error, RequestError) and 'fsync failed' in str(error) for error in outcomes)
                rows = runtime.inbox.store.records(coding_session.session_id, tmp_path)
                assert len(rows) == 3 and {row.status for row in rows} == {'running'}
                assert len(calls) == 1
                return
            responses = await asyncio.wait_for(asyncio.gather(*tasks), 5)
            assert responses[1].stop_reason == 'end_turn'
            assert responses[0].stop_reason == ('cancelled' if fault == 'cancel_waiter' else 'end_turn')
            rows = runtime.inbox.store.records(coding_session.session_id, tmp_path)
            assert len(rows) == 3 and {row.status for row in rows} == {'succeeded'}
            assert len({row.batch_id for row in rows}) == 1 and rows[0].batch_id
            assert len({row.request_id for row in rows}) == 3
            assert len(calls) == 1 and len(calls[0]) == 3
            for index, ((_, client, _), response) in enumerate(zip(peers[:2], responses, strict=True)):
                text = ''.join(update.content.text for _, update in client.updates
                               if getattr(update, 'session_update', '') == 'agent_message_chunk')
                assert text == ('' if fault == 'cancel_waiter' and index == 0 else 'shared answer')
                metadata = response.field_meta['xdog']
                assert metadata['batch_id'] == rows[0].batch_id
                assert metadata['message_id'] in {row.message.message_id for row in rows}
                assert metadata['request_id'] in {row.request_id for row in rows}
            assert peers[2][1].updates == []
    finally:
        release_control.set()
        release_model.set()
        await asyncio.wrap_future(barrier)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if ingress is not None:
            await ingress.aclose()
            short.cleanup()
        await asyncio.wrap_future(runtime.close())


@pytest.mark.parametrize('disconnect', [False, True])
async def test_pending_prompt_can_be_cancelled_before_its_admission_control(coding_session, disconnect):
    from xdog.agent.acp.contracts import Peer, SessionRef
    from xdog.coding.acp.backend import CodingBackend
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start()
    await runtime.activate_inputs()
    backend = CodingBackend(runtime.router, runtime.registry, inbox_store=runtime.inbox.store)
    peer = Peer('waiting-peer', object())
    ref = SessionRef(coding_session.session_id, coding_session.working_dir, runtime.controller.generation)
    entered, release = asyncio.Event(), asyncio.Event()
    baseline = len(runtime.controller._listeners)

    async def control():
        entered.set()
        await release.wait()

    async def emit(update):
        raise AssertionError('cancelled pending prompt must not execute')

    barrier = runtime.controller.control(control, name='test-before-admission')
    task = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task = asyncio.create_task(backend.prompt(ref, peer, 'cancel me', {}, emit))
        async with asyncio.timeout(2):
            while not any(name == 'inbox-prompt-intake' for _, name, _ in runtime.controller._controls):
                await asyncio.sleep(0.01)
        if disconnect:
            await asyncio.wait_for(backend.disconnect(peer), 2)
        else:
            await asyncio.wait_for(backend.cancel(ref, peer), 2)
        if disconnect:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            response = await asyncio.wait_for(asyncio.shield(task), 1)
            assert response.stop_reason == 'cancelled'
            assert response.field_meta['xdog']['execution_continues'] is False
        assert {row.status for row in runtime.inbox.store.records(ref.id, ref.cwd)} == {'cancelled'}
        assert len(runtime.controller._listeners) == baseline
        assert all(name != 'inbox-prompt-intake' for _, name, _ in runtime.controller._controls)
    finally:
        release.set()
        await asyncio.wrap_future(barrier)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


async def test_prompt_waits_behind_backlog_instead_of_failing_after_durable_intake(coding_session):
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session, bound=True)
    runtime.controller.max_messages_per_turn = 1
    runtime.start(setup_pending=True)
    target = InboxMessage(coding_session.session_id, coding_session.working_dir,
                          'target', 'target', (), 'test', {})
    runtime.inbox.store.enqueue(InboxMessage(target.session_id, target.cwd, 'older', 'older', (), 'test', {}))
    try:
        prompt = asyncio.create_task(runtime.inbox.submit_prompt(target, peer_id='peer'))
        async with asyncio.timeout(2):
            while len(runtime.inbox.store.records(target.session_id, target.cwd)) < 2:
                await asyncio.sleep(0.01)
        await runtime.activate_inputs()
        ticket = await asyncio.wait_for(prompt, 3)
        assert (await asyncio.wrap_future(ticket.result)).status == 'succeeded'
        assert {row.status for row in runtime.inbox.store.records(target.session_id, target.cwd)} == {'succeeded'}
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_operator_withdrawal_settles_prompt_without_waiting_for_other_turn(coding_session):
    from xdog.agent.acp.contracts import Peer, SessionRef
    from xdog.agent.session_input import SessionInput
    from xdog.coding.acp.backend import CodingBackend
    from xdog.coding.core.sessions.runtime import SessionRuntime

    started, release = asyncio.Event(), asyncio.Event()

    def stream(*args):
        message = AssistantMessage()
        result = asyncio.get_running_loop().create_future()
        result.set_result(message)

        async def events():
            started.set()
            await release.wait()
            yield DoneEvent(message=message)
        return EventStream(events(), result)

    coding_session.agent.set_stream_fn(stream)
    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start()
    await runtime.activate_inputs()
    backend = CodingBackend(runtime.router, runtime.registry, inbox_store=runtime.inbox.store)
    ref = SessionRef(coding_session.session_id, coding_session.working_dir, runtime.controller.generation)
    peer = Peer('peer', object())
    human = runtime.controller.submit(SessionInput('human', 'human', 'Human', 'work', (), {}), peer_id='human')
    task = None

    async def emit(update):
        raise AssertionError('withdrawn input must not execute')

    try:
        await asyncio.wait_for(started.wait(), 2)
        task = asyncio.create_task(backend.prompt(ref, peer, 'withdraw', {}, emit))
        async with asyncio.timeout(2):
            while not any(name == 'inbox-prompt-intake' for _, name, _ in runtime.controller._controls):
                await asyncio.sleep(0.01)
        row, = runtime.inbox.store.records(ref.id, ref.cwd)
        assert await runtime.inbox.cancel(row.message.message_id, peer_id='inbox-command')
        response = await asyncio.wait_for(asyncio.shield(task), 1)
        assert response.stop_reason == 'cancelled'
        assert response.field_meta['xdog']['message_id'] == row.message.message_id
        assert not human.result.done()
    finally:
        release.set()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.wrap_future(human.result)
        await asyncio.wrap_future(runtime.close())


async def test_cancelled_intake_observer_does_not_orphan_shared_control_error(coding_session, monkeypatch):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start(setup_pending=True)
    entered, wrappers = asyncio.Event(), []
    original = asyncio.wrap_future

    def wrap(future, *args, **kwargs):
        receipt = original(future, *args, **kwargs)
        if future is runtime.inbox._draining:
            wrappers.append(receipt)
        return receipt

    async def admit():
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime.inbox, '_admit_pending', admit)
    monkeypatch.setattr(asyncio, 'wrap_future', wrap)
    observer = asyncio.create_task(runtime.inbox._drain(initial=True))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        abandoned, = wrappers
        observer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await observer
        receipt = runtime.inbox._draining
        runtime.controller.cancel_control(receipt)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wrap_future(receipt)
        await asyncio.sleep(0)
        assert abandoned.done()
        assert not abandoned._log_traceback, 'cancelled observer must retrieve the eventual control error'
    finally:
        for receipt in wrappers:
            if receipt.done() and not receipt.cancelled():
                receipt.exception()
        await asyncio.wrap_future(runtime.close())


@pytest.mark.parametrize('rejection', ['capacity', 'branch_changed'])
async def test_rejected_prompt_has_no_admission_or_stranded_lifecycle(coding_session, monkeypatch, rejection):
    from dataclasses import replace

    from xdog.agent.acp.contracts import Peer, SessionRef
    from xdog.coding.acp.backend import CodingBackend
    from xdog.coding.core.sessions import inbox_store
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.runtime import SessionRuntime

    other_branch = coding_session.create_branch(label='other')
    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start(setup_pending=True)
    backend = CodingBackend(runtime.router, runtime.registry, inbox_store=runtime.inbox.store)
    ref = SessionRef(coding_session.session_id, coding_session.working_dir, runtime.controller.generation)
    if rejection == 'capacity':
        monkeypatch.setattr(inbox_store, 'MAX_PENDING', 1)
        runtime.inbox.store.enqueue(InboxMessage(ref.id, ref.cwd, 'existing', 'existing', (), 'test', {}))
    else:
        enqueue = runtime.inbox.store.enqueue

        def change_branch(message, **kwargs):
            saved = coding_session.session_manager.load_session(ref.id)
            coding_session.session_manager.save_session(replace(saved, active_branch=other_branch))
            return enqueue(message, **kwargs)
        monkeypatch.setattr(runtime.inbox.store, 'enqueue', change_branch)

    async def emit(update):
        raise AssertionError('rejected prompt must not run')

    try:
        with pytest.raises(ValueError, match='capacity' if rejection == 'capacity' else 'branch changed'):
            await backend.prompt(ref, Peer('peer', object()), 'rejected', {}, emit)
        assert not backend._running and not runtime.controller._listeners
        assert not runtime.controller._jobs
        assert not runtime.inbox._message_peers and not runtime.inbox._ticket_events
        assert len(runtime.inbox.store.records(ref.id, ref.cwd)) == (1 if rejection == 'capacity' else 0)
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_transport_cancellation_stays_cancelled_when_owner_intake_is_stopping(coding_session):
    from xdog.agent.acp.contracts import Peer, SessionRef
    from xdog.coding.acp.backend import CodingBackend
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start()
    await runtime.activate_inputs()
    backend = CodingBackend(runtime.router, runtime.registry, inbox_store=runtime.inbox.store)
    ref = SessionRef(coding_session.session_id, coding_session.working_dir, runtime.controller.generation)
    entered, release = asyncio.Event(), asyncio.Event()

    async def control():
        entered.set()
        await release.wait()

    async def emit(update):
        raise AssertionError('pending transport must not execute')

    barrier = runtime.controller.control(control, name='test-shutdown-race')
    task = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task = asyncio.create_task(backend.prompt(ref, Peer('peer', object()), 'pending', {}, emit))
        async with asyncio.timeout(2):
            while not any(name == 'inbox-prompt-intake' for _, name, _ in runtime.controller._controls):
                await asyncio.sleep(0.01)
        await runtime.inbox.stop_polling()
        task.cancel()  # The ACP transport closes its sender before cancelling request handlers.
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not backend._running and not runtime.controller._listeners
        row, = runtime.inbox.store.records(ref.id, ref.cwd)
        assert row.status == 'pending'  # Retirement, not a disposed owner, now controls persistence.
    finally:
        release.set()
        await asyncio.wrap_future(barrier)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.wrap_future(runtime.close())
