from __future__ import annotations

import base64
import json
import os
import stat
from dataclasses import replace

import pytest
from xdog.tui.attachments import attachment
from xdog.tui.child_draft_store import ChildDraftStore, decode_draft, encode_draft
from xdog.tui.child_drafts import ChildDraft

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


def encoded(size=68):
    image = attachment(base64.b64decode(PNG) + b'\0' * (size - 68), 'owned image.png')
    original = ChildDraft('UNSENT /quit literal', 'inherit', {'tools': ['bash'], 'model': 'old/model'},
                          (image,), False, 4)
    return encode_draft(original, label='/workspace session old', scope='old', action='steer', slot='child'), original


@pytest.mark.skipif(os.name != 'posix', reason='POSIX private draft file primitives')
@pytest.mark.parametrize('size', [68, 10 * 1024 * 1024])
def test_private_snapshot_restart_and_reviewed_discard_preserve_bytes_without_old_permissions(tmp_path, size):
    data, original = encoded(size)
    store = ChildDraftStore(tmp_path / 'drafts')
    saved = store.save(data)
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / (saved.id + '.json')).stat().st_mode) == 0o600
    loaded = ChildDraftStore(store.root).load(saved.id)
    assert loaded.sha256 == saved.sha256 and loaded.draft.images[0].data == original.images[0].data
    assert loaded.draft.access == 'read-only' and loaded.draft.config == {'inherit_context': False}
    assert loaded.draft.uncertain and loaded.draft.text == original.text and loaded.source_action == 'steer'
    with pytest.raises(ValueError, match='changed since review'):
        store.discard(saved.id, '0' * 64)
    assert store.load(saved.id).sha256 == saved.sha256
    store.discard(saved.id, saved.sha256)
    assert not store.list() and not list(store.root.glob('.draft-*'))


def test_invalid_draft_snapshots_refuse_instead_of_partial_text_recovery() -> None:

    def _check_case(fault):
        data, _ = encoded()
        value = json.loads(data)
        if fault == "version":
            value["version"] = True
        elif fault == "receipt":
            value["receipt"]["content"] = "other"
        elif fault == "base64":
            value["images"][0]["data"] = "wrong!"
        elif fault == "mime":
            value["images"][0]["mime_type"] = "image/jpeg"
        elif fault == "unknown":
            value["automatic_submit"] = True
        elif fault == "cursor":
            value["cursor"] = True
        else:
            with pytest.raises(ValueError, match="Duplicate"):
                decode_draft("a" * 32, data[:-1] + b',"text":"duplicate"}')
            return
        with pytest.raises(ValueError):
            decode_draft("a" * 32, json.dumps(value).encode())

    for fault in ["version", "receipt", "base64", "mime", "unknown", "duplicate", "cursor"]:
        _check_case(fault)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX file ownership/type guards')
@pytest.mark.parametrize('fault', ['root-link', 'file-link', 'hardlink', 'mode'])
def test_unsafe_storage_refuses_without_following_or_changing_existing_paths(tmp_path, fault):
    data, _ = encoded()
    store = ChildDraftStore(tmp_path / 'drafts')
    saved = store.save(data)
    path = store.root / (saved.id + '.json')
    if fault == 'root-link':
        linked = tmp_path / 'linked'
        linked.symlink_to(store.root, target_is_directory=True)
        store = ChildDraftStore(linked)
    elif fault == 'file-link':
        path.rename(tmp_path / 'outside')
        path.symlink_to(tmp_path / 'outside')
    elif fault == 'hardlink':
        os.link(path, tmp_path / 'another')
    else:
        path.chmod(0o644)
    with pytest.raises((ValueError, OSError)):
        store.load(saved.id)


@pytest.mark.skipif(os.name != 'posix', reason='Owned POSIX restart/storage evidence')
def test_actual_process_snapshot_survives_exit_and_load_never_recovers_permissions(tmp_path):
    import subprocess
    import sys
    data, original = encoded()
    source = tmp_path / 'source.json'
    source.write_bytes(data)
    script = """import json,sys
from pathlib import Path
from xdog.tui.child_draft_store import ChildDraftStore
store=ChildDraftStore(Path(sys.argv[1]))
saved=store.save(Path(sys.argv[2]).read_bytes())
print(json.dumps({'id':saved.id,'sha256':saved.sha256}),flush=True)
"""
    result = subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path / 'drafts'), str(source)],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    restored = ChildDraftStore(tmp_path / 'drafts').load(receipt['id'])
    assert restored.sha256 == receipt['sha256'] and restored.draft.text == original.text
    assert restored.draft.images[0].data == original.images[0].data
    assert restored.draft.access == 'read-only' and restored.draft.config == {'inherit_context': False}


@pytest.mark.skipif(os.name != 'posix', reason='POSIX cooperating-writer lock')
def test_concurrent_saves_cannot_bypass_catalog_capacity(tmp_path, monkeypatch):
    import concurrent.futures

    import xdog.tui.child_draft_store as module
    monkeypatch.setattr(module, 'MAX_DRAFTS', 2)
    data, _ = encoded()
    root = tmp_path / 'drafts'
    # Precreate to isolate catalog admission from directory creation races.
    root.mkdir(mode=0o700)
    def save():
        try:
            return ChildDraftStore(root).save(data).id
        except ValueError:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: save(), range(8)))
    assert sum(value is not None for value in results) == 2
    assert len(ChildDraftStore(root).list()) == 2
    assert not list(root.glob('.draft-*'))


@pytest.mark.skipif(os.name != 'posix', reason='POSIX exact discard receipt')
def test_replaced_file_cannot_be_discarded_using_old_review_receipt(tmp_path):
    data, original = encoded()
    store = ChildDraftStore(tmp_path / 'drafts')
    saved = store.save(data)
    changed = encode_draft(replace(original, text='CHANGED SOURCE'),
                           label='new', scope='new', action='start', slot='')
    path = store.root / (saved.id + '.json')
    path.write_bytes(changed)
    with pytest.raises(ValueError, match='changed since review'):
        store.discard(saved.id, saved.sha256)
    assert path.read_bytes() == changed


def test_missing_root_lists_empty_but_broken_link_is_not_silently_empty(tmp_path):
    store = ChildDraftStore(tmp_path / 'absent')
    assert store.list() == ()
    store.root.symlink_to(tmp_path / 'missing-target', target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic link'):
        store.list()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX private saved catalog')
def test_metadata_catalog_remains_available_when_combined_image_load_exceeds_capacity(tmp_path, monkeypatch):
    import xdog.tui.child_draft_store as module
    data, original = encoded()
    store = ChildDraftStore(tmp_path / 'drafts')
    saved = [store.save(data), store.save(data)]
    monkeypatch.setattr(module, 'MAX_RETAINED_BYTES', len(original.images[0].data))
    with pytest.raises(ValueError, match='local load capacity'):
        store.list()
    catalog = store.catalog()
    assert len(catalog) == 2 and {row['id'] for row in catalog} == {item.id for item in saved}
    assert all(row['image_count'] == 1 and row['image_bytes'] == len(original.images[0].data) for row in catalog)
    assert all('images' not in row and 'draft' not in row for row in catalog)
    assert store.load(saved[0].id).draft.images[0].data == original.images[0].data


@pytest.mark.skipif(os.name != 'posix', reason='Owned separate-process POSIX lock contention')
@pytest.mark.parametrize('action', ['save', 'load', 'catalog', 'discard'])
def test_external_lock_has_bounded_wait_and_preserves_existing_snapshot(tmp_path, monkeypatch, action):
    import select
    import subprocess
    import sys
    import time

    import xdog.tui.child_draft_store as module
    data, _ = encoded()
    store = ChildDraftStore(tmp_path / 'drafts')
    saved = store.save(data)
    before = (store.root / (saved.id + '.json')).read_bytes()
    script = """import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY)
fcntl.flock(fd,fcntl.LOCK_EX)
print('locked',flush=True)
sys.stdin.readline()
os.close(fd)
"""
    peer = subprocess.Popen([sys.executable, '-I', '-c', script, str(store.root)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([peer.stdout], [], [], 3)
        assert ready and peer.stdout.readline().strip() == 'locked'
        monkeypatch.setattr(module, 'LOCK_TIMEOUT', 0.1)
        start = time.monotonic()
        with pytest.raises(ValueError, match='lock deadline exceeded'):
            if action == 'save':
                store.save(data)
            elif action == 'load':
                store.load(saved.id)
            elif action == 'catalog':
                store.catalog()
            else:
                store.discard(saved.id, saved.sha256)
        assert time.monotonic() - start < 1
        assert (store.root / (saved.id + '.json')).read_bytes() == before
        assert not list(store.root.glob('.draft-*'))
        peer.stdin.write('release\n')
        peer.stdin.flush()
        assert peer.wait(timeout=3) == 0
        assert store.load(saved.id).sha256 == saved.sha256
    finally:
        if peer.poll() is None:
            peer.kill()
            peer.wait(timeout=3)
        for stream in (peer.stdin, peer.stdout, peer.stderr):
            stream.close()


@pytest.mark.skipif(os.name != 'posix', reason='Current POSIX private first-run application directory')
@pytest.mark.parametrize('fault', ['none', 'intermediate-link', 'unsafe-parent', 'external-hardlink'])
def test_explicit_application_parent_first_run_preserves_ownership_and_no_link_fallback(tmp_path, fault):
    data, _ = encoded()
    root = tmp_path / 'xdg' / 'xdog' / 'claw' / 'drafts'
    if fault == 'intermediate-link':
        outside = tmp_path / 'outside'
        outside.mkdir(mode=0o700)
        (tmp_path / 'xdg').symlink_to(outside, target_is_directory=True)
    elif fault == 'unsafe-parent':
        root.parent.mkdir(parents=True)
        root.parent.chmod(0o755)
    store = ChildDraftStore(root, create_parent=True)
    if fault in {'intermediate-link', 'unsafe-parent'}:
        with pytest.raises((ValueError, OSError)):
            store.save(data)
        assert not root.exists()
        if fault == 'unsafe-parent':
            assert stat.S_IMODE(root.parent.stat().st_mode) == 0o755  # Never chmod existing directory.
        return
    saved = store.save(data)
    assert stat.S_IMODE(root.parent.stat().st_mode) == 0o700
    path = root / (saved.id + '.json')
    if fault == 'external-hardlink':
        os.link(path, tmp_path / 'external')
        with pytest.raises(ValueError, match='hardlink'):
            store.load(saved.id)
        assert (tmp_path / 'external').exists()  # Never delete unknown hardlinks.
    else:
        assert store.load(saved.id).sha256 == saved.sha256
