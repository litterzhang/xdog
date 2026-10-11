"""Original transfer/receipt/fully-painted one-use empty composer admission."""
from __future__ import annotations

import base64
import hashlib

import pytest
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.parent_draft_receiver import ParentDraftReceiver, composer_receipt

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')


def setup(images=True):
    receiver = ParentDraftReceiver()
    scope = {'request_id': 'read', 'session_id': 'session', 'session_revision': 'revision',
             'parent_draft_owner': 'owner', 'parent_draft_generation': 1}
    composer = composer_receipt(ChildDraft(), 3)
    receiver.expect(scope, composer, 'a' * 32, 'b' * 64)
    body = {'operation': 'recover', 'composer': composer, 'snapshot_id': 'a' * 32,
            'snapshot_sha256': 'b' * 64, 'label': 'PRIVATE saved draft', 'content': 'PRIVATE RESTORED TEXT',
            'images': [{'name': 'private.png', 'mime_type': 'image/png', 'bytes': len(PNG),
                        'sha256': hashlib.sha256(PNG).hexdigest()}] if images else [],
            'notice': 'Editing data only; never submit'}
    frames = [{'type': 'parent_draft_begin', **scope, 'review': body}]
    if images:
        frames.append({'type': 'parent_draft_chunk', **scope, 'image_index': 0, 'index': 0,
                       'data': base64.b64encode(PNG).decode()})
    frames.append({'type': 'parent_draft_end', **scope})
    result = {'type': 'parent_draft_result', **scope, 'action': 'read', 'success': True, 'recovery': body}
    return receiver, scope, composer, frames, result


def paint(panel):
    for _ in range(200):
        panel.render(80)
        if panel._visible:
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Complete parent recovery did not paint')


@pytest.mark.parametrize('images', [False, True])
def test_exact_receive_full_review_and_atomic_adoption_once_even_with_second_panel(images):
    receiver, scope, composer, frames, result = setup(images)
    for frame in frames:
        receiver.feed(frame)
    recovery = receiver.finish('read', result)
    assert not receiver.pending and recovery.draft.text == 'PRIVATE RESTORED TEXT'
    if images:
        assert recovery.draft.images[0].data == PNG
    state = {'draft': ChildDraft(), 'revision': 3}
    adopted = []
    def current():
        return composer_receipt(state['draft'], state['revision'])
    def adopt(draft, expected):
        assert current() == expected and current()['empty']
        state.update(draft=draft, revision=4)
        adopted.append(draft)
    panel = recovery.panel(lambda: scope, current, adopt, lambda: None)
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not adopted
    paint(panel)
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.RELEASE))
    assert not adopted
    panel.handle_input(KeyEvent('enter'))
    assert adopted == [recovery.draft]
    state.update(draft=ChildDraft(), revision=3)
    second = recovery.panel(lambda: scope, current, adopt, lambda: None)
    second.focused = True
    paint(second)
    with pytest.raises(ValueError, match='consumed'):
        second.handle_input(KeyEvent('enter'))
    assert len(adopted) == 1


@pytest.mark.parametrize('fault', ['generation-bool', 'foreign', 'chunk-before-begin', 'duplicate-begin',
                                   'duplicate-chunk', 'missing-chunk', 'hash', 'failed-result', 'missing-end'])
def test_invalid_transfer_never_produces_adoptable_recovery(fault):
    receiver, scope, composer, frames, result = setup()
    try:
        if fault == 'generation-bool':
            frames[0]['parent_draft_generation'] = True
        elif fault == 'foreign':
            frames[0]['parent_draft_owner'] = 'foreign'
        elif fault == 'chunk-before-begin':
            frames[0], frames[1] = frames[1], frames[0]
        elif fault == 'duplicate-begin':
            frames.insert(1, frames[0])
        elif fault == 'duplicate-chunk':
            frames.insert(2, frames[1])
        elif fault == 'missing-chunk':
            frames.pop(1)
        elif fault == 'hash':
            frames[0]['review']['images'][0]['sha256'] = 'c' * 64
        elif fault == 'failed-result':
            result['success'] = False
        elif fault == 'missing-end':
            frames.pop()
        with pytest.raises(ValueError):
            for frame in frames:
                receiver.feed(frame)
            receiver.finish('read', result)
        assert not receiver.pending
    finally:
        receiver.close()


@pytest.mark.parametrize('change', ['owner', 'revision', 'nonempty'])
def test_composer_or_owner_changes_after_painting_refuse_adoption(change):
    receiver, scope, composer, frames, result = setup(False)
    for frame in frames:
        receiver.feed(frame)
    recovery = receiver.finish('read', result)
    adopted = []
    current_scope, current_composer = dict(scope), dict(composer)
    panel = recovery.panel(lambda: current_scope, lambda: current_composer,
                           lambda *args: adopted.append(args), lambda: None)
    panel.focused = True
    paint(panel)
    if change == 'owner':
        current_scope['parent_draft_owner'] = 'changed'
    elif change == 'revision':
        current_composer['revision'] += 1
    else:
        current_composer.update(composer_receipt(ChildDraft('UNSENT'), 4))
    with pytest.raises(ValueError, match='changed'):
        panel.handle_input(KeyEvent('enter'))
    assert not adopted


def test_close_releases_private_spool_and_late_or_reused_frames_never_reopen():
    receiver, scope, composer, frames, result = setup()
    receiver.feed(frames[0])
    streams = list(receiver.pending['read'].files)
    receiver.close()
    assert not receiver.pending and all(stream.closed for stream in streams)
    for frame in frames:
        receiver.feed(frame)
    assert not receiver.pending
    with pytest.raises(ValueError):
        receiver.expect(scope, composer, 'a' * 32, 'b' * 64)


def test_resize_invalidates_painted_recovery_until_whole_new_layout_reviewed():
    receiver, scope, composer, frames, result = setup(False)
    for frame in frames:
        receiver.feed(frame)
    recovery = receiver.finish('read', result)
    adopted = []
    panel = recovery.panel(lambda: scope, lambda: composer, lambda *args: adopted.append(args), lambda: None)
    panel.focused = True
    paint(panel)
    panel.render(30)
    panel.handle_input(KeyEvent('enter'))
    assert not adopted
    paint(panel)
    panel.focused = False
    panel.handle_input(KeyEvent('enter'))
    assert not adopted
    panel.focused = True
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    assert len(adopted) == 1


def test_expiry_releases_all_spools_and_late_end_cannot_produce_receipt():
    receiver, scope, composer, frames, result = setup()
    receiver.feed(frames[0])
    transfer = receiver.pending['read']
    files = list(transfer.files)
    transfer.deadline = 0
    receiver.poll()
    assert not receiver.pending and all(stream.closed for stream in files)
    receiver.feed(frames[-1])
    with pytest.raises(ValueError):
        receiver.finish('read', result)


def test_cancelled_or_expired_review_cannot_be_recreated_to_adopt():
    receiver, scope, composer, frames, result = setup(False)
    for frame in frames:
        receiver.feed(frame)
    recovery = receiver.finish('read', result)
    adopted, canceled = [], []
    panel = recovery.panel(lambda: scope, lambda: composer, lambda *args: adopted.append(args),
                           lambda: canceled.append(True))
    panel.focused = True
    panel.handle_input(KeyEvent('escape'))
    assert canceled == [True]
    recreated = recovery.panel(lambda: scope, lambda: composer, lambda *args: adopted.append(args), lambda: None)
    recreated.focused = True
    paint(recreated)
    with pytest.raises(ValueError, match='consumed'):
        recreated.handle_input(KeyEvent('enter'))
    object.__setattr__(recovery, 'deadline', 0)
    expired = recovery.panel(lambda: scope, lambda: composer, lambda *args: adopted.append(args), lambda: None)
    expired.focused = True
    paint(expired)
    with pytest.raises(ValueError, match='expired'):
        expired.handle_input(KeyEvent('enter'))
    assert not adopted


def test_concurrent_parent_manifests_share_one_receiver_retention_budget(monkeypatch):
    import xdog.tui.parent_draft_receiver as module
    monkeypatch.setattr(module, 'MAX_RETAINED_BYTES', len(PNG))
    receiver, scope, composer, frames, result = setup()
    receiver.feed(frames[0])
    other = {**scope, 'request_id': 'second'}
    receiver.expect(other, composer, 'a' * 32, 'b' * 64)
    with pytest.raises(ValueError, match='invalid'):
        receiver.feed({**frames[0], **other})
    assert 'read' in receiver.pending and 'second' not in receiver.pending
    receiver.close()


def test_current_host_capacity_changes_refuse_before_decode_and_release_files():
    receiver, scope, composer, frames, result = setup()
    remaining = [len(PNG)]
    receiver.remaining_bytes = lambda: remaining[0]
    for frame in frames:
        receiver.feed(frame)
    streams = list(receiver.pending['read'].files)
    remaining[0] = 0
    with pytest.raises(ValueError, match='capacity'):
        receiver.finish('read', result)
    assert not receiver.pending and all(stream.closed for stream in streams)


def test_decode_keeps_original_capacity_until_worker_settles_and_close_revokes_delivery(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import xdog.tui.parent_draft_receiver as module
    receiver, scope, composer, frames, result = setup()
    monkeypatch.setattr(module, 'MAX_RETAINED_BYTES', len(PNG))
    for frame in frames:
        receiver.feed(frame)
    entered, release = threading.Event(), threading.Event()
    original = module.attachment
    def held(data, name):
        entered.set()
        assert release.wait(3)
        return original(data, name)
    monkeypatch.setattr(module, 'attachment', held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(receiver.finish, 'read', result)
        try:
            assert entered.wait(2)
            other = {**scope, 'request_id': 'second'}
            receiver.expect(other, composer, 'a' * 32, 'b' * 64)
            with pytest.raises(ValueError):
                receiver.feed({**frames[0], **other})
            receiver.close()
            assert not work.done()
            release.set()
            with pytest.raises(ValueError, match='revoked'):
                work.result(timeout=2)
        finally:
            release.set()
            receiver.close()
