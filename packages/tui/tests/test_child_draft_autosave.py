"""Debounced opt-in child snapshots; exact previous owner, no automatic submission."""
from __future__ import annotations

import time
from dataclasses import replace

from xdog.tui.child_draft_autosave import ChildDraftAutosave
from xdog.tui.child_draft_store import ChildDraftStore
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.child_draft_panel import DraftEntry


def entry(text):
    draft = ChildDraft(text)
    return DraftEntry('local-scope', ('start', ''), draft, draft, 'source workspace', True)


def settle(owner, store, rows, notices):
    deadline = time.monotonic() + 3
    while owner.busy and time.monotonic() < deadline:
        owner.poll(store, rows, notices.append)
        time.sleep(0.001)
    assert not owner.busy


def test_disabled_default_debounce_and_same_record_update(tmp_path, monkeypatch):
    import xdog.tui.child_draft_autosave as module
    owner = ChildDraftAutosave()
    store = ChildDraftStore(tmp_path / 'drafts')
    rows, notices = (entry('FIRST'),), []
    owner.poll(store, rows, notices.append)
    assert not store.list() and not owner.busy
    owner.set_enabled(True)
    # Force a long debounce without replacing process-global monotonic clocks.
    monkeypatch.setattr(module, 'DEBOUNCE', 60)
    owner.poll(store, rows, notices.append)
    assert not owner.busy
    rows = (entry('LATEST UNSENT'),)
    owner.poll(store, rows, notices.append)
    assert not owner.busy
    owner.poll(store, rows, notices.append, flush=True)
    settle(owner, store, rows, notices)
    saved = store.list()
    assert len(saved) == 1 and saved[0].draft.text == 'LATEST UNSENT'
    rows = (entry('EDITED'),)
    owner.poll(store, rows, notices.append, flush=True)
    settle(owner, store, rows, notices)
    assert len(store.list()) == 1 and store.load(saved[0].id).draft.text == 'EDITED'
    owner.set_enabled(False)
    owner.poll(store, (entry('AFTER OFF'),), notices.append, flush=True)
    assert store.load(saved[0].id).draft.text == 'EDITED' and not notices


def test_loaded_saved_entries_are_not_automatically_resaved(tmp_path):
    owner = ChildDraftAutosave()
    owner.set_enabled(True)
    store = ChildDraftStore(tmp_path / 'drafts')
    row = replace(entry('SAVED OLD TASK'), scope='saved-draft:' + 'a' * 32, current=False)
    owner.poll(store, (row,), lambda _: None, flush=True)
    assert not owner.busy and not store.list()


def test_completed_autosave_survives_owned_process_kill_without_automatic_recovery(tmp_path):
    import json
    import os
    import select
    import subprocess
    import sys

    import pytest
    if os.name != 'posix':
        pytest.skip('Owned POSIX SIGKILL evidence')
    script = """import sys,time,json
from pathlib import Path
from xdog.tui.child_draft_autosave import ChildDraftAutosave
from xdog.tui.child_draft_store import ChildDraftStore
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.child_draft_panel import DraftEntry
store=ChildDraftStore(Path(sys.argv[1]))
owner=ChildDraftAutosave()
owner.set_enabled(True)
draft=ChildDraft('UNSENT AUTOSAVE AFTER SIGKILL',access='inherit',config={'tools':['bash']})
row=DraftEntry('old-session',('steer','child'),draft,draft,'old workspace',True)
owner.poll(store,(row,),lambda message:None,flush=True)
while owner.busy:
    owner.poll(store,(row,),lambda message:None)
    time.sleep(.001)
print(json.dumps({'saved':store.catalog()[0]['id']}),flush=True)
time.sleep(60)
"""
    process = subprocess.Popen([sys.executable, '-I', '-c', script, str(tmp_path / 'autosaves')],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([process.stdout], [], [], 5)
        assert ready, 'Completed owned autosave receipt not observed'
        saved = json.loads(process.stdout.readline())['saved']
        process.kill()
        assert process.wait(timeout=3) == -9
        loaded = ChildDraftStore(tmp_path / 'autosaves').load(saved)
        assert loaded.draft.text == 'UNSENT AUTOSAVE AFTER SIGKILL'
        assert loaded.draft.access == 'read-only' and loaded.draft.config == {'inherit_context': False}
        assert loaded.draft.uncertain
        assert not ChildDraftAutosave().enabled  # Restart never silently opts in or adopts execution policy.
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()


def test_autosave_retains_only_update_receipt_and_prunes_removed_slots_without_disk_delete(tmp_path):
    import base64

    from xdog.tui.attachments import attachment
    image = attachment(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), 'owned.png')
    owner = ChildDraftAutosave()
    owner.set_enabled(True)
    store = ChildDraftStore(tmp_path / 'drafts')
    draft = ChildDraft('UNSENT IMAGE', images=(image,))
    row = DraftEntry('context', ('start', 'slot'), draft, draft, 'source', True)
    owner.poll(store, (row,), lambda _: None, flush=True)
    settle(owner, store, (row,), [])
    receipt = owner._written[row.id][1]
    assert not receipt.draft.images and not receipt.draft.text
    assert owner._active is None
    assert store.load(receipt.id).draft.images[0].data == image.data
    edited = replace(row, value=replace(draft, text='EDITED IMAGE'))
    owner.poll(store, (edited,), lambda _: None, flush=True)
    settle(owner, store, (edited,), [])
    assert owner._written[row.id][1].id == receipt.id
    assert store.load(receipt.id).draft.text == 'EDITED IMAGE'
    owner.poll(store, (), lambda _: None)
    assert not owner._written and not owner._observed and owner._active is None
    assert store.load(receipt.id).draft.images[0].data == image.data
    owner.poll(store, (row,), lambda _: None, flush=True)
    settle(owner, store, (row,), [])
    assert owner._written[row.id][1].id != receipt.id
    assert len(store.catalog()) == 2


def test_durability_pending_writing_stale_receipt_and_latest_commit(tmp_path, monkeypatch):
    import threading

    import xdog.tui.child_draft_autosave as module
    monkeypatch.setattr(module, 'DEBOUNCE', 60)
    owner, store = ChildDraftAutosave(), ChildDraftStore(tmp_path / 'drafts')
    entered, release = threading.Event(), threading.Event()
    original = store.save
    def held(data):
        entered.set()
        assert release.wait(3)
        return original(data)
    monkeypatch.setattr(store, 'save', held)
    rows = (entry('FIRST'),)
    assert owner.durability == 'off'
    owner.set_enabled(True)
    assert owner.durability == 'empty'
    owner.poll(store, rows, lambda _: None)
    assert owner.durability == 'pending'
    owner.poll(store, rows, lambda _: None, flush=True)
    assert entered.wait(2) and owner.durability == 'writing'
    rows = (entry('LATEST DURING WRITE'),)
    owner.poll(store, rows, lambda _: None)
    release.set()
    settle(owner, store, rows, [])
    assert owner.durability == 'pending'  # Older receipt is not latest edit durability.
    assert store.list()[0].draft.text == 'FIRST'
    owner.poll(store, rows, lambda _: None, flush=True)
    settle(owner, store, rows, [])
    assert owner.durability == 'committed'
    assert store.list()[0].draft.text == 'LATEST DURING WRITE'


def test_storage_failure_has_uncertain_state_until_explicit_reenable(tmp_path, monkeypatch):
    owner, store = ChildDraftAutosave(), ChildDraftStore(tmp_path / 'drafts')
    def fail(data):
        raise OSError('PRIVATE content must not leak')
    monkeypatch.setattr(store, 'save', fail)
    owner.set_enabled(True)
    notices, rows = [], (entry('PRIVATE'),)
    owner.poll(store, rows, notices.append, flush=True)
    settle(owner, store, rows, notices)
    assert not owner.enabled and owner.durability == 'uncertain'
    assert 'PRIVATE' not in str(notices)
    owner.set_enabled(True)
    assert owner.durability == 'empty'
