from __future__ import annotations

import asyncio

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.rpc_mode import _handle_prompt
from xdog.tui.keys import KeyEvent

from packages.coding.tests.test_rpc_compaction import history
from packages.coding.tests.test_rpc_extensions import close, session


def review(panel):
    panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))  # Unpainted confirmation must not apply.
    for _ in range(200):
        panel.render(90)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter'))


@pytest.mark.parametrize('ending', ['apply', 'callback-error', 'cancel', 'source-change'])
async def test_native_reviewed_compaction_waits_for_real_producer_finalization(tmp_path, ending):
    owner, _ = session(tmp_path, extra=('aftercompact',))
    history(owner)
    owner.extensions.settings['aftercompact'] = {'fail': ending == 'callback-error'}
    calls = []
    def stream(model, context, options):
        calls.append(context)
        text = 'ANSWER' if len(calls) == 1 else 'SUMMARY'
        return EventStream.empty(AssistantMessage(content=(TextContent(text=text),)))
    owner.agent.set_stream_fn(stream)
    ui = InteractiveMode(owner)
    original = owner.extensions.event
    held, release = False, False
    async def event(name, *args, **kwargs):
        nonlocal held
        await original(name, *args, **kwargs)
        if name == 'turn_end':
            held = True
            while not release:
                await asyncio.sleep(.005)
    owner.extensions.event = event
    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(.003)
    try:
        ui._editor.set_text('UNSENT NATIVE DRAFT')
        ui._start_turn('original run')
        await wait(lambda: ui._extension_dialog_panel is not None)
        assert ui._extension_dialog_panel.dialog['after_run'] is True and len(calls) == 1
        review(ui._extension_dialog_panel)
        if ending == 'callback-error':
            release = True
            await wait(lambda: not ui._worker_active)
            assert len(calls) == 1 and owner.session_data.compaction is None
        else:
            await wait(lambda: held)
            assert len(calls) == 1 and owner.session_data.compaction is None
            if ending == 'cancel':
                ui._cancel_requested = True
                ui._cancel_epoch += 1
            elif ending == 'source-change':
                owner.extensions.fail('aftercompact')
            release = True
            await wait(lambda: not ui._worker_active and not ui._is_busy)
            await wait(lambda: ui._worker_thread is not None and not ui._worker_thread.is_alive())
            for _ in range(30):
                ui._poll()
                await asyncio.sleep(.005)
            if ending == 'apply':
                await wait(lambda: ui._command is None and owner.session_data.compaction is not None)
                assert len(calls) == 2 and not calls[1].tools
            else:
                assert len(calls) == 1 and owner.session_data.compaction is None
        assert ui._editor.get_text() == 'UNSENT NATIVE DRAFT' and owner.permissions.mode == 'deny'
    finally:
        release = True
        ui._request_exit()
        await close(owner)


@pytest.mark.parametrize('ending', ['apply', 'old-client', 'withdraw', 'producer-error'])
async def test_rpc_after_run_capability_and_task_barrier_are_independent_of_command_compaction(tmp_path, ending):
    owner, _ = session(tmp_path, extra=('aftercompact',))
    history(owner)
    calls, events = [], []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='ANSWER'),)))
    owner.agent.set_stream_fn(stream)
    nav = RpcNavigation(owner, events.append)
    control = RpcExtensions(owner, events.append, navigation=nav)
    control.negotiate({'extension_compaction_after_run': int(ending != 'old-client')})
    held, release = asyncio.Event(), asyncio.Event()
    original = owner.extensions.event
    async def event(name, *args, **kwargs):
        await original(name, *args, **kwargs)
        if name == 'turn_end':
            held.set()
            await release.wait()
            if ending == 'producer-error':
                raise ValueError('Producer finalization failed')
    owner.extensions.event = event
    async def prompt():
        async with control.bind('original-run'):
            await _handle_prompt(owner, 'original run', events.append)
    task = asyncio.create_task(prompt())
    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                await asyncio.sleep(.003)
    try:
        if ending != 'old-client':
            await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
            event_row = next(row for row in events if row['type'] == 'extension_dialog')
            assert event_row['dialog']['after_run'] is True and not control.compaction_enabled
            control.answer({'id': event_row['dialog']['id'], 'request_id': event_row['request_id'],
                'session_id': event_row['session_id'], 'scope': event_row['scope'], 'value': True})
        await asyncio.wait_for(held.wait(), 8)
        assert len(calls) == 1 and owner.session_data.compaction is None
        if ending == 'withdraw':
            control.negotiate({'extension_compaction_after_run': 0})
            control.negotiate({'extension_compaction_after_run': 1})
        release.set()
        await task
        await asyncio.sleep(.05)
        if nav.task is not None:
            await nav.task
        assert (owner.session_data.compaction is not None) is (ending == 'apply')
        assert len(calls) == (2 if ending == 'apply' else 1)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await control.aclose()
        await nav.aclose()
        await close(owner)


async def test_canonical_compaction_source_revocation_after_summary_does_not_commit_old_review(tmp_path):
    from packages.coding.tests.test_rpc_compaction import summary_stream
    owner, _ = session(tmp_path)
    original = history(owner)
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    owner.agent.set_stream_fn(summary_stream(entered, release, stopped, calls))
    live = [True]
    task = asyncio.create_task(owner.compact('original reviewed source', lambda: live[0]))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        live[0] = False
        release.set()
        with pytest.raises(RuntimeError, match='owner changed'):
            await task
        assert owner.messages == original and owner.session_data.compaction is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await close(owner)


@pytest.mark.parametrize('enabled', [False, True])
def test_actual_jsonl_reader_parent_run_then_separate_canonical_compaction(tmp_path, monkeypatch, enabled):
    import json
    import shutil
    from pathlib import Path

    from xdog.ai.types import UserMessage
    from xdog.coding.core.session_manager import SessionManager

    from packages.coding.tests.test_rpc_children_cli import Peer
    root, home = tmp_path / 'extensions', tmp_path / 'home'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/aftercompact', root / 'aftercompact')
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['aftercompact'], 'extension_dir': str(root)}))
    count = tmp_path / 'rpc-count'
    monkeypatch.setenv('XDOG_RPC_COUNT_FILE', str(count))
    peer = Peer(tmp_path)
    try:
        store = SessionManager(home / 'sessions')
        target = store.create_session(model='rpc-fixture', working_dir=tmp_path)
        for index in range(6):
            target.messages.extend([UserMessage(content=f'old work {index} ' + 'x' * 2000),
                                    AssistantMessage(content=(TextContent(text='old answer'),))])
        store.save_session(target)
        peer.send({'type': 'session_control', 'action': 'resume', 'request_id': 'load',
            'expected_session': peer.ready['session_id'], 'session_revision': peer.ready['session_revision'],
            'target_session': target.session_id})
        loaded = peer.read('session_result')
        assert loaded['success']
        peer.send({'type': 'rpc_hello', 'extension_compaction': 1,
                   'extension_compaction_after_run': int(enabled)})
        capability = peer.read('rpc_capabilities')
        assert capability['extension_compaction_after_run'] == int(enabled)
        peer.send({'type': 'prompt', 'message': 'original producer', 'request_id': 'parent',
                   'session_id': target.session_id, 'session_revision': loaded['session_revision']})
        if enabled:
            event = peer.read('extension_dialog')
            assert event['dialog']['after_run'] is True and count.read_text() == '1'
            peer.send({'type': 'extension_dialog_response', 'id': event['dialog']['id'],
                'request_id': event['request_id'], 'session_id': event['session_id'],
                'scope': event['scope'], 'value': True})
        ended = peer.read('message_end')
        if enabled:
            completed = peer.read('session_result')
            assert completed['action'] == 'compact' and completed['success']
            assert peer.events.index(ended) < peer.events.index(completed)
            assert count.read_text() == '2' and store.load_session(target.session_id).compaction is not None
        else:
            assert count.read_text() == '1' and store.load_session(target.session_id).compaction is None
        peer.send({'type': 'quit'})
        peer.read('quit_ack')
        assert peer.process.wait(timeout=6) == 0
    finally:
        peer.close()


@pytest.mark.parametrize('kind', ['rpc', 'claw'])
async def test_compaction_capability_revoke_does_not_cancel_other_after_run_reviews(tmp_path, kind):
    from types import SimpleNamespace

    from xdog.agent.extensions.dialogs import DialogBroker
    from xdog.claw.core.runtime.after_run_compaction import AfterRunCompactionConnection
    broker = DialogBroker()
    notices = []
    broker.publish = notices.append
    pending = [asyncio.create_task(broker.ask('local', {
        'id': 'unused', 'kind': 'session', 'operation': 'rename', 'title': 'Rename',
        'name': 'new name', 'expected_name': '', 'after_run': True})),
        asyncio.create_task(broker.ask('local', {
        'id': 'unused', 'kind': 'session', 'operation': 'select_tools', 'title': 'Select tools',
        'names': [], 'revision': 0, 'after_run': True}))]
    async def send(event):
        return None
    controller = None
    owner = None
    try:
        await asyncio.sleep(0)
        assert len(broker._pending) == 2
        if kind == 'rpc':
            owner, _ = session(tmp_path)
            control = RpcExtensions(owner, lambda event: None)
            owner.extension_dialogs = broker
            control.after_run_compaction.negotiate(False)
            controller = control
        else:
            controller = AfterRunCompactionConnection(SimpleNamespace(), send, lambda: True)
            marker = controller.create('group', 'run')
            assert marker is not None  # Read-only observation does not require a compact grant.
            marker.session = SimpleNamespace(extension_dialogs=broker)
            controller.negotiate(False)
        await asyncio.sleep(0)
        assert all(not task.done() for task in pending) and len(broker._pending) == 2
    finally:
        broker.cancel_all()
        await asyncio.gather(*pending, return_exceptions=True)
        if controller is not None:
            await (controller.aclose() if kind == 'rpc' else controller.close())
        if owner is not None:
            await close(owner)
