"""Actual RPC worker/client review; only a scoped apply request, never a claimed remote mutation."""
from __future__ import annotations

import asyncio

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.tui.editor_write import EditorWriteReviews

from packages.coding.tests.test_rpc_extensions import close, response, session


async def wait(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.003)


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'withdraw', 'text', 'old-client', 'emit-fail'])
async def test_actual_rpc_write_only_proposal_independent_client_cas_and_successful_result_delivery(tmp_path, ending):
    owner, calls = session(tmp_path, extra=('editorwrite',))
    events, draft, editor = [], ['CLIENT PRIVATE'], EditorWriteReviews()
    async def send(event):
        if ending == 'emit-fail' and event['type'] == 'extension_result':
            raise OSError('owned output closed')
        events.append(event)
    navigation = RpcNavigation(owner, events.append)
    rpc = RpcExtensions(owner, events.append, navigation=navigation, send=send)
    rpc.negotiate({'extension_editor': 1} if ending == 'old-client' else {'extension_editor_write': 1})
    def replace(text):
        draft[0] = text
        return True
    try:
        rpc.start({'request_id': 'write', 'session_id': owner.session_id, 'action': 'command',
                   'name': 'ext-editorwrite-write', 'args': 'fail' if ending == 'fail' else ''}, turn_running=False)
        if ending != 'old-client':
            await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
            question = next(event for event in events if event['type'] == 'extension_dialog')
            dialog = question['dialog']
            assert 'CLIENT PRIVATE' not in str(question) and dialog['operation'] == 'propose'
            assert editor.prepare(dialog, lambda: (draft[0], len(draft[0]), ('image-owned',)), replace, lambda: True)
            receipt = editor.answer(dialog, ending != 'deny')
            with pytest.raises(ValueError, match='foreign'):
                rpc.answer({**response(question, receipt), 'scope': 'foreign'})
            if ending == 'withdraw':
                rpc.negotiate({'extension_editor_write': 0})
                rpc.negotiate({'extension_editor_write': 1})
            else:
                if ending == 'text':
                    draft[0] = 'USER CHANGE'
                rpc.answer(response(question, receipt))
        if ending == 'emit-fail':
            with pytest.raises(OSError):
                await rpc.task
        else:
            await rpc.task
        apply = [event for event in events if event['type'] == 'extension_editor_write_apply']
        assert len(apply) == (1 if ending in {'apply', 'text'} else 0)
        if apply:
            assert events.index(apply[0]) > next(i for i, event in enumerate(events)
                                                if event['type'] == 'extension_result')
            assert editor.apply(apply[0]) == (ending == 'apply')
            assert not editor.apply(apply[0])
        assert draft[0] == ('PROPOSED 界\nunsent' if ending == 'apply' else
                            'USER CHANGE' if ending == 'text' else 'CLIENT PRIVATE')
        assert not calls and not owner.messages and owner.permissions.mode == 'deny'
    finally:
        await rpc.aclose()
        await navigation.aclose()
        await close(owner)


async def test_independent_proposal_negotiation_preserves_editor_receipts_and_invalid_batches_are_atomic(tmp_path):
    owner, _ = session(tmp_path)
    rpc = RpcExtensions(owner, lambda event: None)
    try:
        rpc.negotiate({'extension_editor': 1, 'extension_dialogs': 1})
        rpc.editor_revisions['original'] = 'a' * 64
        rpc.negotiate({'extension_editor_write': 1})
        assert rpc.editor_enabled and rpc.dialogs_enabled and rpc.editor_revisions == {'original': 'a' * 64}
        generation = rpc.editor_writes.generation
        with pytest.raises(ValueError):
            rpc.negotiate({'extension_editor_write': 0, 'extension_editor': True})
        assert rpc.editor_writes.enabled and rpc.editor_writes.generation == generation
        rpc.negotiate({'extension_editor_write': 0})
        assert rpc.editor_enabled and rpc.dialogs_enabled and rpc.editor_revisions == {'original': 'a' * 64}
    finally:
        await rpc.aclose()
        await close(owner)


def test_actual_cli_independent_editor_write_review_then_result_then_apply_no_model(tmp_path):
    import json
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/editorwrite', root / 'editorwrite')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['editorwrite'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        assert peer.ready['capabilities']['extension_editor_write'] == 1
        peer.send({'type': 'rpc_hello', 'extension_editor_write': 1})
        caps = peer.read('rpc_capabilities')
        assert caps['extension_editor_write'] == 1 and caps['extension_editor'] == 0
        peer.send({'type': 'extension_control', 'request_id': 'write', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-editorwrite-write'})
        question = peer.read('extension_dialog')
        assert question['dialog']['operation'] == 'propose'
        peer.send(response(question, {'approved': True, 'revision': 'a' * 64}))
        result = peer.read('extension_result')
        assert result['success'] and result['output'] == 'EDITOR-WRITE-True'
        apply = peer.read('extension_editor_write_apply')
        assert apply['editor_write'] == question['dialog']['editor_write']
        assert apply['revision'] == 'a' * 64 and apply['text'] == 'PROPOSED 界\nunsent'
        assert peer.events.index(result) < peer.events.index(apply) and not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()


@pytest.mark.parametrize('ending', ['apply', 'withdraw', 'cancel', 'write-fail'])
async def test_real_rpc_callback_cleanup_and_held_result_write_boundaries(tmp_path, ending):
    owner, calls = session(tmp_path, extra=('editorwrite',))
    marker, release = tmp_path / 'accepted', tmp_path / 'cleanup'
    owner.extensions.settings['editorwrite'] = {'marker': str(marker), 'release': str(release)}
    events, writing, finish_write = [], asyncio.Event(), asyncio.Event()
    async def send(event):
        if event['type'] == 'extension_result':
            writing.set()
            await finish_write.wait()
            if ending == 'write-fail':
                raise OSError('controlled failed whole-line delivery')
        events.append(event)
    rpc = RpcExtensions(owner, events.append, send=send)
    rpc.negotiate({'extension_editor_write': 1})
    try:
        rpc.start({'request_id': 'held', 'session_id': owner.session_id, 'action': 'command',
                   'name': 'ext-editorwrite-write'}, turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        question = next(event for event in events if event['type'] == 'extension_dialog')
        rpc.answer(response(question, {'approved': True, 'revision': 'a' * 64}))
        await wait(marker.exists)
        assert not any(event['type'] == 'extension_editor_write_apply' for event in events)
        release.write_text('callback finally may finish')
        await asyncio.wait_for(writing.wait(), 5)
        assert not any(event['type'] == 'extension_editor_write_apply' for event in events)
        if ending == 'withdraw':
            rpc.negotiate({'extension_editor_write': 0})
            rpc.negotiate({'extension_editor_write': 1})
        elif ending == 'cancel':
            rpc.task.cancel()
        finish_write.set()
        if ending == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await rpc.task
        elif ending == 'write-fail':
            with pytest.raises(OSError):
                await rpc.task
        else:
            await rpc.task
        assert sum(event['type'] == 'extension_editor_write_apply' for event in events) == int(ending == 'apply')
        assert not calls and not owner.messages and owner.permissions.mode == 'deny'
    finally:
        release.write_text('settle')
        finish_write.set()
        await rpc.aclose()
        await close(owner)
