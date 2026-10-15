"""The terminal is a presentation subscriber, never an execution queue owner."""
import asyncio
import threading
import time

import pytest
from xdog.agent.session_input import SessionInput
from xdog.coding.core.sessions.models import TurnResult
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.utils import strip_ansi


def test_native_submits_all_inputs_without_waiting_for_a_render_tick(coding_session):
    ui = InteractiveMode(coding_session)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    seen = []
    runtime = coding_session.enable_runtime(bound=True)

    async def execute(value):
        seen.append(value.instruction)
        if len(seen) == 1:
            entered.set()
            await asyncio.to_thread(release.wait)
        if len(seen) == 3:
            finished.set()
        return TurnResult('succeeded', None)

    runtime.controller._execute = execute
    try:
        ui._start_turn('first')
        assert entered.wait(3)
        ui._start_turn('second')
        ui._start_turn('third')
        deadline = time.monotonic() + 3
        while len(runtime.controller.store.records()) < 3 and time.monotonic() < deadline:
            time.sleep(.005)
        assert len(runtime.controller.store.records()) == 3
        release.set()
        assert finished.wait(3), 'controller must progress without polling/rendering the TUI'
        assert seen == ['first', 'second', 'third']
        async def settled():
            return None
        runtime.controller.control(settled, name='test-settlement').result(timeout=3)
        ui._poll()
        assert not ui._worker_active
        assert not ui._queue_entries()
    finally:
        release.set()
        ui._request_exit()
        ui._session_events.close()


def test_native_external_turn_uses_same_streaming_presenter(coding_session):
    from xdog.agent import MessageEndEvent, MessageStartEvent
    from xdog.ai.types import AssistantMessage, TextContent

    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    controller = coding_session.runtime.controller
    async def execute(value):
        job = controller._jobs[value.id]
        answer = AssistantMessage(content=(TextContent(text='external answer'),))
        controller._emit('agent_event', job, MessageStartEvent(message=AssistantMessage()))
        controller._emit('agent_event', job, MessageEndEvent(message=answer))
        return TurnResult('succeeded', answer)
    controller._execute = execute
    try:
        controller.human_input()
        ticket = controller.submit(SessionInput('external', 'acp', 'watcher', 'review file', (), {}), peer_id='acp')
        ticket.result.result(timeout=3)
        ui._poll()
        text = '\n'.join(strip_ansi(line) for line in ui._chat_log.render(100))
        assert 'watcher' in text
        assert 'review file' in text
        assert text.count('external answer') == 1
        assert not ui._is_busy
    finally:
        ui._request_exit()
        ui._session_events.close()


def test_terminal_callback_holds_automatic_work_until_its_cleanup(coding_session):
    from xdog.tui.terminal_interception import HeldTerminalInput
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    controller = coding_session.runtime.controller
    entered, release, started = threading.Event(), threading.Event(), threading.Event()

    async def terminal(data, **kwargs):
        entered.set()
        try:
            await asyncio.to_thread(release.wait)
        finally:
            await asyncio.sleep(.01)
        return {'action': 'pass'}

    async def execute(value):
        started.set()
        return TurnResult('succeeded', None)

    coding_session.extensions.terminal_input = terminal
    controller._execute = execute
    ticket = None
    try:
        controller.human_input()
        assert ui._send_terminal_hook(HeldTerminalInput(1, 'owner', 'text', 'prompt', 'key'))
        assert entered.wait(3)
        ticket = controller.submit(SessionInput('external', 'acp', 'watcher', 'review', (), {}), peer_id='acp')
        ticket.accepted.result(timeout=3)
        async def probe():
            return None
        controller._async(probe).result(timeout=3)
        assert not started.is_set()
        release.set()
        assert ticket.result.result(timeout=3).status == 'succeeded'
    finally:
        release.set()
        ui._request_exit()
        ui._session_events.close()


@pytest.mark.parametrize('kind', ['control', 'image'])
def test_child_control_cleanup_holds_automatic_successor(coding_session, monkeypatch, kind):
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    ui._open_children()
    controller = coding_session.runtime.controller
    entered, release, started = threading.Event(), threading.Event(), threading.Event()
    async def child_control(*args, **kwargs):
        entered.set()
        await asyncio.to_thread(release.wait)
        return {'id': 'child', 'accepted_input': {}}
    async def execute(value):
        started.set()
        return TurnResult('succeeded', None)
    monkeypatch.setattr(coding_session.children.supervisor, 'control', child_control)
    controller._execute = execute
    try:
        controller.human_input()
        if kind == 'control':
            ui._child_send('cancel', 'child', '', 'read-only')
        else:
            ui._child_image_send('steer', 'child', '', 'read-only', {}, ())
        assert entered.wait(3)
        ticket = controller.submit(SessionInput('external', 'acp', 'watcher', 'review', (), {}), peer_id='acp')
        ticket.accepted.result(timeout=3)
        async def probe():
            return None
        controller._async(probe).result(timeout=3)
        assert not started.is_set()
        release.set()
        assert ticket.result.result(timeout=3).status == 'succeeded'
    finally:
        release.set()
        ui._request_exit()
        ui._session_events.close()


def test_cancelled_current_input_preserves_its_image_for_explicit_retry(coding_session):
    import base64

    from xdog.tui.attachments import attachment
    image = attachment(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), 'input.png')
    ui = InteractiveMode(coding_session)
    runtime = coding_session.enable_runtime(bound=True)
    entered = threading.Event()
    async def execute(value):
        entered.set()
        await asyncio.Event().wait()
    runtime.controller._execute = execute
    try:
        ticket = ui._session_events.enqueue('inspect', (image,), 'user')
        assert entered.wait(3)
        ui._poll()
        ui._cancel_active_work()
        assert ticket.result.result(timeout=3).status == 'cancelled'
        ui._poll()
        assert tuple(ui._composer.items.values()) == (image,)
    finally:
        ui._request_exit()
        ui._session_events.close()


def test_escape_cancels_external_preparation_even_before_agent_is_streaming(coding_session, monkeypatch):
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    controller = coding_session.runtime.controller
    entered, stopped = threading.Event(), threading.Event()
    async def prepare():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(coding_session, 'initialize_extensions', prepare)
    try:
        controller.human_input()
        ticket = controller.submit(SessionInput('external', 'acp', 'watcher', 'review', (), {}), peer_id='acp')
        assert entered.wait(3)
        assert coding_session.agent.is_idle
        ui._poll()
        ui._cancel_active_work()
        assert ticket.result.result(timeout=3).status == 'cancelled'
        assert stopped.is_set()
    finally:
        ui._request_exit()
        ui._session_events.close()
