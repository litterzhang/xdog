"""Opening and observing an inbox is presentation, not an executable replay queue."""

import asyncio
from dataclasses import replace

from xdog.coding.core.sessions.models import InputRecord, SessionEvent
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.utils import strip_ansi


def text(log):
    return '\n'.join(strip_ansi(line) for line in log.render(100))


def pending(coding_session,tmp_path):
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore

    store = InboxStore(coding_session.session_manager._dir,tmp_path/'inbox')
    message = InboxMessage(coding_session.session_id,tmp_path,'m1','Inspect late.py',({'path':'late.py'},),'Eyes',{})
    store.enqueue(message)
    return store,message


async def test_capture_history_includes_pending_inbox_but_never_starts_model(coding_session,tmp_path):
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.interactive.session_presenter import SessionPresenter, capture_history

    store,message = pending(coding_session,tmp_path)
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    try:
        snapshot = await capture_history(coding_session)
        assert snapshot.inbox[0].status=='pending_review'
        log = ChatLog(create_default_theme())
        presenter = SessionPresenter(log)
        presenter.replay(snapshot.records,snapshot.messages,snapshot.inbox)
        output = text(log)
        assert 'Inspect late.py' in output and 'late.py' in output and 'Eyes' in output
        assert 'pending_review' in output and 'not a human' in output
        assert not coding_session.messages and not runtime.controller.store.records()
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_native_event_queue_accepts_complete_frozen_inbox_snapshot(coding_session,tmp_path):
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.interactive.session_presenter import capture_history
    from xdog.tui.event_queue import EventQueue, thaw_event

    store,message = pending(coding_session,tmp_path)
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    try:
        snapshot = await capture_history(coding_session)
        queue = EventQueue()
        queue.put({'history':snapshot})
        decoded = thaw_event(queue.get_nowait())['history']
        assert decoded.inbox[0].message.cwd==tmp_path
        assert decoded.inbox[0].message.evidence==({'path':'late.py'},)
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_pending_component_is_joined_with_canonical_attempt_without_duplicate_input(coding_session,tmp_path):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.interactive.session_presenter import SessionPresenter

    store,message = pending(coding_session,tmp_path)
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    controller = runtime.controller
    log = ChatLog(create_default_theme())
    presenter = SessionPresenter(log)
    try:
        row = (await runtime.inbox.snapshot())[0]
        presenter.apply(SessionEvent(message.session_id,controller.generation,'','inbox_snapshot',(row,)))
        approved = store.approve(
            message.session_id,('m1',),cwd=tmp_path,branch_id='',generation=controller.generation)[0]
        value = SessionInput(approved.request_id,'acp','Eyes',message.instruction,message.evidence,
                             {'inbox':{'message_id':'m1','attempt_id':approved.attempt_id},'submitted':{}})
        record = InputRecord(value,'',controller.generation,'paused')
        presenter.apply(SessionEvent(message.session_id,controller.generation,value.id,'input_paused',record))
        presenter.apply(SessionEvent(message.session_id,controller.generation,'','inbox_snapshot',(approved,)))
        presenter.apply(SessionEvent(message.session_id,controller.generation,'','inbox_snapshot',(approved,)))
        output = text(log)
        assert output.count('Inspect late.py')==1
        assert 'm1' in output and approved.attempt_id in output
        assert 'approved' in output and 'paused' in output
        assert len(log.detail_records())==1
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_untrusted_labels_are_safe_and_full_evidence_is_in_details(coding_session,tmp_path):
    from xdog.coding.modes.interactive.session_presenter import SessionPresenter

    store,message = pending(coding_session,tmp_path)
    row = store.records(message.session_id,tmp_path)[0]
    evidence = tuple({'path':f'file-{i}','data':'x'*2000} for i in range(50))
    row = replace(row,message=replace(row.message,source='Eyes\x1b[2J\nStatus: forged',evidence=evidence))
    log = ChatLog(create_default_theme())
    presenter = SessionPresenter(log)
    presenter.replay((),(),(row,))
    output = text(log)
    assert len(output)<12000 and '\x1b' not in output
    assert '\nStatus: forged' not in output
    assert 'file-49' in log.detail_records()[0].body


def test_native_late_enqueue_refreshes_display_without_starting_an_agent(coding_session,tmp_path):
    import time

    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    store,message = pending(coding_session,tmp_path)
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    try:
        ui._replay_history()
        assert 'Inspect late.py' in text(ui._chat_log)
        store.enqueue(replace(message,message_id='m2',instruction='Second late message'))
        deadline = time.monotonic()+4
        while 'Second late message' not in text(ui._chat_log):
            assert time.monotonic()<deadline
            ui._poll()
            time.sleep(0.02)
        assert not coding_session.messages
    finally:
        ui._request_exit()
        ui._session_events.close()


def test_native_review_after_a_turn_still_displays_the_current_permission_request(coding_session,tmp_path):
    import time

    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    store,message = pending(coding_session,tmp_path)
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    try:
        ticket = ui._session_events.enqueue('Hello',(),'user')
        ticket.result.result(timeout=3)
        ui._poll()
        ui._handle_slash_command('inbox','approve m1')
        deadline = time.monotonic()+4
        while ui._permission_prompt is None:
            assert time.monotonic()<deadline, 'review permission request was lost under a previous turn stamp'
            ui._poll()
            time.sleep(0.01)
        assert ui._permission_prompt.request.tool_name=='session_inbox_approve'
        coding_session.permissions.resolve(ui._permission_prompt.request.id,'deny')
    finally:
        ui._request_exit()
        if ui._command_thread is not None:
            ui._command_thread.join(timeout=3)
        ui._session_events.close()


async def test_late_poll_snapshot_cannot_rewind_a_completed_attempt(coding_session,tmp_path,monkeypatch):
    import threading

    from xdog.coding.core.sessions.runtime import SessionRuntime

    store,message = pending(coding_session,tmp_path)
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    await runtime.inbox.snapshot()
    coding_session.permissions.set_mode('allow-all')
    entered,release = threading.Event(),threading.Event()
    block = [True]
    original = store.records
    events = []
    runtime.controller.subscribe(events.append)

    def delayed(*args):
        rows = original(*args)
        if block[0]:
            block[0] = False
            entered.set()
            release.wait(4)
        return rows

    monkeypatch.setattr(store,'records',delayed)
    stale = asyncio.create_task(runtime.inbox.snapshot())
    try:
        assert await asyncio.to_thread(entered.wait,3)
        await runtime.inbox.approve(('m1',),peer_id='reviewer')
        async with asyncio.timeout(3):
            while runtime.controller.store.records()[-1].status!='succeeded':
                await asyncio.sleep(0.01)
        release.set()
        latest = await stale
        assert latest[0].status=='succeeded', 'an old polling read must not replace a newer durable result'
        snapshots = [e for e in events if e.kind=='inbox_snapshot']
        assert snapshots[-1].data[0].status=='succeeded'
    finally:
        release.set()
        await asyncio.gather(stale,return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


def test_replay_replaces_old_pending_inbox_components_without_touching_other_notices(coding_session,tmp_path):
    from xdog.coding.modes.interactive.session_presenter import SessionPresenter

    store,message = pending(coding_session,tmp_path)
    row = store.records(message.session_id,tmp_path)[0]
    log = ChatLog(create_default_theme())
    log.add_system('Unrelated notice')
    presenter = SessionPresenter(log)
    presenter.replay((),(),(row,))
    presenter.replay((),(),())
    assert 'Inspect late.py' not in text(log)
    assert 'Unrelated notice' in text(log)


def test_native_inbox_read_does_not_wait_for_a_busy_model(coding_session,tmp_path):
    import threading
    import time

    from xdog.coding.core.sessions.models import TurnResult
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    pending(coding_session,tmp_path)
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    controller = coding_session.runtime.controller
    entered,release = threading.Event(),threading.Event()

    async def execute(item):
        entered.set()
        await asyncio.to_thread(release.wait,4)
        return TurnResult('succeeded',None)

    controller._execute = execute
    try:
        ticket = ui._session_events.enqueue('A running turn',(),'user')
        assert entered.wait(3)
        ui._poll()
        ui._handle_slash_command('inbox','list')
        deadline = time.monotonic()+1
        while ui._command is not None:
            assert time.monotonic()<deadline, 'read-only inbox inspection must not queue behind model execution'
            ui._poll()
            time.sleep(0.01)
        assert not ticket.result.done()
        assert 'm1' in text(ui._chat_log)
    finally:
        release.set()
        ui._request_exit()
        if ui._command_thread is not None:
            ui._command_thread.join(timeout=3)
        ui._session_events.close()


def test_native_inbox_query_is_not_a_new_human_wake_credit(coding_session,tmp_path):
    import threading
    import time

    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.models import TurnResult
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    pending(coding_session,tmp_path)
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    runtime = coding_session.runtime
    started = threading.Event()

    async def execute(value):
        started.set()
        return TurnResult('succeeded',None)

    async def barrier():
        await asyncio.sleep(0)

    runtime.controller._execute = execute
    ticket = runtime.controller.submit(
        SessionInput('older-query','acp','Other','Unapproved backlog',(),{}),peer_id='other')
    ticket.accepted.result(timeout=3)
    try:
        ui._handle_submit('/inbox list')
        deadline = time.monotonic()+3
        while ui._command is not None:
            assert time.monotonic()<deadline
            ui._poll()
            time.sleep(0.01)
        ui._poll()
        runtime.controller._async(barrier).result(timeout=3)
        assert runtime.controller.current_request is None and not started.is_set(), (
            'inbox inspection cannot select an automatic task while its result is still being persisted')
        assert not ticket.result.done(), 'read-only inbox commands must not grant automatic execution credit'
        assert not coding_session.messages
    finally:
        ui._request_exit()
        if ui._command_thread is not None:
            ui._command_thread.join(timeout=3)
        ui._session_events.close()


def test_rpc_evidence_preview_reports_truncation_of_a_single_large_item(coding_session,tmp_path):
    from xdog.coding.core.sessions.inbox_views import summary

    store,message = pending(coding_session,tmp_path)
    row = store.records(message.session_id,tmp_path)[0]
    row = replace(row,message=replace(message,evidence=({'data':'x'*5000},)))
    projected = summary(row)
    assert projected['truncated'] is True
    assert len(projected['evidence_preview'])<4096


async def test_navigation_history_capture_waits_for_the_new_inbox_scope(coding_session,tmp_path,monkeypatch):
    import threading

    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.interactive.session_presenter import capture_history

    store,message = pending(coding_session,tmp_path)
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    await runtime.inbox.snapshot()
    entered,release = threading.Event(),threading.Event()
    original = store.retire
    old_id = coding_session.session_id

    def delayed(sid,generation):
        if sid==old_id:
            entered.set()
            release.wait(4)
        original(sid,generation)

    monkeypatch.setattr(store,'retire',delayed)
    monkeypatch.setattr(coding_session,'_resolve_selected_model',lambda _:None)

    async def operation():
        await asyncio.to_thread(coding_session.new_session)
        return await capture_history(coding_session)

    receipt = runtime.controller.control(operation,name='new-view')
    try:
        assert await asyncio.to_thread(entered.wait,3)
        release.set()
        view = await asyncio.wait_for(asyncio.wrap_future(receipt),4)
        assert view.session_id==coding_session.session_id!=old_id
        assert view.inbox==() and view.messages==()
    finally:
        release.set()
        await asyncio.gather(asyncio.wrap_future(receipt),return_exceptions=True)
        await asyncio.wrap_future(runtime.close())
