"""Automatic Inbox admission preserves scope, ownership and durable effect boundaries."""
import asyncio
import threading
from dataclasses import replace

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream


@pytest.fixture
async def inbox_owner(coding_session, tmp_path):
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = InboxStore(coding_session.session_manager._dir, tmp_path / 'inbox')
    message = InboxMessage(coding_session.session_id, tmp_path, 'm1', 'Review', ({'path': 'a.py'},), 'Eyes',
                           {'inbox': {'message_id': 'forged'}, 'route_id': 'route'})
    store.enqueue(message)
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        yield runtime, store, message
    finally:
        await asyncio.wait_for(asyncio.wrap_future(runtime.close()), 5)


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.005)


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
    from xdog.coding.core.sessions.ownership import SessionLease, lease_directory
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = InboxStore(coding_session.session_manager._dir,tmp_path/'inbox')
    store.enqueue(InboxMessage(coding_session.session_id,tmp_path,'m1','Review',(),'Eyes',{}))
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start(setup_pending=True)
    await runtime.inbox.snapshot()

    def failed(*args):
        raise OSError('retirement disk failure')

    monkeypatch.setattr(store,'retire',failed)
    try:
        with pytest.raises(OSError,match='retirement disk failure'):
            await asyncio.wait_for(asyncio.wrap_future(runtime.close()),3)
        assert coding_session._lease is None, 'settled failed shutdown must not strand a writer lease'
        lease = SessionLease.acquire(lease_directory(coding_session.session_manager._dir),coding_session.session_id)
        lease.close()
    finally:
        runtime.disposing = True
        coding_session.dispose()


async def test_endpoint_close_error_does_not_return_before_producer_settlement(coding_session):
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session,bound=True)
    runtime.start(setup_pending=True)
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


async def test_direct_owner_endpoint_recovers_inbox_before_publishing(coding_session,tmp_path,monkeypatch):
    from xdog.coding.acp.server import serve_local
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = InboxStore(coding_session.session_manager._dir,tmp_path/'inbox')
    sid = coding_session.session_id
    store.enqueue(InboxMessage(sid,tmp_path,'m1','Review',(),'Eyes',{}))
    store.reserve(sid,('m1',),cwd=tmp_path,branch_id='',generation='previous-owner')
    runtime = SessionRuntime(coding_session,bound=True,inbox_store=store)
    runtime.start(setup_pending=True)
    original = runtime.registry.publish

    def publish(record):
        assert store.records(sid,tmp_path)[0].status=='pending', 'recover before advertising an owner endpoint'
        original(record)

    monkeypatch.setattr(runtime.registry,'publish',publish)
    try:
        await serve_local(runtime)
        assert not coding_session.messages
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_canonical_input_preserves_untrusted_provenance_and_real_attempt_identity(inbox_owner):
    runtime, store, message = inbox_owner
    await runtime.activate_inputs()
    await wait_until(lambda: store.records(message.session_id, message.cwd)[0].status == 'succeeded')
    record = store.records(message.session_id, message.cwd)[0]
    value = runtime.controller.store.records()[0].input
    assert value.origin == 'acp'
    assert value.provenance['submitted'] == message.provenance
    assert value.provenance['inbox'] == {'message_id': 'm1', 'attempt_id': record.attempt_id, 'branch_id': ''}
    assert value.id == record.request_id and record.owner_generation == runtime.controller.generation


async def test_activation_inside_control_admits_without_waiting_for_its_own_turn(inbox_owner):
    runtime, store, message = inbox_owner

    async def controlled():
        await runtime.activate_inputs()
        rows = runtime.controller.store.records()
        assert len(rows) == 1 and rows[0].status == 'queued'
        assert runtime.session.messages == []
        return rows[0].input.id

    result = runtime.controller.control(controlled, name='owned-intake')
    request = await asyncio.wait_for(asyncio.wrap_future(result), 3)
    await wait_until(lambda: store.records(message.session_id, message.cwd)[0].status == 'succeeded')
    assert store.records(message.session_id, message.cwd)[0].request_id == request


async def test_producer_settlement_fences_automatic_turn_and_preserves_batch_hooks(inbox_owner):
    runtime, store, message = inbox_owner
    hooks = []

    async def before(batch):
        hooks.append(('before', batch.id))

    async def after(batch, outcome):
        hooks.append(('after', batch.id))

    runtime.controller.before_batch, runtime.controller.after_batch = before, after
    release = runtime.controller.reserve_producer('terminal-settlement')
    activating = asyncio.create_task(runtime.activate_inputs())
    try:
        await asyncio.sleep(0.02)
        assert not activating.done() and runtime.session.messages == []
        release()
        await asyncio.wait_for(activating, 3)
        await wait_until(lambda: store.records(message.session_id, message.cwd)[0].status == 'succeeded')
        batch_id = store.records(message.session_id, message.cwd)[0].batch_id
        assert hooks == [('before', batch_id), ('after', batch_id)]
    finally:
        release()
        await asyncio.gather(activating, return_exceptions=True)


async def test_shutdown_settles_inflight_claim_before_releasing_writer_lease(inbox_owner, monkeypatch):
    from xdog.coding.core.sessions.ownership import SessionLease

    runtime, store, message = inbox_owner
    entered, release = threading.Event(), threading.Event()
    original = store.reserve

    def reserve(*args, **kwargs):
        rows = original(*args, **kwargs)
        entered.set()
        release.wait(5)
        return rows

    monkeypatch.setattr(store, 'reserve', reserve)
    activating = asyncio.create_task(runtime.activate_inputs())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        lease = runtime.session._lease
        closing = runtime.close()
        await asyncio.sleep(0.02)
        assert not closing.done()
        with pytest.raises(RuntimeError, match='owned'):
            other = SessionLease.acquire(lease.directory, message.session_id)
            other.close()
        release.set()
        await asyncio.wait_for(asyncio.wrap_future(closing), 5)
        await asyncio.gather(activating, return_exceptions=True)
        assert store.records(message.session_id, message.cwd)[0].status == 'pending'
        other = SessionLease.acquire(lease.directory, message.session_id)
        other.close()
        assert not runtime.session.messages
    finally:
        release.set()
        await asyncio.gather(activating, return_exceptions=True)


async def test_exact_claim_snapshot_rejects_content_mutation_before_reservation(inbox_owner, monkeypatch):
    import json

    runtime, store, message = inbox_owner
    original = store.reserve

    def changed(*args, **kwargs):
        path = next(store.root.rglob('*.json'))
        raw = json.loads(path.read_text())
        raw['message']['instruction'] = 'Changed after the intake snapshot'
        path.write_text(json.dumps(raw))
        return original(*args, **kwargs)

    monkeypatch.setattr(store, 'reserve', changed)
    with pytest.raises(ValueError, match='changed'):
        await runtime.activate_inputs()
    assert runtime.session.messages == []
    assert store.records(message.session_id, message.cwd)[0].status == 'pending'


async def test_snapshot_remains_read_only_and_available_while_model_is_busy(inbox_owner):
    runtime, store, message = inbox_owner
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(batch):
        from xdog.coding.core.sessions.models import TurnResult
        entered.set()
        await release.wait()
        return TurnResult('succeeded', None)

    runtime.controller._execute_batch = execute
    try:
        await runtime.activate_inputs()
        await asyncio.wait_for(entered.wait(), 3)
        rows = await asyncio.wait_for(runtime.inbox.snapshot(), 1)
        assert rows[0].status == 'running'
        assert not runtime.session.messages
    finally:
        release.set()


async def test_claim_publication_failure_precedes_model_and_presentation_hooks(inbox_owner, monkeypatch):
    runtime, store, message = inbox_owner
    prepared = []

    async def before(batch):
        prepared.append(batch.id)

    def failed(*args, **kwargs):
        raise OSError('claim disk failed')

    runtime.controller.before_batch = before
    monkeypatch.setattr(store, 'begin_batch', failed)
    await runtime.activate_inputs()
    await wait_until(lambda: runtime.controller.store.records()[-1].status == 'failed')
    assert prepared == [] and runtime.session.messages == []
    assert store.records(message.session_id, message.cwd)[0].status == 'failed'


async def test_navigation_retires_old_intake_before_releasing_previous_writer(inbox_owner, monkeypatch):
    from xdog.coding.core.sessions.ownership import SessionLease

    runtime, store, message = inbox_owner
    await runtime.inbox.snapshot()
    old_id, old_lease = runtime.session.session_id, runtime.session._lease
    entered, release = threading.Event(), threading.Event()
    retire = store.retire

    def blocked(session_id, generation):
        if session_id == old_id:
            entered.set()
            release.wait(5)
        retire(session_id, generation)

    monkeypatch.setattr(store, 'retire', blocked)
    monkeypatch.setattr(runtime.session, '_resolve_selected_model', lambda _: None)

    async def navigate():
        return await asyncio.to_thread(runtime.session.new_session)

    navigation = runtime.controller.control(navigate, name='new-session')
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        with pytest.raises(RuntimeError, match='owned'):
            extra = SessionLease.acquire(old_lease.directory, old_id)
            extra.close()
        release.set()
        new_id = await asyncio.wait_for(asyncio.wrap_future(navigation), 5)
        assert new_id != old_id and not runtime.session.messages
        assert store.records(old_id, message.cwd)[0].status == 'pending'
        extra = SessionLease.acquire(old_lease.directory, old_id)
        extra.close()
    finally:
        release.set()
        await asyncio.gather(asyncio.wrap_future(navigation), return_exceptions=True)


async def test_terminal_inbox_failure_does_not_report_success_or_start_successor(inbox_owner, monkeypatch):
    runtime, store, message = inbox_owner
    store.enqueue(replace(message, message_id='m2'))
    runtime.controller.max_messages_per_turn = 1
    calls = []
    runtime.session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))
    save = store._save

    def fail(directory, record, old=None):
        if record.status == 'succeeded':
            raise OSError('terminal inbox fsync failed')
        return save(directory, record, old)

    monkeypatch.setattr(store, '_save', fail)
    await runtime.activate_inputs()
    await wait_until(lambda: runtime.controller._broken is not None
                     and runtime.controller.store.records()[0].status == 'failed')
    assert calls == [True]
    assert runtime.controller.store.records()[0].status == 'failed'
    assert store.records(message.session_id, message.cwd)[1].status == 'pending'


async def test_automatic_owner_crash_becomes_interrupted_and_retry_remains_explicit(coding_session, tmp_path):
    import subprocess
    import sys

    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.runtime import SessionRuntime

    store = InboxStore(coding_session.session_manager._dir, tmp_path / 'inbox')
    sid = coding_session.session_id
    store.enqueue(InboxMessage(sid, tmp_path, 'm1', 'Review', (), 'callback', {}))
    script = '''
import asyncio,os,sys
from pathlib import Path
from xdog.agent import Agent,AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.sessions.runtime import SessionRuntime
async def main():
 root=Path(sys.argv[1]);manager=SessionManager(root/'sessions')
 def stream(*args):
  (root/'attempts').write_text('first\n')
  os._exit(19)
 session=AgentSession(agent=Agent(stream,config=AgentConfig(model='test')),
  session_data=manager.load_session(sys.argv[2]),session_manager=manager,
  settings=SettingsManager(global_path=root/'settings.json'),working_dir=root,tool_registry=None,bash=None)
 runtime=SessionRuntime(session,bound=True);runtime.start()
 await asyncio.Event().wait()
asyncio.run(main())
'''
    # Escape the embedded literal so the child receives Python source, not a physical newline in the string.
    script = script.replace("write_text('first\n')", "write_text('first\\n')")
    process = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script, str(tmp_path), sid],
                                      capture_output=True, text=True, timeout=20)
    assert process.returncode == 19, process.stderr
    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start(setup_pending=True)
    try:
        row = (await runtime.inbox.snapshot())[0]
        assert row.status == 'interrupted'
        await runtime.activate_inputs()
        assert calls == [] and (tmp_path / 'attempts').read_text() == 'first\n'
        await runtime.inbox.retry('m1', peer_id='inbox-command')
        await wait_until(lambda: store.records(sid, tmp_path)[0].status == 'succeeded')
        row = store.records(sid, tmp_path)[0]
        assert len(calls) == 1
        assert [attempt.status for attempt in row.attempts] == ['interrupted', 'succeeded']
        assert row.attempts[0].id != row.attempts[1].id
    finally:
        await asyncio.wrap_future(runtime.close())


async def test_retry_inside_owned_control_does_not_wait_on_itself(inbox_owner):
    runtime, store, message = inbox_owner
    rows = store.reserve(message.session_id, ('m1',), cwd=message.cwd, branch_id='', generation='previous')
    store.begin_batch(message.session_id, rows, batch_id='previous', generation='previous')
    assert (await runtime.inbox.snapshot())[0].status == 'interrupted'
    await runtime.activate_inputs()

    async def retry():
        return await runtime.inbox.retry('m1', peer_id='inbox-command')

    result = runtime.controller.control(retry, name='owned-retry')
    request_id = await asyncio.wait_for(asyncio.wrap_future(result), 1)
    await wait_until(lambda: store.records(message.session_id, message.cwd)[0].status == 'succeeded')
    assert store.records(message.session_id, message.cwd)[0].request_id == request_id
