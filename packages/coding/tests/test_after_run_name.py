"""Reviewed name persistence cannot race the original producer's transcript commit."""
from __future__ import annotations

import asyncio

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.rpc_mode import _handle_prompt

from packages.coding.tests.test_after_run_compaction import review
from packages.coding.tests.test_rpc_extensions import close, response, session


@pytest.mark.parametrize('host', ['native', 'rpc'])
@pytest.mark.parametrize('ending', ['apply', 'callback-fail', 'producer-fail', 'withdraw', 'name-change'])
async def test_original_producer_settles_before_reviewed_name_cas_preserving_final_transcript(tmp_path, host, ending):
    owner, calls = session(tmp_path, extra=('aftername',))
    owner.extensions.settings['aftername'] = {'fail': ending == 'callback-fail'}
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='ORIGINAL FINAL TRANSCRIPT'),)))
    owner.agent.set_stream_fn(stream)
    held, release = False, False
    original = owner.extensions.event
    async def event(name, *args, **kwargs):
        nonlocal held
        await original(name, *args, **kwargs)
        if name == 'turn_end':
            held = True
            while not release:
                await asyncio.sleep(.003)
            if ending == 'producer-fail':
                raise ValueError('Owned producer cleanup failure')
    owner.extensions.event = event
    ui, control, nav, task = None, None, None, None
    events = []
    async def wait(predicate):
        async with asyncio.timeout(10):
            while True:
                if ui is not None:
                    ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        if host == 'native':
            ui = InteractiveMode(owner)
            ui._editor.set_text('UNSENT DRAFT')
            ui._start_turn('explicit original input')
            await wait(lambda: ui._extension_dialog_panel is not None)
            assert ui._extension_dialog_panel.dialog['name_after_run'] is True
            review(ui._extension_dialog_panel)
        else:
            nav = RpcNavigation(owner, events.append)
            control = RpcExtensions(owner, events.append, navigation=nav)
            control.negotiate({'extension_name_after_run': 1})
            assert not control.after_run_compaction.enabled and not control.name_enabled
            async def prompt():
                async with control.bind('original'):
                    await _handle_prompt(owner, 'explicit original input', events.append)
            task = asyncio.create_task(prompt())
            await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
            dialog = next(row for row in events if row['type'] == 'extension_dialog')
            assert dialog['dialog']['name_after_run'] is True
            control.answer(response(dialog, True))
        if ending != 'callback-fail':
            await wait(lambda: held)
        assert owner.session_data.name == '' and len(calls) == 1
        if ending == 'withdraw':
            if control is not None:
                control.negotiate({'extension_name_after_run': 0})
                control.negotiate({'extension_name_after_run': 1})
            else:
                ui._cancel_epoch += 1
        elif ending == 'name-change':
            owner.session_data.name = 'OTHER ORIGINAL NAME'
        release = True
        if task is not None:
            await task
        else:
            await wait(lambda: not ui._worker_active and ui._worker_thread is not None
                       and not ui._worker_thread.is_alive())
        for _ in range(30):
            if ui is not None:
                ui._poll()
            await asyncio.sleep(.003)
        if ending == 'apply':
            await wait(lambda: owner.session_data.name == 'AFTER-RUN-NAME')
        if nav is not None and nav.task is not None:
            await nav.task
        if ui is not None:
            await wait(lambda: ui._command is None)
            assert ui._editor.get_text() == 'UNSENT DRAFT'
        assert owner.session_data.name == ('AFTER-RUN-NAME' if ending == 'apply' else
                                          'OTHER ORIGINAL NAME' if ending == 'name-change' else '')
        assert len(calls) == 1 and owner.permissions.mode == 'deny'
        if ending == 'apply':
            saved = owner.session_manager.load_session(owner.session_id)
            assert saved.name == 'AFTER-RUN-NAME'
            assert 'ORIGINAL FINAL TRANSCRIPT' in str(saved.messages) and len(saved.messages) == len(owner.messages)
    finally:
        release = True
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if ui is not None:
            ui._request_exit()
        if control is not None:
            await control.aclose()
        if nav is not None:
            await nav.aclose()
        await close(owner)


def test_actual_cli_name_only_capability_review_preserves_original_final_transcript(tmp_path):
    import json
    import shutil
    from pathlib import Path

    from xdog.coding.core.session_manager import SessionManager

    from packages.coding.tests.test_rpc_children_cli import Peer
    root, home = tmp_path / 'extensions', tmp_path / 'home'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/aftername', root / 'aftername')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['aftername'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_name_after_run': 1})
        caps = peer.read('rpc_capabilities')
        assert caps['extension_name_after_run'] == 1 and caps['extension_session_name'] == 0
        assert caps['extension_compaction_after_run'] == 0
        peer.send({'type': 'prompt', 'request_id': 'original-name', 'session_id': peer.ready['session_id'],
                   'message': 'Explicit original input'})
        question = peer.read('extension_dialog')
        assert question['dialog']['name_after_run'] is True and question['dialog']['expected_name'] == ''
        peer.send(response(question, True))
        ended = peer.read('message_end')
        persisted = peer.read('session_result')
        assert persisted['success'] and peer.events.index(ended) < peer.events.index(persisted)
        saved = SessionManager(home / 'sessions').load_session(peer.ready['session_id'])
        assert saved.name == 'AFTER-RUN-NAME' and 'RPC-OFFLINE-ANSWER' in str(saved.messages)
        assert len(saved.messages) == 2
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()


async def test_before_replace_revocation_refuses_name_save_without_overwriting_transcript(tmp_path, monkeypatch):
    import threading
    owner, _ = session(tmp_path)
    owner.agent.append_message(AssistantMessage(content=(TextContent(text='KEEP FINAL TRANSCRIPT'),)))
    owner._persist()
    before = owner.session_manager.load_session(owner.session_id)
    live, entered, release = [True], threading.Event(), threading.Event()
    save = owner.session_manager.save_session
    def held(data, *, before_replace=None):
        def guard():
            entered.set()
            assert release.wait(5)
            before_replace()
        save(data, before_replace=guard)
    monkeypatch.setattr(owner.session_manager, 'save_session', held)
    task = asyncio.create_task(asyncio.to_thread(owner.rename_session, 'MUST NOT SAVE',
                                                expected_name='', alive=lambda: live[0]))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.003)
        live[0] = False
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        after = owner.session_manager.load_session(owner.session_id)
        assert after.name == before.name == '' and after.messages == before.messages
        assert owner.session_data.name == ''
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await close(owner)
