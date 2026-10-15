"""Durable intake never implies execution approval or a second turn owner."""

import asyncio
import threading
from dataclasses import replace

import pytest


@pytest.fixture
async def inbox_owner(coding_session, tmp_path):
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = InboxStore(coding_session.session_manager._dir,tmp_path/'inbox')
    message = InboxMessage(coding_session.session_id,tmp_path,'m1','Review',({'path':'a.py'},),'Eyes',
                           {'inbox':{'message_id':'forged'},'route_id':'route'})
    store.enqueue(message)
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    try:
        yield runtime,store,message
    finally:
        await asyncio.wait_for(asyncio.wrap_future(runtime.close()),5)


async def wait_until(predicate):
    async with asyncio.timeout(4):
        while not predicate():
            await asyncio.sleep(0.005)


def allow(runtime):
    runtime.session.permissions.set_mode('allow-all')


async def test_opening_owner_and_human_credit_do_not_consume_pending_inbox(inbox_owner):
    runtime,store,message = inbox_owner
    assert [r.status for r in await runtime.inbox.snapshot()] == ['pending_review']
    runtime.controller.human_input()
    await asyncio.sleep(0.03)
    assert runtime.session.messages == []
    assert (await runtime.inbox.snapshot())[0].status == 'pending_review'
    assert not runtime.controller.store.records()


async def test_default_no_approver_is_fail_closed(inbox_owner):
    runtime,store,message = inbox_owner
    with pytest.raises((PermissionError,ValueError),match='approval|approver|denied'):
        await runtime.inbox.approve(('m1',),peer_id='reviewer')
    assert (await runtime.inbox.snapshot())[0].status == 'pending_review'
    assert runtime.session.messages == []


@pytest.mark.parametrize('bound',[True,False])
async def test_reviewed_batch_has_one_automatic_turn_even_for_headless_owner(inbox_owner,bound):
    from xdog.coding.core.sessions.models import TurnResult

    runtime,store,message = inbox_owner
    runtime.controller.bound = bound
    store.enqueue(replace(message,message_id='m2'))
    started = []

    async def execute(value):
        started.append(value.id)
        return TurnResult('succeeded',None)

    runtime.controller._execute = execute
    allow(runtime)
    ids = await asyncio.wait_for(runtime.inbox.approve(('m1','m2'),peer_id='reviewer'),3)
    await wait_until(lambda: len([r for r in runtime.controller.store.records() if r.status=='succeeded'])==1)
    await asyncio.sleep(0.03)
    assert started == [ids[0]]
    assert [r.status for r in await runtime.inbox.snapshot()] == ['succeeded','approved']
    assert await asyncio.wrap_future(runtime.controller.resume('reviewer'))
    await wait_until(lambda: len(started)==2)
    await wait_until(lambda: runtime.controller.store.records()[-1].status=='succeeded')
    assert [r.status for r in await runtime.inbox.snapshot()] == ['succeeded','succeeded']


async def test_canonical_input_keeps_provenance_and_actual_attempt_identity(inbox_owner):
    from xdog.agent.session_input import decode_input

    runtime,store,message = inbox_owner
    allow(runtime)
    ids = await runtime.inbox.approve(('m1',),peer_id='reviewer')
    await wait_until(lambda: runtime.controller.store.records()[-1].status=='succeeded')
    record = (await runtime.inbox.snapshot())[0]
    value = runtime.controller.store.records()[0].input
    assert value.id == ids[0] == record.request_id
    assert value.origin == 'acp' and value.source == 'Eyes'
    assert value.evidence == ({'path':'a.py'},)
    assert value.provenance['submitted'] == {'inbox':{'message_id':'forged'},'route_id':'route'}
    assert value.provenance['inbox']['message_id'] == 'm1'
    assert value.provenance['inbox']['attempt_id'] == record.attempt_id
    canonical = [decode_input(m.data) for m in runtime.session.messages
                 if getattr(m,'type','')=='xdog.session_input.v1']
    assert len(canonical)==1 and canonical[0].id == ids[0]


async def test_approval_inside_control_returns_admission_without_waiting_for_its_own_turn(inbox_owner):
    runtime,store,message = inbox_owner
    allow(runtime)
    result = runtime.controller.control(lambda:runtime.inbox.approve(('m1',),peer_id='reviewer'),name='inbox-review')
    ids = await asyncio.wait_for(asyncio.wrap_future(result),3)
    await wait_until(lambda: runtime.controller.store.records()[-1].status=='succeeded')
    assert ids and runtime.controller.store.records()[0].input.id==ids[0]


async def test_pending_permission_is_bound_to_exact_batch_and_generation(inbox_owner):
    runtime,store,message = inbox_owner
    requests = []
    runtime.session.permissions.set_request_handler(requests.append)
    approval = asyncio.create_task(runtime.inbox.approve(('m1',),peer_id='reviewer'))
    try:
        await wait_until(lambda:bool(requests))
        request = requests[0]
        assert request.tool_name=='session_inbox_approve'
        assert request.arguments['message_ids']==['m1']
        assert request.arguments['generation']==runtime.controller.generation
        old_generation = runtime.controller.generation
        runtime.session.session_data.active_branch = 'new-branch'
        runtime.session.session_manager.save_session(runtime.session.session_data)
        await asyncio.wrap_future(runtime.controller.rotate('new-branch'))
        runtime.session.permissions.resolve(request.id,'allow_once')
        with pytest.raises((ValueError,RuntimeError),match='context|generation|branch'):
            await approval
        assert runtime.controller.generation!=old_generation
        assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
        assert not runtime.controller.store.records()
    finally:
        if not approval.done():
            approval.cancel()
        await asyncio.gather(approval,return_exceptions=True)


async def test_concurrent_duplicate_review_does_not_create_a_second_permission_or_attempt(inbox_owner):
    runtime,store,message = inbox_owner
    requests = []
    runtime.session.permissions.set_request_handler(requests.append)
    approval = asyncio.create_task(runtime.inbox.approve(('m1',),peer_id='first'))
    try:
        await wait_until(lambda:bool(requests))
        with pytest.raises((ValueError,RuntimeError),match='review|pending|busy'):
            await runtime.inbox.approve(('m1',),peer_id='second')
        assert len(requests)==1
        runtime.session.permissions.resolve(requests[0].id,'allow_once')
        await approval
        await wait_until(lambda: runtime.controller.store.records()[-1].status=='succeeded')
        assert len((await runtime.inbox.snapshot())[0].attempts)==1
    finally:
        if not approval.done():
            approval.cancel()
        await asyncio.gather(approval,return_exceptions=True)


async def test_producer_settlement_fences_inbox_turn_and_preserves_ui_hooks(inbox_owner):
    runtime,store,message = inbox_owner
    allow(runtime)
    hooks = []

    async def before(value):
        hooks.append(('before',value.id))

    async def after(value,result):
        hooks.append(('after',value.id))

    runtime.controller.before_turn = before
    runtime.controller.after_turn = after
    release = runtime.controller.reserve_producer('terminal')
    approval = asyncio.create_task(runtime.inbox.approve(('m1',),peer_id='reviewer'))
    try:
        await asyncio.sleep(0.03)
        assert not approval.done() and not runtime.session.messages
        release()
        ids = await approval
        await wait_until(lambda:runtime.controller.store.records()[-1].status=='succeeded')
        assert hooks == [('before',ids[0]),('after',ids[0])]
    finally:
        release()


async def test_close_returns_unstarted_approved_items_to_review(inbox_owner):
    runtime,store,message = inbox_owner
    allow(runtime)
    runtime.controller.set_human_pending(True)
    await runtime.inbox.approve(('m1',),peer_id='reviewer')
    assert (await runtime.inbox.snapshot())[0].status=='approved'
    await asyncio.wrap_future(runtime.close())
    record = store.records(message.session_id,message.cwd)[0]
    assert record.status=='pending_review' and not record.owner_generation
    assert record.attempts[0].status=='cancelled'
    assert runtime.session.messages==[]


async def test_cancelling_permission_review_never_persists_authorization(inbox_owner):
    runtime,store,message = inbox_owner
    requests = []
    runtime.session.permissions.set_request_handler(requests.append)
    task = asyncio.create_task(runtime.inbox.approve(('m1',),peer_id='reviewer'))
    await wait_until(lambda:bool(requests))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
    assert not runtime.session.permissions.has_pending_requests
    assert not runtime.controller.store.records()


async def test_cancelled_admission_settles_disk_and_reverts_unstarted_attempt(inbox_owner,monkeypatch):
    runtime,store,message = inbox_owner
    allow(runtime)
    entered,release = threading.Event(),threading.Event()
    original = runtime.controller.store._commit

    def blocked(records):
        entered.set()
        release.wait(4)
        original(records)

    monkeypatch.setattr(runtime.controller.store,'_commit',blocked)
    task = asyncio.create_task(runtime.inbox.approve(('m1',),peer_id='reviewer'))
    try:
        assert await asyncio.to_thread(entered.wait,3)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await wait_until(lambda:not runtime.controller.busy)
        assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
        assert runtime.session.messages==[]
    finally:
        release.set()
        await asyncio.gather(task,return_exceptions=True)


async def test_terminal_persistence_failure_prevents_successor_execution(inbox_owner,monkeypatch):
    from xdog.coding.core.sessions.models import TurnResult

    runtime,store,message = inbox_owner
    allow(runtime)
    store.enqueue(replace(message,message_id='m2'))
    started = []

    async def execute(value):
        started.append(value.id)
        return TurnResult('succeeded',None)

    def failed(*args,**kwargs):
        raise OSError('terminal disk failure')

    runtime.controller._execute = execute
    monkeypatch.setattr(store,'finish',failed)
    ids = await runtime.inbox.approve(('m1','m2'),peer_id='reviewer')
    await wait_until(lambda:runtime.controller._broken is not None)
    runtime.controller.human_input()
    await asyncio.sleep(0.03)
    assert started==[ids[0]]
    assert runtime.controller.store.records()[0].status=='failed'


async def test_no_writer_lease_cannot_approve_or_claim(inbox_owner):
    runtime,store,message = inbox_owner
    allow(runtime)
    lease = runtime.session._lease
    runtime.session._lease = None
    try:
        with pytest.raises(RuntimeError,match='lease|owner'):
            await runtime.inbox.approve(('m1',),peer_id='reviewer')
        assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
    finally:
        runtime.session._lease = lease


async def test_approval_from_another_loop_is_routed_to_the_existing_owner(inbox_owner):
    runtime,store,message = inbox_owner
    allow(runtime)

    def submit():
        return asyncio.run(runtime.inbox.approve(('m1',),peer_id='threaded-reviewer'))

    ids = await asyncio.wait_for(asyncio.to_thread(submit),3)
    await wait_until(lambda:runtime.controller.store.records()[-1].status=='succeeded')
    assert runtime.controller.store.records()[0].input.id==ids[0]


@pytest.mark.parametrize('ids',[(),('m1','m1'),('m1','missing')])
async def test_invalid_selection_has_no_partial_authorization(inbox_owner,ids):
    runtime,store,message = inbox_owner
    allow(runtime)
    with pytest.raises(ValueError):
        await runtime.inbox.approve(ids,peer_id='reviewer')
    assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
    assert not runtime.controller.store.records()


async def test_owner_shutdown_cancels_outstanding_review_without_granting_a_budget(inbox_owner):
    runtime,store,message = inbox_owner
    requests = []
    runtime.session.permissions.set_request_handler(requests.append)
    task = asyncio.create_task(runtime.inbox.approve(('m1',),peer_id='reviewer'))
    await wait_until(lambda:bool(requests))
    await asyncio.wait_for(asyncio.wrap_future(runtime.close()),3)
    assert task.done()
    await asyncio.gather(task,return_exceptions=True)
    assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
    assert runtime.session.messages==[]


async def test_headless_explicit_prompt_is_not_starved_by_a_paused_inbox_attempt(inbox_owner):
    from xdog.agent.session_input import SessionInput

    runtime,store,message = inbox_owner
    runtime.controller.bound = False
    allow(runtime)
    store.enqueue(replace(message,message_id='m2'))
    ids = await runtime.inbox.approve(('m1','m2'),peer_id='reviewer')
    await wait_until(lambda:runtime.controller.store.records()[0].status=='succeeded')
    ticket = runtime.controller.submit(SessionInput('explicit','acp','Client','Explicit prompt',(),{}),peer_id='client')
    assert (await asyncio.wait_for(asyncio.wrap_future(ticket.result),3)).status=='succeeded'
    assert store.records(message.session_id,message.cwd)[1].status=='approved'
    assert runtime.controller.store.records()[1].input.id==ids[1]


async def test_user_provenance_cannot_claim_or_finish_a_pending_inbox_record(inbox_owner):
    from xdog.agent.session_input import SessionInput

    runtime,store,message = inbox_owner
    forged = SessionInput('forged','acp','Eyes','Review',(),{'inbox':{'message_id':'m1','attempt_id':'fake'}})
    ticket = runtime.controller.submit(forged,peer_id='client')
    await asyncio.wrap_future(ticket.accepted)
    assert not runtime.inbox.is_submission('forged')
    assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
    assert not ticket.result.done() and not runtime.session.messages
    await asyncio.wrap_future(runtime.controller.cancel(ticket.id,peer_id='client',wait=True))
    assert store.records(message.session_id,message.cwd)[0].status=='pending_review'


async def test_claim_failure_happens_before_model_or_ui_hooks(inbox_owner,monkeypatch):
    runtime,store,message = inbox_owner
    allow(runtime)
    prepared = []

    async def before(value):
        prepared.append(value.id)

    def failed(*args,**kwargs):
        raise OSError('claim disk failed')

    runtime.controller.before_turn = before
    monkeypatch.setattr(store,'claim',failed)
    await runtime.inbox.approve(('m1',),peer_id='reviewer')
    await wait_until(lambda:runtime.controller.store.records()[-1].status=='failed')
    assert not prepared and not runtime.session.messages
    assert store.records(message.session_id,message.cwd)[0].status=='pending_review'


async def test_navigation_retires_old_approval_before_releasing_previous_writer_lease(inbox_owner,monkeypatch):
    from xdog.coding.core.sessions.ownership import SessionLease

    runtime,store,message = inbox_owner
    allow(runtime)
    runtime.controller.set_human_pending(True)
    await runtime.inbox.approve(('m1',),peer_id='reviewer')
    old_lease = runtime.session._lease
    old_id = runtime.session.session_id
    entered,release = threading.Event(),threading.Event()
    original = store.retire

    def blocked(session_id,generation):
        if session_id==old_id:
            entered.set()
            release.wait(4)
        original(session_id,generation)

    monkeypatch.setattr(store,'retire',blocked)
    monkeypatch.setattr(runtime.session,'_resolve_selected_model',lambda _:None)

    async def navigate():
        return await asyncio.to_thread(runtime.session.new_session)

    navigation = runtime.controller.control(navigate,name='new')
    try:
        reached = await asyncio.to_thread(entered.wait,3)
        assert reached, repr(navigation.exception()) if navigation.done() else 'navigation still active'
        with pytest.raises(RuntimeError,match='owned'):
            extra = SessionLease.acquire(old_lease.directory,old_id)
            extra.close()
        release.set()
        new_id = await asyncio.wait_for(asyncio.wrap_future(navigation),4)
        assert new_id!=old_id
        assert store.records(old_id,message.cwd)[0].status=='pending_review'
        assert not runtime.session.messages
        lease = SessionLease.acquire(old_lease.directory,old_id)
        lease.close()
    finally:
        release.set()
        await asyncio.gather(asyncio.wrap_future(navigation),return_exceptions=True)


async def test_failed_control_still_waits_for_every_registered_cleanup(inbox_owner):
    runtime,store,message = inbox_owner
    entered,release = asyncio.Event(),asyncio.Event()

    async def failing():
        await entered.wait()
        raise OSError('one cleanup failed')

    async def slow():
        entered.set()
        await release.wait()

    async def control():
        runtime.controller.settle_before_release(failing())
        runtime.controller.settle_before_release(slow())

    future = runtime.controller.control(control,name='cleanup-fence')
    try:
        await asyncio.wait_for(entered.wait(),3)
        await asyncio.sleep(0.02)
        assert not future.done(), 'one failed settlement must not release another live producer'
    finally:
        release.set()
        results = await asyncio.gather(asyncio.wrap_future(future),return_exceptions=True)
    assert isinstance(results[0],OSError)


async def test_failed_retirement_reports_error_but_releases_the_stopped_owner(coding_session,tmp_path,monkeypatch):
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.ownership import SessionLease, runtime_directory
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = InboxStore(coding_session.session_manager._dir,tmp_path/'inbox')
    store.enqueue(InboxMessage(coding_session.session_id,tmp_path,'m1','Review',(),'Eyes',{}))
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    await runtime.inbox.snapshot()

    def failed(*args):
        raise OSError('retirement disk failure')

    monkeypatch.setattr(store,'retire',failed)
    try:
        with pytest.raises(OSError,match='retirement disk failure'):
            await asyncio.wait_for(asyncio.wrap_future(runtime.close()),3)
        assert coding_session._lease is None, 'settled failed shutdown must not strand a writer lease'
        lease = SessionLease.acquire(runtime_directory(coding_session.session_manager._dir),coding_session.session_id)
        lease.close()
    finally:
        runtime.disposing = True
        coding_session.dispose()


async def test_real_owner_crash_leaves_interrupted_attempt_and_retry_is_explicit(coding_session,tmp_path):
    import subprocess
    import sys

    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.models import TurnResult
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = InboxStore(coding_session.session_manager._dir,tmp_path/'inbox')
    sid = coding_session.session_id
    marker = tmp_path/'attempts.txt'
    store.enqueue(InboxMessage(sid,tmp_path,'m1','Review',(),'Eyes',{}))
    script = '''
import asyncio,os,sys
from pathlib import Path
from xdog.agent import Agent,AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.runtime import SessionRuntime
async def main():
 root=Path(sys.argv[1]); m=SessionManager(root/'sessions'); data=m.load_session(sys.argv[2])
 session=AgentSession(agent=Agent(lambda *_:EventStream.empty(AssistantMessage()),config=AgentConfig(model='test')),
  session_data=data,session_manager=m,settings=SettingsManager(),tool_registry=None,bash=None,working_dir=root)
 session.permissions.set_mode('allow-all')
 runtime=SessionRuntime(session,bound=True,inbox_store=InboxStore(m._dir,root/'inbox'));runtime.start()
 async def execute(value):
  (root/'attempts.txt').write_text('first attempt\\n')
  os._exit(19)
 runtime.controller._execute=execute
 await runtime.inbox.approve(('m1',),peer_id='reviewer')
 await asyncio.Event().wait()
asyncio.run(main())
'''
    result = await asyncio.to_thread(subprocess.run,[sys.executable,'-c',script,str(tmp_path),sid],
                                      capture_output=True,text=True,timeout=20)
    assert result.returncode==19,result.stderr
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    try:
        row = (await runtime.inbox.snapshot())[0]
        assert row.status=='interrupted'
        runtime.controller.human_input()
        await asyncio.sleep(0.03)
        assert marker.read_text()=='first attempt\n'
        assert not coding_session.messages
        with pytest.raises(ValueError):
            await runtime.inbox.approve(('m1',),peer_id='reviewer')
        allow(runtime)

        async def retry(value):
            with marker.open('a') as stream:
                stream.write('reviewed retry\n')
            return TurnResult('succeeded',None)

        runtime.controller._execute = retry
        request_id = await runtime.inbox.retry('m1',peer_id='reviewer')
        await wait_until(lambda:runtime.controller.store.records()[-1].status=='succeeded')
        updated = (await runtime.inbox.snapshot())[0]
        assert [a.status for a in updated.attempts]==['interrupted','succeeded']
        assert updated.attempts[0].request_id!=request_id==updated.request_id
        assert marker.read_text()=='first attempt\nreviewed retry\n'
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_endpoint_close_error_does_not_return_before_producer_settlement(coding_session):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session,bound=True)
    runtime.start()
    release = runtime.controller.reserve_producer('settling-tool')

    class FailedEndpoint:
        async def close(self):
            raise OSError('endpoint close failed')

    runtime.server = FailedEndpoint()
    receipt = runtime.close()
    try:
        await asyncio.sleep(0.03)
        assert not receipt.done(), 'endpoint error does not mean the writer/producer has stopped'
    finally:
        release()
        await asyncio.gather(asyncio.wrap_future(receipt),return_exceptions=True)
        await asyncio.gather(asyncio.wrap_future(runtime.controller._closed),return_exceptions=True)
        runtime.disposing = True
        coding_session.dispose()


async def test_late_snapshot_cannot_be_relabelled_as_the_new_generation(inbox_owner,monkeypatch):
    runtime,store,message = inbox_owner
    await runtime.inbox.snapshot()
    store.enqueue(replace(message,message_id='m2'))
    events = []
    runtime.controller.subscribe(events.append)
    entered,release = threading.Event(),threading.Event()
    original = store.records

    def blocked(*args):
        entered.set()
        release.wait(4)
        return original(*args)

    monkeypatch.setattr(store,'records',blocked)
    task = asyncio.create_task(runtime.inbox.snapshot())
    try:
        assert await asyncio.to_thread(entered.wait,3)
        runtime.session.session_data.active_branch = 'new-branch'
        runtime.session.session_manager.save_session(runtime.session.session_data)
        await asyncio.wrap_future(runtime.controller.rotate('new-branch'))
        release.set()
        with pytest.raises(RuntimeError,match='context|generation|branch'):
            await task
        assert not [e for e in events if e.kind=='inbox_snapshot' and e.generation==runtime.controller.generation]
    finally:
        release.set()
        await asyncio.gather(task,return_exceptions=True)


async def test_pending_review_cannot_authorize_content_changed_after_permission_request(inbox_owner):
    import json

    runtime,store,message = inbox_owner
    requests = []
    runtime.session.permissions.set_request_handler(requests.append)
    approval = asyncio.create_task(runtime.inbox.approve(('m1',),peer_id='reviewer'))
    try:
        await wait_until(lambda:bool(requests))
        path = next(store.root.rglob('*.json'))
        data = json.loads(path.read_text())
        data['message']['instruction'] = 'Not the instruction the human reviewed'
        path.write_text(json.dumps(data))
        runtime.session.permissions.resolve(requests[0].id,'allow_once')
        with pytest.raises(ValueError,match='content|review|changed'):
            await approval
        assert not runtime.session.messages and not runtime.controller.store.records()
        assert store.records(message.session_id,message.cwd)[0].status=='pending_review'
    finally:
        if not approval.done():
            approval.cancel()
        await asyncio.gather(approval,return_exceptions=True)


async def test_exact_inbox_approval_does_not_wake_an_unrelated_older_external_request(inbox_owner):
    from xdog.agent.session_input import SessionInput

    runtime,store,message = inbox_owner
    older = runtime.controller.submit(
        SessionInput('older','acp','Other client','Unreviewed backlog',(),{}),peer_id='other')
    await asyncio.wrap_future(older.accepted)
    allow(runtime)
    ids = await runtime.inbox.approve(('m1',),peer_id='reviewer')
    await wait_until(lambda:any(r.status=='succeeded' for r in runtime.controller.store.records()))
    completed = [r.input.id for r in runtime.controller.store.records() if r.status=='succeeded']
    assert completed==[ids[0]], 'approval of selected inbox IDs must not grant another peer a turn'
    assert not older.result.done()


async def test_direct_owner_endpoint_recovers_inbox_before_publishing(coding_session,tmp_path,monkeypatch):
    from xdog.coding.acp.server import serve_local
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = InboxStore(coding_session.session_manager._dir,tmp_path/'inbox')
    sid = coding_session.session_id
    store.enqueue(InboxMessage(sid,tmp_path,'m1','Review',(),'Eyes',{}))
    store.approve(sid,('m1',),cwd=tmp_path,branch_id='',generation='previous-owner')
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start()
    original = runtime.registry.publish

    def publish(record):
        assert store.records(sid,tmp_path)[0].status=='pending_review', 'recover before advertising an owner endpoint'
        original(record)

    monkeypatch.setattr(runtime.registry,'publish',publish)
    try:
        await serve_local(runtime)
        assert not coding_session.messages
    finally:
        await asyncio.wrap_future(runtime.close())
