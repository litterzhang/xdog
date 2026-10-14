"""Real Coding supervisor/child loops over scoped RPC; no model network or parent tool grants."""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.tools import get_default_tools
from xdog.coding.modes.rpc.children import RpcChildren
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.transcripts import RpcTranscripts
from xdog.tui.source_receiver import SourceReceiver


class Adapter:
    def __init__(self):
        self.contexts = []
        self.entered = threading.Event()
        self.settled = threading.Event()
    def __call__(self, model, context, options):
        self.contexts.append(context)
        content = str(context.messages[-1].content)
        if context.messages[-1].role == 'user' and 'write' in content:
            message = AssistantMessage(content=(ToolCall(id='write', name='filesystem', arguments={
                'action': 'write', 'path': 'target', 'content': 'RPC-CHILD-CHANGE'}),))
        elif context.messages[-1].role == 'user' and 'dialogue' in content:
            message = AssistantMessage(content=(ToolCall(id='question', name=next(tool.name for tool in context.tools
                if tool.name.startswith('ext_dialogue_many' if 'many' in content else 'ext_dialogue_ask')),
                arguments={}),))
        else:
            message = AssistantMessage(content=(TextContent(text='RPC-CHILD-DONE'),))
        async def generate():
            try:
                if 'hold' in content:
                    self.entered.set()
                    yield StartEvent(partial=AssistantMessage(content=(TextContent(text='RPC-LIVE-PARTIAL'),)))
                    await asyncio.sleep(60)
                yield DoneEvent(message=message)
            finally:
                self.settled.set()
        result = asyncio.get_running_loop().create_future()
        result.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=result)


def setup(tmp_path, mode='deny', extension=''):
    settings = tmp_path / 'settings.json'
    if extension:
        root = tmp_path / 'extensions'
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions' / extension, root / extension)
        settings.write_text(json.dumps({'extensions': [extension], 'extension_dir': str(root)}))
    adapter = Adapter()
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='offline/model'),
        tools=get_default_tools(tmp_path), tool_ctx={'fs_workspace': str(tmp_path)}),
        session_data=store.create_session(model='offline/model', working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager(mode))
    events = []
    nav = RpcNavigation(session, events.append)
    sources = RpcTranscripts(session, nav, events.append)
    children = RpcChildren(session, nav, sources, events.append)
    children.negotiate({'child_controls': 1, 'child_sources': 1})
    return session, adapter, nav, sources, children, events


def packet(children, key='control', **fields):
    return {'type': 'child_control', 'request_id': key, **children._envelope(), **fields}


async def control(children, action, key='control', **fields):
    children.start(packet(children, key, action=action, **fields), turn_running=False, extension_running=False)
    await children.jobs[key][0]
    await asyncio.sleep(0)


async def wait(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.003)


async def close(session, nav, sources, children):
    await children.aclose()
    await sources.aclose()
    await nav.aclose()
    await session.extensions.aclose()
    session.dispose()


@pytest.mark.parametrize('access,mode,write', [('read-only', 'allow-all', False), ('inherit', 'deny', False),
                                               ('inherit', 'ask', False), ('inherit', 'allow-all', True)])
async def test_actual_rpc_child_policy_persistence_and_source_are_independent_of_parent(tmp_path, access, mode, write):
    session, adapter, nav, sources, children, events = setup(tmp_path, mode)
    try:
        await control(children, 'start', task='write', access=access)
        started = next(event for event in events if event['type'] == 'child_result' and event['action'] == 'start')
        assert started['success']
        child = started['child_id']
        await control(children, 'wait', 'wait', child_id=child)
        assert (tmp_path / 'target').exists() is write and not session.messages
        assert len(adapter.contexts) == 2
        assert not any(tool.name in {'agent_spawn', 'agent_control', 'update_plan'}
                       for tool in adapter.contexts[0].tools)
        assert session.children.store.load()[0]['messages']
        received = []
        receiver = SourceReceiver(received.append, max_bytes=128 * 1024 * 1024)
        receiver.expect('source', '', session.session_id, target_session_id=session.session_id,
            session_revision=nav.revision, child_id=child, child_owner=children.lease,
            child_epoch=session.children.token)
        try:
            children.source(packet(children, 'source', child_id=child))
            await sources.task
            for event in events:
                if event['type'].startswith('source_'):
                    receiver.feed(event)
            assert received[-1]['type'] == 'source_ready'
            assert 'RPC-CHILD-DONE' in str(received[-1]['records'])
        finally:
            receiver.close()
        await control(children, 'close', 'close', child_id=child)
        assert not session.children.store.load() and not session.children.supervisor.snapshot()['children']
        assert session.permissions.mode == mode and not sources.enabled
    finally:
        await close(session, nav, sources, children)


@pytest.mark.parametrize('scenario', ['allow', 'deny', 'foreign', 'planning', 'withdraw'])
async def test_actual_child_permission_nonce_cannot_borrow_parent_or_other_connection_and_plan_remains_authoritative(
    tmp_path, scenario,
):
    session, _, nav, sources, children, events = setup(tmp_path, 'ask')
    children.negotiate({'child_approvals': 1})
    try:
        await control(children, 'start', task='write', access='inherit')
        await wait(lambda: any(event['type'] == 'child_permission_request' for event in events))
        question = next(event for event in events if event['type'] == 'child_permission_request')
        assert not session.permissions.resolve(question['id'], 'allow_once')
        answer = packet(children, id=question['id'], child_id=question['child_id'], decision='allow_once',
                        approval_generation=question['approval_generation'])
        if scenario == 'foreign':
            for field in ('child_owner', 'child_epoch', 'child_id', 'session_revision'):
                with pytest.raises(ValueError):
                    children.answer_permission({**answer, field: 'foreign'})
            assert not (tmp_path / 'target').exists()
        if scenario == 'planning':
            session.plan.change('start', 0, 'Read only while review pending')
        if scenario == 'withdraw':
            children.negotiate({'child_approvals': 0})
            with pytest.raises(ValueError):
                children.answer_permission(answer)
        else:
            children.answer_permission({**answer, 'decision': 'deny' if scenario == 'deny' else 'allow_once'})
            with pytest.raises(ValueError):
                children.answer_permission(answer)
        await control(children, 'wait', 'wait', child_id=question['child_id'])
        assert (tmp_path / 'target').exists() is (scenario in {'allow', 'foreign'})
        assert not session.permissions._session_rules
    finally:
        await close(session, nav, sources, children)


async def test_rpc_cancel_of_wait_does_not_cancel_child_but_native_cancel_settles_generator_and_thread(tmp_path):
    session, adapter, nav, sources, children, events = setup(tmp_path)
    try:
        await control(children, 'start', task='hold')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        assert await asyncio.to_thread(adapter.entered.wait, 3)
        children.start(packet(children, 'wait', action='wait', child_id=child),
                       turn_running=False, extension_running=False)
        waiting = children.jobs['wait'][0]
        await asyncio.sleep(0.01)
        children.cancel(packet(children, 'wait'))
        await waiting
        assert session.children.supervisor.active and not adapter.settled.is_set()
        for action, text in [('steer', 'STEERING'), ('follow-up', 'FOLLOWUP')]:
            await control(children, action, action, child_id=child, text=text)
        queued = session.children.supervisor._children[child].agent.pending_inputs()
        assert [item.content for item in queued] == ['STEERING', 'FOLLOWUP']
        await control(children, 'cancel', 'cancel', child_id=child)
        assert adapter.settled.is_set() and not session.children.supervisor.active
        assert session.children.store.load()[0]['status'] == 'canceled'
    finally:
        await close(session, nav, sources, children)
    assert not session.children.supervisor._thread.is_alive()


@pytest.mark.parametrize('supported', [False])
async def test_real_independent_child_tool_question_uses_only_scoped_child_dialogs(tmp_path, supported):
    session, _, nav, sources, children, events = setup(tmp_path, 'allow-all', 'dialogue')
    children.negotiate({'child_dialogs': int(supported)})
    try:
        await control(children, 'start', task='dialogue', access='inherit')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        if supported:
            await wait(lambda: any(event['type'] == 'child_extension_dialog' for event in events))
            question = next(event for event in events if event['type'] == 'child_extension_dialog')
            assert not session.extension_dialogs.contains(question['dialog']['id'])
            reply = packet(children, id=question['dialog']['id'], child_id=child, value='CHILD-ONLY-ANSWER')
            with pytest.raises(ValueError):
                children.answer_dialog({**reply, 'child_id': '000000000000'})
            children.answer_dialog(reply)
            with pytest.raises(ValueError):
                children.answer_dialog(reply)
        await control(children, 'wait', 'wait', child_id=child)
        value = session.children.store.load()[0]
        assert ('TOOL-DIALOG-CHILD-ONLY-ANSWER' if supported else 'TOOL-DIALOG-None') in str(value['messages'])
        assert not session.messages and session.permissions.mode == 'allow-all'
    finally:
        await close(session, nav, sources, children)


async def test_admission_config_caps_stale_epoch_and_navigation_do_not_silently_change_parent(tmp_path):
    session, _, nav, sources, children, events = setup(tmp_path)
    try:
        for field in ('child_owner', 'child_epoch', 'session_id', 'session_revision'):
            with pytest.raises(ValueError):
                children.start(packet(children, action='list', **{field: 'foreign'}),
                               turn_running=False, extension_running=False)
        with pytest.raises(ValueError):
            children.negotiate({'child_controls': True})
        with pytest.raises(ValueError):
            children.start(packet(children, action='start', task='task', config={'tools': ['agent_spawn']}),
                           turn_running=False, extension_running=False)
        await control(children, 'configure', config={'tools': [], 'max_model_calls': 1, 'inherit_context': False})
        configured = next(event for event in events if event['type'] == 'child_result')
        assert configured['config']['tools'] == [] and configured['config']['max_model_calls'] == 1
        old = packet(children, 'old', action='list')
        nav.refresh_revision()
        with pytest.raises(ValueError):
            children.start(old, turn_running=False, extension_running=False)
        children.negotiate({})
        await control(children, 'list', 'fresh')
        assert not session.messages and events[-1]['success']
    finally:
        await close(session, nav, sources, children)


async def test_actual_resolved_approval_cannot_revive_after_withdraw_and_reenable(tmp_path, monkeypatch):
    session, _, nav, sources, children, events = setup(tmp_path, 'ask')
    entered, release = threading.Event(), threading.Event()
    original = PermissionManager.before_tool_call
    async def held(manager, context, cancel=None):
        result = await original(manager, context, cancel)
        if manager is not session.permissions and result is None:
            entered.set()
            assert await asyncio.to_thread(release.wait, 4)
        return result
    monkeypatch.setattr(PermissionManager, 'before_tool_call', held)
    children.negotiate({'child_approvals': 1})
    try:
        await control(children, 'start', task='write', access='inherit')
        await wait(lambda: any(event['type'] == 'child_permission_request' for event in events))
        question = next(event for event in events if event['type'] == 'child_permission_request')
        answer = packet(children, id=question['id'], child_id=question['child_id'], decision='allow_session',
                        approval_generation=question['approval_generation'])
        children.answer_permission(answer)
        assert await asyncio.to_thread(entered.wait, 3)
        assert not (tmp_path / 'target').exists()
        children.negotiate({'child_approvals': 0})
        children.negotiate({'child_approvals': 1})
        assert not (tmp_path / 'target').exists()
        release.set()
        await control(children, 'wait', 'wait', child_id=question['child_id'])
        assert not (tmp_path / 'target').exists()
        assert 'UI owner changed' in str(session.children.store.load()[0]['messages'])
    finally:
        release.set()
        await close(session, nav, sources, children)


async def test_pending_control_cancel_before_admission_has_terminal_receipt_and_no_child(tmp_path):
    session, adapter, nav, sources, children, events = setup(tmp_path)
    try:
        children.start(packet(children, action='start', task='hold'), turn_running=False, extension_running=False)
        task = children.jobs['control'][0]
        children.cancel(packet(children))
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert not children.starting and not children.busy and not session.children.supervisor.snapshot()['children']
        assert not adapter.contexts
        assert any(event['type'] == 'child_result' and event['canceled'] for event in events)
    finally:
        await close(session, nav, sources, children)


async def test_child_source_capability_is_independent_of_parent_transcript_negotiation_and_strictly_scoped(tmp_path):
    session, _, nav, sources, children, events = setup(tmp_path)
    try:
        await control(children, 'start', task='task')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        await control(children, 'wait', 'wait', child_id=child)
        sources.negotiate({'transcript_sources': 0})
        children.source(packet(children, 'source', child_id=child))
        await sources.task
        assert events[-1]['type'] == 'source_end' and not sources.enabled
        for field in ('child_id', 'child_owner', 'child_epoch'):
            received = []
            receiver = SourceReceiver(received.append)
            receiver.expect('source', '', session.session_id, target_session_id=session.session_id,
                session_revision=nav.revision, child_id=child, child_owner=children.lease,
                child_epoch=session.children.token)
            try:
                for event in events:
                    if event['type'].startswith('source_'):
                        receiver.feed({**event, field: 'foreign'})
                assert received[-1]['type'] == 'source_failed'
                assert not any(event['type'] == 'source_ready' for event in received)
            finally:
                receiver.close()
    finally:
        await close(session, nav, sources, children)


async def test_cancel_after_start_admission_settles_exact_child_and_reports_it_without_rollback(tmp_path, monkeypatch):
    session, adapter, nav, sources, children, events = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    start = session.children.supervisor.start
    admitted = []
    async def held(*args, **kwargs):
        identifier = await start(*args, **kwargs)
        admitted.append(identifier)
        entered.set()
        assert await asyncio.to_thread(release.wait, 4)
        return identifier
    monkeypatch.setattr(session.children.supervisor, 'start', held)
    try:
        children.start(packet(children, action='start', task='hold'), turn_running=False, extension_running=False)
        task = children.jobs['control'][0]
        assert await asyncio.to_thread(entered.wait, 3)
        assert await asyncio.to_thread(adapter.entered.wait, 3)
        children.cancel(packet(children))
        await asyncio.sleep(0.01)
        assert children.busy and children.starting
        release.set()
        await task
        await asyncio.sleep(0)
        assert adapter.settled.is_set() and not session.children.supervisor.active
        result = next(event for event in events if event['type'] == 'child_result')
        assert result['canceled'] and result['child_id'] == admitted[0]
        assert session.children.store.load()[0]['status'] == 'canceled'
    finally:
        release.set()
        await close(session, nav, sources, children)


async def test_child_source_cancel_cannot_use_parent_only_or_foreign_child_scope(tmp_path, monkeypatch):
    import xdog.coding.modes.rpc.transcripts as module

    session, _, nav, sources, children, events = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    prepare = module.prepare
    def held(item, canceled):
        result = prepare(item, canceled)
        entered.set()
        assert release.wait(4)
        return result
    try:
        await control(children, 'start', task='task')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        await control(children, 'wait', 'wait', child_id=child)
        monkeypatch.setattr(module, 'prepare', held)
        request = packet(children, 'source', child_id=child)
        children.source(request)
        assert await asyncio.to_thread(entered.wait, 3)
        with pytest.raises(ValueError, match='Child source'):
            sources.cancel({key: value for key, value in request.items() if not key.startswith('child_')})
        with pytest.raises(ValueError, match='Child source'):
            sources.cancel({**request, 'child_id': '000000000000'})
        sources.cancel(request)
        release.set()
        await sources.task
        assert not any(event['type'] == 'source_end' for event in events)
    finally:
        release.set()
        await close(session, nav, sources, children)


@pytest.mark.parametrize('with_image', [False, True])
async def test_rpc_initial_task_recovery_is_separately_negotiated_and_never_reexecutes(tmp_path, with_image):
    from xdog.ai.types import ImageContent
    from xdog.tui.child_recovery import ChildRecoveryReceiver
    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='
    session, adapter, nav, sources, children, events = setup(tmp_path)
    def fail(*args):
        raise ValueError('Owned setup failure')
    session.children.supervisor.factory = fail
    receiver = ChildRecoveryReceiver(lambda event: received.append(event))
    received = []
    try:
        child = await session.children.supervisor.start('FAILED INITIAL TASK',
            images=(ImageContent(data=png, mime_type='image/png'),) if with_image else ())
        await session.children.supervisor.control('wait', child)
        before = session.children.store.load()[0]
        children.negotiate({'child_queues': 1})
        await control(children, 'queue', 'task-list', child_id=child, queue={'action': 'list'})
        captured = next(row['queue'] for row in events if row.get('request_id') == 'task-list'
                        and row['type'] == 'child_result')
        row = captured['inputs'][0]
        assert row['source'] == 'initial-task'
        request = {'action': 'recover_task', 'id': row['id'], 'revision': captured['revision']}
        with pytest.raises(ValueError, match='child_task_recovery:1'):
            children.start(packet(children, 'task-without-cap', action='queue', child_id=child, queue=request),
                           turn_running=False, extension_running=False)
        children.negotiate({'child_task_recovery': 1, 'child_image_recovery': int(with_image)})
        if with_image:
            receiver.expect({**children._envelope(), 'request_id': 'task-copy', 'child_id': child,
                             'input_id': row['id'], 'queue_revision': captured['revision']})
        async def send(event):
            receiver.feed(event)
            await asyncio.sleep(0)
        children.send = send
        await control(children, 'queue', 'task-copy', child_id=child, queue=request)
        copied = next(row for row in events if row.get('request_id') == 'task-copy' and row['type'] == 'child_result')
        assert copied['success'] and copied['queue']['recovered'] == row
        assert bool(received) is with_image
        assert session.children.store.load()[0] == before and not adapter.contexts and not session.messages
    finally:
        receiver.close()
        await close(session, nav, sources, children)


@pytest.mark.parametrize('case', ['confirm', 'cancel', 'changed', 'foreign', 'withdraw', 'consent', 'expired'])
async def test_rpc_saved_discard_two_phase_exact_receipt_never_mutates_history(tmp_path, case, monkeypatch):
    import xdog.coding.modes.rpc.children as module
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    from xdog.tui.child_drafts import ChildDraft
    session, adapter, nav, sources, children, events = setup(tmp_path)
    store = ChildDraftStore(session.session_manager._dir / 'saved-child-drafts')
    store.root.parent.mkdir(exist_ok=True)
    saved = store.save(encode_draft(ChildDraft('UNSENT SAVED TASK'), label='source',
                                   scope='old', action='start', slot=''))
    def result(key):
        return next(row for row in events if row.get('request_id') == key and row['type'] == 'child_result')
    try:
        with pytest.raises(ValueError, match='child_saved_discard:1'):
            children.start(packet(children, 'no-cap', action='saved_discard_prepare',
                                  snapshot_id=saved.id, snapshot_sha256=saved.sha256),
                           turn_running=False, extension_running=False)
        children.negotiate({'child_saved_drafts': 1, 'child_saved_discard': 1})
        await control(children, 'saved_discard_prepare', 'prepare-delete',
                      snapshot_id=saved.id, snapshot_sha256=saved.sha256)
        prepared = result('prepare-delete')
        assert prepared['success'] and store.load(saved.id).sha256 == saved.sha256
        review_id, review = prepared['review_id'], prepared['review']
        fields = {'review_id': review_id, 'review': review, 'confirmed': True}
        if case == 'cancel':
            await control(children, 'saved_discard_cancel', 'cancel-review', review_id=review_id)
        elif case == 'changed':
            (store.root / (saved.id + '.json')).write_bytes(encode_draft(
                ChildDraft('CHANGED'), label='other', scope='old', action='start', slot=''))
        elif case == 'foreign':
            fields['review_id'] = '0' * 32
        elif case == 'withdraw':
            children.negotiate({'child_saved_discard': 0})
            children.negotiate({'child_saved_discard': 1})
        elif case == 'consent':
            fields['confirmed'] = 1  # Must be actual bool True, not truthy.
        elif case == 'expired':
            prior = children.saved_discards[review_id]
            children.saved_discards[review_id] = prior[0], prior[1], module.time.monotonic() - 1, prior[3]
        await control(children, 'saved_discard_confirm', 'confirm-delete', **fields)
        confirmed = result('confirm-delete')
        if case == 'confirm':
            assert confirmed['success'] and confirmed['discarded_snapshot_id'] == saved.id
            assert not store.catalog()
        else:
            assert not confirmed['success']
            assert store.load(saved.id).draft.text == ('CHANGED' if case == 'changed' else 'UNSENT SAVED TASK')
        assert not session.messages and not adapter.contexts
        assert not session.children.supervisor.snapshot()['children']
    finally:
        await close(session, nav, sources, children)


async def test_saved_discard_revocation_while_waiting_for_disk_cannot_delete(tmp_path, monkeypatch):
    import threading

    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    from xdog.tui.child_drafts import ChildDraft
    session, adapter, nav, sources, children, events = setup(tmp_path)
    store = ChildDraftStore(session.session_manager._dir / 'saved-child-drafts')
    store.root.parent.mkdir(exist_ok=True)
    saved = store.save(encode_draft(ChildDraft('KEEP WHILE REVOKED'), label='source',
                                   scope='old', action='start', slot=''))
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.discard
    def held(self, key, digest, *, alive=lambda: True):
        entered.set()
        assert release.wait(5)
        original(self, key, digest, alive=alive)
    try:
        children.negotiate({'child_saved_drafts': 1, 'child_saved_discard': 1})
        await control(children, 'saved_discard_prepare', 'prep', snapshot_id=saved.id, snapshot_sha256=saved.sha256)
        prepared = next(row for row in events if row.get('request_id') == 'prep' and row['type'] == 'child_result')
        monkeypatch.setattr(ChildDraftStore, 'discard', held)
        children.start(packet(children, 'held-delete', action='saved_discard_confirm', review_id=prepared['review_id'],
                              review=prepared['review'], confirmed=True), turn_running=False, extension_running=False)
        work = children.jobs['held-delete'][0]
        assert await asyncio.to_thread(entered.wait, 3)
        children.negotiate({'child_saved_discard': 0})
        await asyncio.sleep(0.02)
        assert not work.done()  # Actual worker must settle; cancellation cannot detach removal ownership.
        release.set()
        await work
        assert store.load(saved.id).sha256 == saved.sha256 and not session.messages and not adapter.contexts
        failed = next(row for row in events if row.get('request_id') == 'held-delete' and row['type'] == 'child_result')
        assert not failed['success']
    finally:
        release.set()
        await close(session, nav, sources, children)


@pytest.mark.parametrize('case', ['text', 'image', 'cancel', 'consent', 'withdraw', 'expired'])
async def test_rpc_saved_creation_needs_independent_exact_review_and_never_executes(tmp_path, case):
    from xdog.tui.child_draft_store import ChildDraftStore
    session, adapter, nav, sources, children, events = setup(tmp_path)
    store = ChildDraftStore(session.session_manager._dir / 'saved-child-drafts')
    def result(key):
        return next(row for row in events if row.get('request_id') == key and row['type'] == 'child_result')
    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='
    fields = {'text': 'REMOTE UNSENT /quit', 'label': 'reviewed source'}
    if case == 'image':
        fields['images'] = [{'data': png, 'mime_type': 'image/png'}]
    try:
        with pytest.raises(ValueError, match='child_saved_save:1'):
            children.start(packet(children, 'no-save-cap', action='saved_save_prepare', **fields),
                           turn_running=False, extension_running=False)
        children.negotiate({'child_saved_drafts': 1, 'child_saved_save': 1})
        if case == 'image':
            with pytest.raises(ValueError):
                children.start(packet(children, 'no-image-cap', action='saved_save_prepare', **fields),
                               turn_running=False, extension_running=False)
            children.negotiate({'child_saved_images': 1})
        await control(children, 'saved_save_prepare', 'prepare-save', **fields)
        prepared = result('prepare-save')
        assert prepared['success'] and not store.catalog()
        assert prepared['review']['content'] == fields['text']
        confirm = {'review_id': prepared['review_id'], 'review': prepared['review'], 'confirmed': True}
        if case == 'cancel':
            await control(children, 'saved_save_cancel', 'cancel-save', review_id=prepared['review_id'])
        elif case == 'consent':
            confirm['confirmed'] = 'true'
        elif case == 'withdraw':
            children.negotiate({'child_saved_save': 0})
            children.negotiate({'child_saved_save': 1})
        elif case == 'expired':
            old = children.saved_saves[prepared['review_id']]
            children.saved_saves[prepared['review_id']] = old[0], old[1], 0, old[3], old[4]
        await control(children, 'saved_save_confirm', 'confirm-save', **confirm)
        saved = result('confirm-save')
        if case in {'text', 'image'}:
            assert saved['success']
            loaded = store.load(saved['snapshot_id'])
            assert loaded.sha256 == prepared['review']['snapshot_sha256'] == saved['snapshot_sha256']
            assert loaded.draft.text == fields['text'] and loaded.draft.access == 'read-only'
            assert loaded.draft.config == {'inherit_context': False}
            assert bool(loaded.draft.images) is (case == 'image')
            await control(children, 'saved_save_confirm', 'repeat-save', **confirm)
            assert result('repeat-save')['success'] is False and len(store.catalog()) == 1
        else:
            assert not saved['success'] and not store.catalog()
        assert not session.messages and not adapter.contexts and not session.children.supervisor.active
    finally:
        await close(session, nav, sources, children)


async def test_saved_creation_revoked_at_publication_keeps_no_snapshot(tmp_path, monkeypatch):
    import threading

    from xdog.tui.child_draft_store import ChildDraftStore
    session, adapter, nav, sources, children, events = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = ChildDraftStore.save
    def held(self, data, *, alive=lambda: True):
        entered.set()
        assert release.wait(5)
        return original(self, data, alive=alive)
    try:
        children.negotiate({'child_saved_drafts': 1, 'child_saved_save': 1})
        await control(children, 'saved_save_prepare', 'prep-save', text='DO NOT PUBLISH')
        prepared = next(row for row in events if row.get('request_id') == 'prep-save' and row['type'] == 'child_result')
        monkeypatch.setattr(ChildDraftStore, 'save', held)
        children.start(packet(children, 'held-save', action='saved_save_confirm', review_id=prepared['review_id'],
                              review=prepared['review'], confirmed=True), turn_running=False, extension_running=False)
        task = children.jobs['held-save'][0]
        assert await asyncio.to_thread(entered.wait, 3)
        children.negotiate({'child_saved_save': 0})
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        await task
        store = ChildDraftStore(session.session_manager._dir / 'saved-child-drafts')
        assert not store.catalog() and not list(store.root.glob('.draft-*'))
        assert not session.messages and not adapter.contexts
    finally:
        release.set()
        await close(session, nav, sources, children)
