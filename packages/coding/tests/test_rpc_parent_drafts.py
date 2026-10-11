"""Independent parent snapshot negotiation, no composer/model/permission mutation."""
from __future__ import annotations

import hashlib
import json

import pytest
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.parent_drafts import RpcParentDrafts
from xdog.tui.child_drafts import draft_receipt

from packages.coding.tests.test_rpc_extensions import close, session


def composer(text, revision=1):
    return {'revision': revision, 'empty': not text, 'sha256': hashlib.sha256(json.dumps(
        draft_receipt(text, ()), sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


async def test_parent_snapshot_save_catalog_read_discard_exact_review_and_no_execution(tmp_path):
    owner, calls = session(tmp_path)
    events = []
    async def send(row):
        events.append(row)
    controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
    owner.session_manager._dir.chmod(0o700)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1, 'parent_saved_discard': 1})
    async def control(rid, action, **fields):
        controller.start({'request_id': rid, **controller.envelope(), 'action': action, **fields}, blocked=False)
        await controller.task
        row = next(row for row in events if row.get('request_id') == rid and row['type'] == 'parent_draft_result')
        return row
    try:
        receipt = composer('PRIVATE PARENT')
        prepared = await control('prepare', 'save_prepare', text='PRIVATE PARENT', composer=receipt)
        assert prepared['success'] and not (owner.session_manager._dir / 'saved-parent-drafts').exists()
        rejected = await control('changed', 'save_confirm', composer=composer('CHANGED', 2),
            review_id=prepared['review_id'], review=prepared['review'], confirmed=True)
        assert not rejected['success'] and not (owner.session_manager._dir / 'saved-parent-drafts').exists()
        saved = await control('save', 'save_confirm', composer=receipt, review_id=prepared['review_id'],
                              review=prepared['review'], confirmed=True)
        assert saved['success']
        replay = await control('replay', 'save_confirm', composer=receipt, review_id=prepared['review_id'],
                               review=prepared['review'], confirmed=True)
        assert not replay['success']
        catalog = await control('catalog', 'catalog')
        assert catalog['success'] and len(catalog['catalog']) == 1
        assert 'PRIVATE PARENT' not in str(catalog)
        fields = {'snapshot_id': saved['snapshot_id'], 'snapshot_sha256': saved['snapshot_sha256']}
        with pytest.raises(ValueError, match='empty'):
            controller.start({'request_id': 'nonempty', **controller.envelope(), 'action': 'read',
                              'composer': receipt, **fields}, blocked=False)
        read = await control('read', 'read', composer=composer(''), **fields)
        assert read['success'] and read['recovery']['content'] == 'PRIVATE PARENT'
        assert [row['type'] for row in events if row.get('request_id') == 'read'] == [
            'parent_draft_begin', 'parent_draft_end', 'parent_draft_result']
        discard = await control('discard-prep', 'discard_prepare', composer=receipt, **fields)
        assert discard['success']
        done = await control('discard', 'discard_confirm', composer=receipt, review_id=discard['review_id'],
                             review=discard['review'], confirmed=True)
        assert done['success'] and not list((owner.session_manager._dir / 'saved-parent-drafts').glob('*.json'))
        assert not calls and not owner.agent.messages and owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await close(owner)


async def test_parent_capability_withdrawal_revokes_prepared_receipts_and_foreign_owner(tmp_path):
    owner, calls = session(tmp_path)
    events = []
    async def send(row):
        events.append(row)
    controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
    try:
        with pytest.raises(ValueError, match='Negotiate'):
            controller.start({'request_id': 'old', **controller.envelope(), 'action': 'catalog'}, blocked=False)
        controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
        old = controller.envelope()
        controller.start({'request_id': 'prepare', **old, 'action': 'save_prepare',
                          'text': 'PRIVATE', 'composer': composer('PRIVATE')}, blocked=False)
        await controller.task
        assert events[-1]['success'] and controller.reviews
        controller.negotiate({'parent_saved_drafts': 0})
        assert not controller.reviews
        controller.negotiate({'parent_saved_drafts': 1})
        with pytest.raises(ValueError, match='changed'):
            controller.start({'request_id': 'stale', **old, 'action': 'catalog'}, blocked=False)
        with pytest.raises(ValueError, match='changed'):
            controller.start({'request_id': 'foreign', **controller.envelope(),
                              'parent_draft_owner': 'foreign', 'action': 'catalog'}, blocked=False)
        assert not calls
    finally:
        await controller.aclose()
        await close(owner)


def test_actual_cli_parent_draft_independent_negotiation_and_save_receipt(tmp_path):
    from packages.coding.tests.test_rpc_children_cli import Peer
    peer = Peer(tmp_path)
    try:
        assert peer.events[0]['capabilities']['parent_saved_drafts'] == 1
        peer.send({'type': 'rpc_hello', 'parent_saved_drafts': 1, 'parent_saved_save': 1})
        caps = peer.read('parent_draft_capabilities')
        scope = {key: caps[key] for key in ('session_id', 'session_revision', 'parent_draft_owner',
                                          'parent_draft_generation')}
        receipt = composer('CLI PRIVATE PARENT')
        peer.send({'type': 'parent_draft_control', **scope, 'request_id': 'prepare', 'action': 'save_prepare',
                   'text': 'CLI PRIVATE PARENT', 'composer': receipt})
        prepared = peer.read('parent_draft_result')
        assert prepared['success'], prepared
        peer.send({'type': 'parent_draft_control', **scope, 'request_id': 'confirm', 'action': 'save_confirm',
                   'review_id': prepared['review_id'], 'review': prepared['review'],
                   'composer': receipt, 'confirmed': True})
        saved = peer.read('parent_draft_result')
        assert saved['success'], saved
        peer.send({'type': 'parent_draft_control', **scope, 'request_id': 'catalog', 'action': 'catalog'})
        catalog = peer.read('parent_draft_result')
        assert catalog['success'] and catalog['catalog'][0]['id'] == saved['snapshot_id']
        from xdog.tui.child_drafts import ChildDraft
        from xdog.tui.keys import KeyEvent
        from xdog.tui.parent_draft_receiver import ParentDraftReceiver, composer_receipt

        from packages.tui.tests.test_parent_draft_receiver import paint
        receiver = ParentDraftReceiver()
        read_scope = {'request_id': 'read', **scope}
        empty = composer_receipt(ChildDraft(), 2)
        receiver.expect(read_scope, empty, saved['snapshot_id'], saved['snapshot_sha256'])
        peer.send({'type': 'parent_draft_control', **read_scope, 'action': 'read', 'composer': empty,
                   'snapshot_id': saved['snapshot_id'], 'snapshot_sha256': saved['snapshot_sha256']})
        receiver.feed(peer.read('parent_draft_begin'))
        receiver.feed(peer.read('parent_draft_end'))
        recovery = receiver.finish('read', peer.read('parent_draft_result'))
        adopted = []
        panel = recovery.panel(lambda: read_scope, lambda: empty, lambda *args: adopted.append(args), lambda: None)
        panel.focused = True
        panel.handle_input(KeyEvent('enter'))
        assert not adopted
        paint(panel)
        panel.handle_input(KeyEvent('enter'))
        assert adopted[0][0].text == 'CLI PRIVATE PARENT'
        receiver.close()
        assert not peer.marker.exists()
        peer.send({'type': 'quit'})
        peer.read('quit_ack')
        assert peer.process.wait(timeout=6) == 0
    finally:
        peer.close()


async def test_parent_review_cannot_cross_session_revision_even_with_new_envelope(tmp_path):
    owner, calls = session(tmp_path)
    events = []
    async def send(row):
        events.append(row)
    navigation = RpcNavigation(owner, events.append)
    controller = RpcParentDrafts(owner, navigation, send)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    try:
        receipt = composer('PRIVATE')
        controller.start({'request_id': 'prepare', **controller.envelope(), 'action': 'save_prepare',
                          'text': 'PRIVATE', 'composer': receipt}, blocked=False)
        await controller.task
        prepared = events[-1]
        navigation.refresh_revision()
        controller.start({'request_id': 'confirm', **controller.envelope(), 'action': 'save_confirm',
                          'review_id': prepared['review_id'], 'review': prepared['review'],
                          'confirmed': True, 'composer': receipt}, blocked=False)
        await controller.task
        assert not events[-1]['success'] and not calls
        assert not (owner.session_manager._dir / 'saved-parent-drafts').exists()
    finally:
        await controller.aclose()
        await close(owner)


async def test_parent_receipt_published_copy_and_boolean_not_integer_confirmation(tmp_path):
    owner, calls = session(tmp_path)
    events = []
    async def send(row):
        events.append(row)
    controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    try:
        receipt = composer('PRIVATE')
        controller.start({'request_id': 'prepare', **controller.envelope(), 'action': 'save_prepare',
                          'text': 'PRIVATE', 'composer': receipt}, blocked=False)
        await controller.task
        prepared = events[-1]
        prepared['review']['composer']['empty'] = 0  # Not False: preserve exact JSON type.
        controller.start({'request_id': 'bad', **controller.envelope(), 'action': 'save_confirm',
                          'composer': receipt, 'review': prepared['review'],
                          'review_id': prepared['review_id'], 'confirmed': True}, blocked=False)
        await controller.task
        assert not events[-1]['success']
        assert controller.reviews[prepared['review_id']].body['composer']['empty'] is False
        assert not (owner.session_manager._dir / 'saved-parent-drafts').exists() and not calls
    finally:
        await controller.aclose()
        await close(owner)


async def test_parent_read_binary_manifest_chunks_exact_hash_and_no_image_capability_fallback(tmp_path):
    import base64

    from xdog.tui.attachments import attachment
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    from xdog.tui.child_drafts import ChildDraft
    owner, calls = session(tmp_path)
    owner.session_manager._dir.chmod(0o700)
    data = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
    image = attachment(data, 'private.png')
    store = ChildDraftStore(owner.session_manager._dir / 'saved-parent-drafts')
    saved = store.save(encode_draft(ChildDraft('TEXT', images=(image,)), label='image',
                                  scope=owner.session_id, action='parent', slot='rpc'))
    events = []
    async def send(row):
        events.append(row)
    controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
    controller.negotiate({'parent_saved_drafts': 1})
    async def control(rid, action, **fields):
        controller.start({'request_id': rid, **controller.envelope(), 'action': action, **fields}, blocked=False)
        await controller.task
        return events[-1]
    try:
        await control('catalog', 'catalog')
        fields = {'snapshot_id': saved.id, 'snapshot_sha256': saved.sha256, 'composer': composer('')}
        assert not (await control('no-binary', 'read', **fields))['success']
        assert not any(row['type'] == 'parent_draft_begin' for row in events)
        controller.negotiate({'parent_saved_images': 1})
        await control('new-catalog', 'catalog')
        read = await control('binary', 'read', **fields)
        assert read['success']
        frames = [row for row in events if row.get('request_id') == 'binary']
        chunks = [row for row in frames if row['type'] == 'parent_draft_chunk']
        decoded = b''.join(base64.b64decode(row['data'], validate=True) for row in chunks)
        assert decoded == data and frames[0]['review']['images'][0]['sha256'] == hashlib.sha256(data).hexdigest()
        assert frames[-2]['type'] == 'parent_draft_end'
        assert store.load(saved.id).sha256 == saved.sha256 and not calls
    finally:
        await controller.aclose()
        await close(owner)


async def test_parent_withdrawal_waits_owned_disk_worker_and_cannot_publish_preparation(tmp_path, monkeypatch):
    import asyncio
    import threading

    import xdog.coding.modes.rpc.parent_drafts as module
    owner, calls = session(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = module.encode_draft
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'encode_draft', held)
    events = []
    async def send(row):
        events.append(row)
    controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    try:
        controller.start({'request_id': 'held', **controller.envelope(), 'action': 'save_prepare',
                          'text': 'PRIVATE', 'composer': composer('PRIVATE')}, blocked=False)
        assert await asyncio.to_thread(entered.wait, 2)
        controller.negotiate({'parent_saved_drafts': 0})
        await asyncio.sleep(.01)
        assert controller.busy and not controller.reviews
        release.set()
        await controller.task
        assert not events[-1]['success'] and events[-1]['canceled'] and not controller.reviews
        assert not calls and not (owner.session_manager._dir / 'saved-parent-drafts').exists()
    finally:
        release.set()
        await controller.aclose()
        await close(owner)


async def test_actual_rpc_receiver_complete_binary_review_before_client_composer_cas(tmp_path):
    import asyncio
    import base64

    from xdog.tui.attachments import attachment
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    from xdog.tui.child_drafts import ChildDraft
    from xdog.tui.keys import KeyEvent
    from xdog.tui.parent_draft_receiver import ParentDraftReceiver, composer_receipt

    from packages.tui.tests.test_parent_draft_receiver import PNG, paint

    owner, calls = session(tmp_path)
    owner.session_manager._dir.chmod(0o700)
    store = ChildDraftStore(owner.session_manager._dir / 'saved-parent-drafts')
    saved = store.save(encode_draft(ChildDraft('PRIVATE CLIENT RECOVERY', images=(attachment(PNG, 'image.png'),)),
                                  label='original', scope=owner.session_id, action='parent', slot=''))
    events = []
    receiver = ParentDraftReceiver()
    async def send(row):
        events.append(row)
        if row['type'] in {'parent_draft_begin', 'parent_draft_chunk', 'parent_draft_end'}:
            receiver.feed(row)
    controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_images': 1})
    client = {'draft': ChildDraft(), 'revision': 9}
    adopted = []
    try:
        controller.start({'request_id': 'catalog', **controller.envelope(), 'action': 'catalog'}, blocked=False)
        await controller.task
        scope = {'request_id': 'read', **controller.envelope()}
        receipt = composer_receipt(client['draft'], client['revision'])
        receiver.expect(scope, receipt, saved.id, saved.sha256)
        controller.start({**scope, 'action': 'read', 'composer': receipt,
                          'snapshot_id': saved.id, 'snapshot_sha256': saved.sha256}, blocked=False)
        await controller.task
        recovery = await asyncio.to_thread(receiver.finish, 'read', events[-1])
        assert not adopted and not calls and not client['draft'].text
        def current():
            return composer_receipt(client['draft'], client['revision'])
        def adopt(draft, expected):
            assert current() == expected and current()['empty']
            client.update(draft=draft, revision=10)
            adopted.append(draft)
        panel = recovery.panel(lambda: scope, current, adopt, lambda: None)
        panel.focused = True
        panel.handle_input(KeyEvent('enter'))
        assert not adopted
        paint(panel)
        panel.handle_input(KeyEvent('enter'))
        assert client['draft'].text == 'PRIVATE CLIENT RECOVERY'
        assert client['draft'].images[0].data == PNG
        assert not calls and not owner.agent.messages and owner.permissions.mode == 'deny'
        assert store.load(saved.id).sha256 == saved.sha256
        assert base64.b64decode(next(row for row in events if row['type'] == 'parent_draft_chunk')['data']) == PNG
    finally:
        receiver.close()
        await controller.aclose()
        await close(owner)


async def test_parent_save_deadline_rechecked_after_queued_disk_work(tmp_path, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace

    import xdog.coding.modes.rpc.parent_drafts as module
    from xdog.tui.child_draft_store import ChildDraftStore
    owner, calls = session(tmp_path)
    owner.session_manager._dir.chmod(0o700)
    entered, release = threading.Event(), threading.Event()
    clock = [100.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    original = ChildDraftStore.save
    def held(self, data, *, alive=lambda: True):
        entered.set()
        assert release.wait(3)
        return original(self, data, alive=alive)
    monkeypatch.setattr(ChildDraftStore, 'save', held)
    events = []
    async def send(row):
        events.append(row)
    controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    try:
        receipt = composer('PRIVATE')
        controller.start({'request_id': 'prepare', **controller.envelope(), 'action': 'save_prepare',
                          'text': 'PRIVATE', 'composer': receipt}, blocked=False)
        await controller.task
        prepared = events[-1]
        controller.start({'request_id': 'confirm', **controller.envelope(), 'action': 'save_confirm',
                          'review_id': prepared['review_id'], 'review': prepared['review'],
                          'composer': receipt, 'confirmed': True}, blocked=False)
        assert await asyncio.to_thread(entered.wait, 2)
        clock[0] = 200.0
        release.set()
        await controller.task
        assert not events[-1]['success']
        root = owner.session_manager._dir / 'saved-parent-drafts'
        assert not list(root.glob('*.json')) and not list(root.glob('.draft-*'))
        assert not calls and not controller.reviews
    finally:
        release.set()
        await controller.aclose()
        await close(owner)


async def test_failed_save_completion_receipt_does_not_retry_or_rollback_committed_snapshot(tmp_path):
    owner, calls = session(tmp_path)
    owner.session_manager._dir.chmod(0o700)
    events = []
    async def send(row):
        if row.get('action') == 'save_confirm':
            raise OSError('Lost committed save receipt')
        events.append(row)
    controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    try:
        receipt = composer('PRIVATE')
        controller.start({'request_id': 'prepare', **controller.envelope(), 'action': 'save_prepare',
                          'text': 'PRIVATE', 'composer': receipt}, blocked=False)
        await controller.task
        prepared = events[-1]
        confirm = {**controller.envelope(), 'action': 'save_confirm', 'composer': receipt,
                   'review_id': prepared['review_id'], 'review': prepared['review'], 'confirmed': True}
        controller.start({'request_id': 'save', **confirm}, blocked=False)
        await controller.task
        root = owner.session_manager._dir / 'saved-parent-drafts'
        assert len(list(root.glob('*.json'))) == 1 and not controller.reviews
        controller.start({'request_id': 'retry', **confirm}, blocked=False)
        await controller.task
        assert len(list(root.glob('*.json'))) == 1 and not calls
        assert not any(row.get('action') == 'save_confirm' for row in events)
    finally:
        await controller.aclose()
        await close(owner)


async def test_failed_parent_prepare_delivery_retires_private_review_bytes(tmp_path):
    owner, calls = session(tmp_path)
    async def failed(row):
        raise OSError('Undelivered private review')
    controller = RpcParentDrafts(owner, RpcNavigation(owner, lambda _: None), failed)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    try:
        controller.start({'request_id': 'prepare', **controller.envelope(), 'action': 'save_prepare',
                          'text': 'PRIVATE', 'composer': composer('PRIVATE')}, blocked=False)
        await controller.task
        assert not controller.reviews
        assert not calls and not (owner.session_manager._dir / 'saved-parent-drafts').exists()
    finally:
        await controller.aclose()
        await close(owner)


async def test_parent_review_auto_expiry_releases_private_bytes_without_next_command(tmp_path):
    import asyncio
    from dataclasses import replace
    owner, calls = session(tmp_path)
    events = []
    async def send(row):
        events.append(row)
    controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
    controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
    try:
        controller.start({'request_id': 'prepare', **controller.envelope(), 'action': 'save_prepare',
                          'text': 'PRIVATE', 'composer': composer('PRIVATE')}, blocked=False)
        await controller.task
        key = events[-1]['review_id']
        timer = controller.review_timers[key]
        timer.cancel()
        controller.reviews[key] = replace(controller.reviews[key], deadline=0)
        controller.review_timers[key] = asyncio.get_running_loop().call_later(.01, controller._expire_review, key)
        await asyncio.sleep(.03)
        assert not controller.reviews and not controller.review_timers
        assert not calls and not (owner.session_manager._dir / 'saved-parent-drafts').exists()
    finally:
        await controller.aclose()
        await close(owner)
