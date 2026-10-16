"""Native live and persisted projections share one actual multi-input Agent turn."""
import asyncio
import threading
import time

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.session_presenter import SessionPresenter
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.utils import strip_ansi


def paint(ui):
    deadline = time.monotonic()+3
    while not ui._event_queue.empty():
        assert time.monotonic()<deadline, 'paint every bounded FIFO frame, not just the first eight milliseconds'
        ui._poll()


def rendered(log):
    return '\n'.join(strip_ansi(line) for line in log.render(140))


@pytest.mark.parametrize('external_count', [0, 3])
def test_native_five_inputs_one_answer_live_and_reopened(coding_session, external_count):
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    calls = []

    def stream(model, context, options):
        calls.append(tuple(context.messages))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='ONE-SHARED-BATCH-ANSWER'),)))

    coding_session.agent.set_stream_fn(stream)
    ui = InteractiveMode(coding_session)
    events = ui._session_events
    events.start()
    runtime = coding_session.runtime
    entered, release = threading.Event(), threading.Event()
    local, external = [], []
    observed = []
    unsubscribe = runtime.controller.subscribe(observed.append)

    async def control():
        entered.set()
        await asyncio.to_thread(release.wait)

    barrier = runtime.controller.control(control, name='test-native-batch')
    try:
        assert entered.wait(3)
        for index in range(5 - external_count):
            ticket = events.enqueue(f'native-message-{index}', (), 'user')
            ticket.accepted.result(timeout=3)
            local.append(ticket)
        for index in range(external_count):
            message = InboxMessage(coding_session.session_id, coding_session.working_dir, f'external-{index}',
                                   f'external-message-{index}', (), f'observer-{index}', {})
            external.append(runtime.controller._async(
                lambda message=message: runtime.inbox.submit_prompt(message, peer_id=message.source)))
        if external:
            # A synchronous reader here observes committed data, not scheduled model work.
            deadline = time.monotonic() + 3
            while sum(name == 'inbox-prompt-intake' for _, name, _ in runtime.controller._controls) < external_count:
                assert time.monotonic() < deadline
                time.sleep(0.01)
        release.set()
        barrier.result(timeout=3)
        tickets = local + [future.result(timeout=3) for future in external]
        results = [ticket.result.result(timeout=5) for ticket in tickets]
        paint(ui)
        live = rendered(ui._chat_log)
        assert all(result.status == 'succeeded' for result in results)
        assert len(calls) == 1 and len(calls[0]) == 5
        assert live.count('ONE-SHARED-BATCH-ANSWER') == 1
        for index in range(5 - external_count):
            assert live.count(f'native-message-{index}') == 1
        for index in range(external_count):
            assert live.count(f'external-message-{index}') == 1
            assert f'observer-{index}' in live
        batch_id, = {result.batch_id for result in results}
        assert batch_id and {row.batch_id for row in events.presenter.records.values()} == {batch_id}
        assert runtime.controller.before_batch is not None and runtime.controller.after_batch is not None
        # Repeated delivery cannot repeat any input or the shared stream.
        for event in observed:
            events.receive(event)
        paint(ui)
        assert rendered(ui._chat_log) == live
        saved = coding_session.session_manager.load_session(coding_session.session_id)
        log = ChatLog(create_default_theme())
        presenter = SessionPresenter(log)
        presenter.replay(runtime.controller.store.records(), tuple(saved.messages),
                         runtime.inbox.store.records(coding_session.session_id, coding_session.working_dir))
        reopened = rendered(log)
        assert reopened.count('ONE-SHARED-BATCH-ANSWER') == 1
        for index in range(5 - external_count):
            assert reopened.count(f'native-message-{index}') == 1
        for index in range(external_count):
            assert reopened.count(f'external-message-{index}') == 1
        assert {row.batch_id for row in presenter.records.values()} == {batch_id}
    finally:
        release.set()
        unsubscribe()
        barrier.result(timeout=5)
        ui._request_exit()
        events.close()


@pytest.mark.parametrize('decision', ['deny', 'allow_once'])
def test_native_tool_approval_keeps_batch_identity_when_new_human_input_arrives(coding_session, tmp_path, decision):
    import time

    from xdog.agent import AgentTool, AgentToolResult
    from xdog.ai.types import DoneEvent, StartEvent, ToolCall
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    entered, release = threading.Event(), threading.Event()
    calls = []
    target = tmp_path / 'approved-effect'

    async def execute(tool_call_id, params, cancel=None, on_update=None, ctx=None):
        target.write_text('executed')
        return AgentToolResult(content=(TextContent(text='written'),))

    coding_session.agent.set_tools([AgentTool(name='batch_write', description='write',
        parameters={'type': 'object', 'properties': {}}, execute=execute)])

    def stream(model, context, options):
        calls.append(tuple(context.messages))
        if len(calls) != 1:
            return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
        message = AssistantMessage(content=(ToolCall(id='write', name='batch_write', arguments={}),))
        result = asyncio.get_running_loop().create_future()
        result.set_result(message)

        async def events():
            yield StartEvent(partial=AssistantMessage())
            entered.set()
            await asyncio.to_thread(release.wait)
            yield DoneEvent(message=message)
        return EventStream(events(), result)

    coding_session.agent.set_stream_fn(stream)
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    runtime = coding_session.runtime
    message = InboxMessage(coding_session.session_id, tmp_path, 'callback', 'inspect', (), 'observer', {})
    intake = runtime.controller._async(lambda: runtime.inbox.submit_prompt(message, peer_id='peer'))
    try:
        assert entered.wait(3)
        # The runtime already owns the callback batch, but its input-running UI
        # event has not been painted yet. A human submission changes the UI stamp.
        ui._start_turn('later human input')
        release.set()
        deadline = time.monotonic() + 3
        while not coding_session.permissions.has_pending_requests:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        ui._poll()
        assert ui._permission_prompt is not None, 'approval belongs to the active batch, not the later UI stamp'
        assert ui._permission_prompt.request.tool_name == 'batch_write'
        assert not target.exists()
        ui._permission_prompt._on_decision(decision)
        assert intake.result(timeout=3).result.result(timeout=3).status == 'succeeded'
        assert target.exists() is (decision == 'allow_once')
    finally:
        release.set()
        ui._request_exit()
        ui._session_events.close()


def test_native_batch_keeps_all_attachment_metadata_until_shared_stream_finishes(coding_session, monkeypatch):
    from xdog.agent import MessageStartEvent
    from xdog.ai.types import ImageContent, Model, UserMessage
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
    from xdog.tui.attachments import attachment

    from packages.coding.tests.test_image_composer import PNG

    coding_session.model_info = Model(id='vision', input=('text','image'))
    calls,painted = [],[]
    coding_session.agent.set_stream_fn(lambda _,context,*args: calls.append(context) or EventStream.empty(
        AssistantMessage(content=(TextContent(text='images answered'),))))
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    runtime = coding_session.runtime
    entered,release = threading.Event(),threading.Event()
    first,second = attachment(PNG,'first.png'),attachment(PNG,'second.png')
    dispatch = ui._dispatch_agent_event

    def inspect(event):
        if isinstance(event,MessageStartEvent) and isinstance(event.message,AssistantMessage):
            painted.append(ui._active_images)
        dispatch(event)
    monkeypatch.setattr(ui,'_dispatch_agent_event',inspect)

    async def control():
        entered.set()
        await asyncio.to_thread(release.wait)

    barrier = runtime.controller.control(control,name='test-batch-images')
    try:
        assert entered.wait(2)
        tickets = [ui._session_events.enqueue('first image',(first,),'user'),
                   ui._session_events.enqueue('second image',(second,),'user'),
                   ui._session_events.enqueue('text only',(),'user')]
        for ticket in tickets:
            ticket.accepted.result(timeout=3)
        release.set()
        for ticket in tickets:
            assert ticket.result.result(timeout=3).status=='succeeded'
        paint(ui)
        assert len(calls)==1 and painted==[(first,second)]
        assert sum(isinstance(part,ImageContent) for message in calls[0].messages
                   if isinstance(message,UserMessage) for part in message.content)==2
        assert ui._active_images==()
    finally:
        release.set()
        barrier.result(timeout=3)
        ui._request_exit()
        ui._session_events.close()
