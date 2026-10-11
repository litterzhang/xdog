"""Original remote controller receipts fence disk replacement; no stale recovery/delete."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from xdog.claw.core.runtime.parent_draft_protocol import ParentDraftConnection
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.parent_drafts import RpcParentDrafts
from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.parent_draft_receiver import composer_receipt

from packages.claw.tests.test_mcp_sampling_gateway import setup
from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('window', ['catalog-read', 'review-discard'])
async def test_remote_parent_changed_disk_hash_refuses_stale_receipt_and_keeps_replacement(tmp_path, kind, window):
    events = []
    async def send(row):
        events.append(row)
    server = None
    if kind == 'coding':
        owner, calls = session(tmp_path)
        owner.session_manager._dir.chmod(0o700)
        root = owner.session_manager._dir / 'saved-parent-drafts'
        controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
        controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_discard': 1})
        identity = owner.session_id
        def scope():
            return controller.envelope()
        async def control(rid, action, **fields):
            controller.start({'request_id': rid, 'action': action, **scope(), **fields}, blocked=False)
            await controller.task
            return events[-1]
    else:
        server, config, runtime, owner, adapter, _ = await setup(tmp_path)
        Path(config.data_dir).chmod(0o700)
        root = Path(config.data_dir) / 'saved-parent-drafts'
        controller = ParentDraftConnection(server._orchestrator, root, send)
        controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_discard': 1})
        identity = owner.meta.session_id
        calls = adapter.calls
        def scope():
            return {'group_id': 'main', 'session_id': identity, 'view': 'a' * 32,
                    'parent_draft_owner': controller.owner, 'parent_draft_generation': controller.generation}
        async def control(rid, action, **fields):
            await controller.request({'request_id': rid, 'action': action, **scope(), **fields})
            await asyncio.gather(*tuple(controller.jobs.values()))
            await asyncio.sleep(0)
            return events[-1]
    store = ChildDraftStore(root)
    initial = encode_draft(ChildDraft('ORIGINAL PRIVATE'), label='original', scope=identity, action='parent', slot='')
    saved = store.save(initial)
    receipt = composer_receipt(ChildDraft(), 1)
    try:
        catalog = await control('catalog', 'catalog')
        assert catalog['success'] and catalog['catalog'][0]['sha256'] == saved.sha256
        fields = {'snapshot_id': saved.id, 'snapshot_sha256': saved.sha256, 'composer': receipt}
        prepared = None
        if window == 'review-discard':
            prepared = await control('prepare', 'discard_prepare', **fields)
            assert prepared['success'] and prepared['review']['content'] == 'ORIGINAL PRIVATE'
        replacement = encode_draft(ChildDraft('REPLACEMENT PRIVATE'), label='replacement', scope=identity,
                                   action='parent', slot='')
        updated = store.update(saved.id, saved.sha256, replacement)
        if window == 'catalog-read':
            refused = await control('stale-read', 'read', **fields)
            assert not any(row['type'].startswith('parent_draft_') and row['type'] != 'parent_draft_result'
                           for row in events)
        else:
            refused = await control('stale-delete', 'discard_confirm', composer=receipt,
                                    review_id=prepared['review_id'], review=prepared['review'], confirmed=True)
            # Consumed confirmation may not be replayed after uncertain/failing mutation.
            replay = await control('replay', 'discard_confirm', composer=receipt,
                                   review_id=prepared['review_id'], review=prepared['review'], confirmed=True)
            assert not replay['success'] and not controller.reviews and not controller.review_timers
        assert not refused['success']
        assert 'PRIVATE' not in str(refused)
        assert store.load(saved.id).sha256 == updated.sha256
        assert store.load(saved.id).draft.text == 'REPLACEMENT PRIVATE'
        assert len(store.catalog()) == 1 and not calls and not owner.agent.messages
        if server is not None:
            assert server._orchestrator.try_reserve_group('main')
            server._orchestrator.release_group_reservation('main')
    finally:
        if kind == 'coding':
            await controller.aclose()
            await close(owner)
        else:
            await controller.close()
            await server.stop()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('fault', ['begin', 'chunk', 'end', 'disk-save'])
async def test_parent_transport_and_storage_failures_have_no_success_or_automatic_replay(tmp_path, kind, fault,
                                                                                       monkeypatch):
    from xdog.tui.attachments import attachment
    from xdog.tui.parent_draft_receiver import ParentDraftReceiver

    from packages.tui.tests.test_parent_draft_receiver import PNG

    events, receiver = [], ParentDraftReceiver()
    async def send(row):
        if row['type'] == 'parent_draft_' + fault:
            raise OSError('PRIVATE TRANSPORT DIAGNOSTIC')
        events.append(row)
        if row['type'] in {'parent_draft_begin', 'parent_draft_chunk', 'parent_draft_end'}:
            receiver.feed(row)
    server = None
    if kind == 'coding':
        owner, calls = session(tmp_path)
        owner.session_manager._dir.chmod(0o700)
        root = owner.session_manager._dir / 'saved-parent-drafts'
        controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
        controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_images': 1, 'parent_saved_save': 1})
        identity = owner.session_id
        def scope():
            return controller.envelope()
        async def control(rid, action, **fields):
            controller.start({'request_id': rid, 'action': action, **scope(), **fields}, blocked=False)
            await controller.task
            return events[-1]
    else:
        server, config, runtime, owner, adapter, _ = await setup(tmp_path)
        Path(config.data_dir).chmod(0o700)
        root = Path(config.data_dir) / 'saved-parent-drafts'
        controller = ParentDraftConnection(server._orchestrator, root, send)
        controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_images': 1, 'parent_saved_save': 1})
        identity = owner.meta.session_id
        calls = adapter.calls
        def scope():
            return {'group_id': 'main', 'session_id': identity, 'view': 'a' * 32,
                    'parent_draft_owner': controller.owner, 'parent_draft_generation': controller.generation}
        async def control(rid, action, **fields):
            await controller.request({'request_id': rid, 'action': action, **scope(), **fields})
            await asyncio.gather(*tuple(controller.jobs.values()))
            await asyncio.sleep(0)
            return events[-1]
    store = ChildDraftStore(root)
    try:
        if fault == 'disk-save':
            def fail_save(*args, **kwargs):
                raise OSError('PRIVATE DISK DIAGNOSTIC')
            monkeypatch.setattr(ChildDraftStore, 'save', fail_save)
            composer = composer_receipt(ChildDraft('PRIVATE'), 1)
            prepared = await control('prepare', 'save_prepare', text='PRIVATE', composer=composer)
            assert prepared['success']
            fields = {'review_id': prepared['review_id'], 'review': prepared['review'],
                      'composer': composer, 'confirmed': True}
            result = await control('confirm', 'save_confirm', **fields)
            replay = await control('retry', 'save_confirm', **fields)
            assert not replay['success'] and not controller.reviews and not controller.review_timers
            assert not store.catalog()
        else:
            draft = ChildDraft('PRIVATE', images=(attachment(PNG, 'image.png'),))
            saved = store.save(encode_draft(draft, label='transport', scope=identity, action='parent', slot=''))
            catalog = await control('catalog', 'catalog')
            assert catalog['success']
            expected = {'request_id': 'read', **scope()}
            if kind == 'claw':
                expected['session_revision'] = catalog['session_revision']
            composer = composer_receipt(ChildDraft(), 1)
            receiver.expect(expected, composer, saved.id, saved.sha256)
            result = await control('read', 'read', composer=composer,
                                   snapshot_id=saved.id, snapshot_sha256=saved.sha256)
            with pytest.raises(ValueError):
                receiver.finish('read', result)
            assert not receiver.pending
            assert store.load(saved.id).sha256 == saved.sha256 and len(store.catalog()) == 1
        assert not result['success'] and 'PRIVATE' not in str(result)
        assert not calls and not owner.agent.messages
        if server is not None:
            assert server._orchestrator.try_reserve_group('main')
            server._orchestrator.release_group_reservation('main')
    finally:
        receiver.close()
        if kind == 'coding':
            await controller.aclose()
            await close(owner)
        else:
            await controller.close()
            await server.stop()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('window', ['before-first-step', 'held-save'])
async def test_parent_cancel_disconnect_settles_original_disk_work_and_preserves_permissions(tmp_path, kind, window,
                                                                                          monkeypatch):
    import threading

    entered, release = threading.Event(), threading.Event()
    events = []
    async def send(row):
        events.append(row)
    server = None
    if kind == 'coding':
        owner, calls = session(tmp_path)
        owner.session_manager._dir.chmod(0o700)
        root = owner.session_manager._dir / 'saved-parent-drafts'
        controller = RpcParentDrafts(owner, RpcNavigation(owner, events.append), send)
        controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
        def scope():
            return controller.envelope()
        async def start(rid, action, **fields):
            controller.start({'request_id': rid, 'action': action, **scope(), **fields}, blocked=False)
            return controller.task
        async def shutdown():
            await controller.aclose()
    else:
        server, config, runtime, owner, adapter, _ = await setup(tmp_path)
        Path(config.data_dir).chmod(0o700)
        root = Path(config.data_dir) / 'saved-parent-drafts'
        controller = ParentDraftConnection(server._orchestrator, root, send)
        controller.negotiate({'parent_saved_drafts': 1, 'parent_saved_save': 1})
        calls = adapter.calls
        def scope():
            return {'group_id': 'main', 'session_id': owner.meta.session_id, 'view': 'a' * 32,
                    'parent_draft_owner': controller.owner, 'parent_draft_generation': controller.generation}
        async def start(rid, action, **fields):
            await controller.request({'request_id': rid, 'action': action, **scope(), **fields})
            return controller.jobs[rid]
        async def shutdown():
            await controller.close()
    original = ChildDraftStore.save
    def held(self, data, *, alive=lambda: True):
        entered.set()
        assert release.wait(3)
        return original(self, data, alive=alive)
    closing = None
    try:
        if window == 'before-first-step':
            await start('catalog', 'catalog')
            await shutdown()  # No yield before close's synchronous cancellation signal.
        else:
            receipt = composer_receipt(ChildDraft('PRIVATE'), 1)
            work = await start('prepare', 'save_prepare', text='PRIVATE', composer=receipt)
            await work
            prepared = events[-1]
            assert prepared['success']
            monkeypatch.setattr(ChildDraftStore, 'save', held)
            await start('confirm', 'save_confirm', composer=receipt, review_id=prepared['review_id'],
                        review=prepared['review'], confirmed=True)
            assert await asyncio.to_thread(entered.wait, 2)
            closing = asyncio.create_task(shutdown())
            await asyncio.sleep(.01)
            closing.cancel()
            await asyncio.sleep(.01)
            assert not closing.done()
            if server is not None:
                assert not server._orchestrator.try_reserve_group('main')
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await closing
        assert not list(root.glob('*.json')) and not list(root.glob('.draft-*'))
        assert not controller.reviews and not controller.review_timers
        assert not calls and not owner.agent.messages
        if kind == 'coding':
            assert owner.permissions.mode == 'deny'
        else:
            assert server._orchestrator.try_reserve_group('main')
            server._orchestrator.release_group_reservation('main')
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await shutdown()
        if kind == 'coding':
            await close(owner)
        else:
            await server.stop()
