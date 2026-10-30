"""Native queued messages keep their identity when promoted into the active turn."""
from __future__ import annotations

import asyncio
import threading
import time

from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


def until(check, ui=None):
    deadline = time.monotonic() + 8
    while not check():
        if ui is not None:
            ui._poll()
        if time.monotonic() >= deadline:
            raise AssertionError('Native steering did not settle')
        time.sleep(.005)


def held_model(session):
    entered, release = threading.Event(), threading.Event()
    contexts = []

    def stream(model, context, options):
        contexts.append(context)
        response = AssistantMessage(content=(TextContent(text=f'response-{len(contexts)}'),))
        first = len(contexts) == 1

        async def events():
            if first:
                entered.set()
                await asyncio.to_thread(release.wait)
            yield DoneEvent(message=response)

        result = asyncio.get_running_loop().create_future()
        result.set_result(response)
        return EventStream.from_async_generator(events(), result_future=result)

    session.agent.set_stream_fn(stream)
    return entered, release, contexts


def test_escape_promotes_all_native_next_turn_messages_once_without_cancelling(coding_session):
    entered, release, contexts = held_model(coding_session)
    ui = InteractiveMode(coding_session)
    tickets = []
    try:
        ui._start_turn('FIRST')
        assert entered.wait(8)
        ui._poll()
        controller = ui._session_events.runtime.controller
        original_batch = controller._batch.id
        for text in ('SECOND', 'THIRD'):
            ticket = ui._session_events.enqueue(text, (), 'user')
            ticket.accepted.result(timeout=8)
            tickets.append(ticket)
        ui._editor.set_text('UNSENT DRAFT')
        ui._handle_global_input(KeyEvent(key='escape'))
        until(lambda: all(controller._jobs[ticket.id].steering for ticket in tickets), ui)
        ui._poll()
        assert not coding_session.agent.cancellation_requested
        assert ui._editor.get_text() == 'UNSENT DRAFT'
        assert [entry.delivery for entry in ui._queue_entries()] == ['steer', 'steer']
        # Delivery acceptance is not execution: the durable receipt is still queued.
        assert all(row.status == 'queued' for row in controller.store.records() if row.input.id in {
            ticket.id for ticket in tickets})
        release.set()
        results = [ticket.result.result(timeout=8) for ticket in tickets]
        # Backend settlement can precede its UI completion event. Wait for the
        # actual UI owner too, rather than racing a single poll under CI load.
        until(lambda: controller._batch is None and not ui._is_busy, ui)
        ui._poll()
        assert all(result.status == 'succeeded' and result.batch_id == original_batch for result in results)
        assert len(contexts) == 1 + len(tickets)  # existing ONE steering mode; all rounds share one batch
        last = str(contexts[-1].messages)
        assert last.index('SECOND') < last.index('THIRD') and 'UNSENT DRAFT' not in last
        history = str(coding_session.messages)
        assert history.count('SECOND') == history.count('THIRD') == 1
        rows = {row.input.id: row for row in controller.store.records()}
        assert all(rows[ticket.id].status == 'succeeded' and rows[ticket.id].start_order > 0 for ticket in tickets)
        assert all(rows[ticket.id].batch_id == original_batch for ticket in tickets)
        assert not ui._queue_entries() and not ui._is_busy
        rendered = '\n'.join(strip_ansi(row) for row in ui._chat_log.render(80))
        assert rendered.count('SECOND') == rendered.count('THIRD') == 1
    finally:
        release.set()
        ui._request_exit()
        ui._session_events.close()


def test_steering_never_promotes_external_or_wrong_peer_messages(coding_session):
    entered, release, contexts = held_model(coding_session)
    ui = InteractiveMode(coding_session)
    try:
        ui._start_turn('FIRST')
        assert entered.wait(8)
        controller = ui._session_events.runtime.controller
        external = controller.submit(SessionInput('external', 'automatic', 'goal', 'EXTERNAL', (), {}),
                                     peer_id='acp', input_origin='session_input')
        external.accepted.result(timeout=8)
        assert controller.promote_pending((external.id,), peer_id='native').result(timeout=8) == 0
        assert controller.promote_pending((external.id,), peer_id='acp').result(timeout=8) == 0
        assert not controller._jobs[external.id].steering
        release.set()
        assert external.result.result(timeout=8).status == 'succeeded'
        assert len(contexts) == 2
    finally:
        release.set()
        ui._request_exit()
        ui._session_events.close()


def test_promoted_input_checkpoint_failure_stops_before_next_model(coding_session, monkeypatch):
    entered, release, contexts = held_model(coding_session)
    ui = InteractiveMode(coding_session)
    try:
        ui._start_turn('FIRST')
        assert entered.wait(8)
        ui._poll()
        controller = ui._session_events.runtime.controller
        ticket = ui._session_events.enqueue('SECOND', (), 'user')
        ticket.accepted.result(timeout=8)
        transition = controller.store.transition_batch

        async def fail(ids, status, **kwargs):
            if ticket.id in ids and status == 'running':
                raise OSError('steering checkpoint failed')
            await transition(ids, status, **kwargs)

        monkeypatch.setattr(controller.store, 'transition_batch', fail)
        assert controller.promote_pending((ticket.id,), peer_id='native').result(timeout=8) == 1
        release.set()
        result = ticket.result.result(timeout=8)
        assert result.status == 'failed' and len(contexts) == 1
        assert any(row.input.id == ticket.id and row.status == 'failed' for row in controller.store.records())
    finally:
        release.set()
        ui._request_exit()
        ui._session_events.close()


def test_escape_does_not_promote_while_permission_owner_is_active(coding_session, monkeypatch):
    ui = InteractiveMode(coding_session)
    try:
        ui._set_busy(True)
        ui._permission_prompt = object()
        promoted = []
        monkeypatch.setattr(ui._session_events, 'promote_pending', lambda: promoted.append(True))
        ui._handle_escape()
        assert not promoted
    finally:
        ui._permission_prompt = None
        ui._set_busy(False)
        ui._request_exit()
        ui._session_events.close()


def test_checkpoint_commit_is_awaited_before_steered_model_request(coding_session, monkeypatch):
    entered, release, contexts = held_model(coding_session)
    checkpoint_entered, checkpoint_release = threading.Event(), threading.Event()
    ui = InteractiveMode(coding_session)
    try:
        ui._start_turn('FIRST')
        assert entered.wait(8)
        controller = ui._session_events.runtime.controller
        ticket = ui._session_events.enqueue('SECOND', (), 'user')
        ticket.accepted.result(timeout=8)
        transition = controller.store.transition_batch

        async def held_transition(ids, status, **kwargs):
            if ticket.id in ids and status == 'running':
                checkpoint_entered.set()
                await asyncio.to_thread(checkpoint_release.wait)
            await transition(ids, status, **kwargs)

        monkeypatch.setattr(controller.store, 'transition_batch', held_transition)
        assert controller.promote_pending((ticket.id,), peer_id='native').result(timeout=8) == 1
        assert controller.promote_pending((ticket.id,), peer_id='native').result(timeout=8) == 0
        release.set()
        assert checkpoint_entered.wait(8)
        assert len(contexts) == 1 and not ticket.result.done()
        row = next(row for row in controller.store.records() if row.input.id == ticket.id)
        assert row.status == 'queued' and row.start_order == 0
        checkpoint_release.set()
        assert ticket.result.result(timeout=8).status == 'succeeded'
        assert len(contexts) == 2
    finally:
        release.set()
        checkpoint_release.set()
        ui._request_exit()
        ui._session_events.close()


def test_promoted_queued_message_keeps_its_image_bytes(coding_session):
    from xdog.ai.types import ImageContent, Model
    from xdog.tui.attachments import attachment

    from packages.coding.tests.test_child_image_submission import PNG

    entered, release, contexts = held_model(coding_session)
    ui = InteractiveMode(coding_session)
    try:
        ui._start_turn('FIRST')
        assert entered.wait(8)
        coding_session.model_info = Model(id='test', context_window=128000, input=('text', 'image'))
        picture = attachment(PNG, 'queued.png')
        ticket = ui._session_events.enqueue('IMAGE SECOND', (picture,), 'user')
        ticket.accepted.result(timeout=8)
        controller = ui._session_events.runtime.controller
        assert controller.promote_pending((ticket.id,), peer_id='native').result(timeout=8) == 1
        release.set()
        assert ticket.result.result(timeout=8).status == 'succeeded'
        assert len(contexts) == 2
        images = [part for message in contexts[-1].messages if message.role == 'user'
                  and not isinstance(message.content, str)
                  for part in message.content if isinstance(part, ImageContent)]
        import base64
        assert len(images) == 1 and base64.b64decode(images[0].data) == PNG
        assert images[0].mime_type == 'image/png'
    finally:
        release.set()
        ui._request_exit()
        ui._session_events.close()


def test_escape_release_or_repeat_cannot_promote_or_cancel(coding_session, monkeypatch):
    from xdog.tui.keys import KeyEventType

    ui = InteractiveMode(coding_session)
    called = []
    monkeypatch.setattr(ui, '_handle_escape', lambda: called.append(True))
    try:
        ui._set_busy(True)
        for kind in (KeyEventType.RELEASE, KeyEventType.REPEAT):
            ui._handle_global_input(KeyEvent(key='escape', event_type=kind))
        assert not called
        ui._handle_global_input(KeyEvent(key='escape', event_type=KeyEventType.PRESS))
        assert called == [True]
    finally:
        ui._set_busy(False)
        ui._request_exit()
        ui._session_events.close()
