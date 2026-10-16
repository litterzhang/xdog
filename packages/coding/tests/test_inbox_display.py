"""Opening and observing an inbox is presentation, not an executable replay queue."""
import asyncio
from dataclasses import replace

import pytest
from xdog.coding.core.sessions.models import InputRecord, SessionEvent
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.utils import strip_ansi

from packages.coding.tests.batch_helpers import single_input_execute


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
    runtime.start(setup_pending=True)
    try:
        snapshot = await capture_history(coding_session)
        assert snapshot.inbox[0].status=='pending'
        log = ChatLog(create_default_theme())
        presenter = SessionPresenter(log)
        presenter.replay(snapshot.records,snapshot.messages,snapshot.inbox)
        output = text(log)
        assert 'Inspect late.py' in output and 'late.py' in output and 'Eyes' in output
        assert 'pending' in output and 'not a human' in output
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
    runtime.start(setup_pending=True)
    controller = runtime.controller
    log = ChatLog(create_default_theme())
    presenter = SessionPresenter(log)
    try:
        row = (await runtime.inbox.snapshot())[0]
        presenter.apply(SessionEvent(message.session_id,controller.generation,'','inbox_snapshot',(row,)))
        claimed = store.reserve(
            message.session_id,('m1',),cwd=tmp_path,branch_id='',generation=controller.generation)[0]
        value = SessionInput(claimed.request_id,'acp','Eyes',message.instruction,message.evidence,
                             {'inbox':{'message_id':'m1','attempt_id':claimed.attempt_id},'submitted':{}})
        record = InputRecord(value,'',controller.generation,'queued')
        presenter.apply(SessionEvent(message.session_id,controller.generation,value.id,'input_queued',record))
        presenter.apply(SessionEvent(message.session_id,controller.generation,'','inbox_snapshot',(claimed,)))
        presenter.apply(SessionEvent(message.session_id,controller.generation,'','inbox_snapshot',(claimed,)))
        output = text(log)
        assert output.count('Inspect late.py')==1
        assert 'm1' in output and claimed.attempt_id in output
        assert 'claimed' in output and 'queued' in output
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


def test_native_late_enqueue_is_automatically_consumed_without_message_approval(coding_session,tmp_path):
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
        deadline = time.monotonic()+4
        while store.records(message.session_id,message.cwd)[-1].status!='succeeded':
            assert time.monotonic()<deadline
            ui._poll()
            time.sleep(0.01)
        ui._poll()
        assert coding_session.messages and not coding_session.permissions.has_pending_requests
        assert text(ui._chat_log).count('Second late message')==1
    finally:
        ui._request_exit()
        ui._session_events.close()


def test_obsolete_native_approval_after_a_turn_never_opens_a_permission_dialog(coding_session,tmp_path):
    import time

    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    pending(coding_session,tmp_path)
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    try:
        ticket = ui._session_events.enqueue('Hello',(),'user')
        ticket.result.result(timeout=3)
        ui._poll()
        ui._handle_slash_command('inbox','approve m1')
        deadline = time.monotonic()+4
        while ui._command is not None:
            assert time.monotonic()<deadline
            ui._poll()
            time.sleep(0.01)
        ui._poll()
        assert 'obsolete' in text(ui._chat_log)
        assert ui._permission_prompt is None and not coding_session.permissions.has_pending_requests
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
    runtime.start(setup_pending=True)
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
        await runtime.activate_inputs()
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


@pytest.mark.parametrize('action',['list','approve m1','cancel m2'])
def test_native_inbox_controls_do_not_wait_for_an_unrelated_busy_model(coding_session,tmp_path,action):
    import threading
    import time

    from xdog.coding.core.sessions.models import TurnResult
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    store,message = pending(coding_session,tmp_path)
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    controller = coding_session.runtime.controller
    entered,release = threading.Event(),threading.Event()

    async def execute(item):
        entered.set()
        await asyncio.to_thread(release.wait,4)
        return TurnResult('succeeded',None)

    controller.max_messages_per_turn = 1
    controller._execute_batch = single_input_execute(execute)
    try:
        ticket = ui._session_events.enqueue('A running turn',(),'user')
        assert entered.wait(3)
        ui._poll()
        if action=='cancel m2':
            store.enqueue(replace(message,message_id='m2'))
        ui._handle_slash_command('inbox',action)
        deadline = time.monotonic()+1
        while ui._command is not None:
            assert time.monotonic()<deadline, 'read-only inbox inspection must not queue behind model execution'
            ui._poll()
            time.sleep(0.01)
        assert not ticket.result.done()
        if action=='approve m1':
            assert 'obsolete' in text(ui._chat_log)
        elif action=='cancel m2':
            assert next(row for row in store.records(message.session_id,tmp_path)
                        if row.message.message_id=='m2').status=='cancelled'
        else:
            assert 'm1' in text(ui._chat_log)
        assert not coding_session.permissions.has_pending_requests
    finally:
        release.set()
        ui._request_exit()
        if ui._command_thread is not None:
            ui._command_thread.join(timeout=3)
        ui._session_events.close()


def test_native_inbox_query_does_not_repeat_automatically_delivered_input(coding_session,tmp_path):
    import time

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    store,message = pending(coding_session,tmp_path)
    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))
    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    runtime = coding_session.runtime
    try:
        deadline = time.monotonic()+3
        while (store.records(message.session_id,message.cwd)[0].status!='succeeded'
               or not runtime.controller.store.records()
               or runtime.controller.store.records()[0].status!='succeeded'):
            assert time.monotonic()<deadline
            ui._poll()
            time.sleep(0.01)
        records = runtime.controller.store.records()
        before = len(calls)
        assert before==1 and len(records)==1
        ui._handle_submit('/inbox list')
        while ui._command is not None:
            assert time.monotonic()<deadline
            ui._poll()
            time.sleep(0.01)
        ui._poll()
        assert runtime.controller.store.records()==records and len(calls)==before
        assert not coding_session.permissions.has_pending_requests
    finally:
        ui._request_exit()
        if ui._command_thread is not None:
            ui._command_thread.join(timeout=3)
        ui._session_events.close()


def test_inbox_evidence_preview_reports_truncation_of_a_single_large_item(coding_session,tmp_path):
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


def test_inbox_summary_explains_branch_block_without_implying_message_approval(coding_session,tmp_path):
    from xdog.coding.core.sessions.inbox_views import summary

    store,message = pending(coding_session,tmp_path)
    row = store.records(message.session_id,tmp_path)[0]
    projected = summary(row,current_branch='another-branch')
    assert projected['blocked_reason']=='branch_changed'
    assert projected['status']=='pending' and projected['branch_id']==''
    assert projected['sequence']==row.sequence and projected['batch_id']==row.batch_id
    assert summary(row,current_branch='')['blocked_reason'] is None
