"""UI-047 owned Linux crash evidence, not per-key or physical power-loss claims."""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time

import pytest
from xdog.tui.child_draft_autosave import ChildDraftAutosave
from xdog.tui.child_draft_store import ChildDraftStore
from xdog.tui.components.status_line import StatusLine


@pytest.mark.skipif(sys.platform != 'linux', reason='Current Linux owned SIGKILL/private storage evidence')
@pytest.mark.parametrize('surface', ['parent', 'child'])
@pytest.mark.parametrize('boundary', ['debounce', 'before-update', 'after-replace',
                                      'receipt-unpolled', 'edited-inflight'])
def test_owned_crash_recovers_only_published_snapshot_without_auto_adoption(tmp_path, surface, boundary):
    script = '''import sys,time,json,threading,os
from pathlib import Path
from xdog.tui.child_draft_autosave import ChildDraftAutosave
from xdog.tui.child_draft_store import ChildDraftStore
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.child_draft_panel import DraftEntry
import xdog.tui.child_draft_autosave as module
root=Path(sys.argv[1]); surface=sys.argv[2]; boundary=sys.argv[3]
store=ChildDraftStore(root); owner=ChildDraftAutosave(); owner.set_enabled(True)
def row(text):
    draft=ChildDraft(text,access='inherit',config={'tools':['bash']})
    return DraftEntry('old-session',(surface if surface=='parent' else 'steer','slot'),draft,draft,'old',True)
def receipt():
    print(json.dumps({'state':owner.durability}),flush=True)
    threading.Event().wait(30)
    raise RuntimeError('Owned crash boundary not killed')
old=(row('OLD COMMITTED'),)
owner.poll(store,old,lambda _:None,flush=True)
while owner.busy:
    owner.poll(store,old,lambda _:None);time.sleep(.001)
assert owner.durability=='committed'
module.DEBOUNCE=60
new=(row('NEW WRITING'),)
if boundary=='debounce':
    owner.poll(store,new,lambda _:None)
    receipt()
original=store.update
entered=threading.Event(); release=threading.Event()
def update(*args):
    if boundary=='before-update': receipt()
    if boundary=='edited-inflight':
        entered.set();assert release.wait(20)
    return original(*args)
store.update=update
replace=os.replace
def replace_owned(*args,**kwargs):
    replace(*args,**kwargs)
    if boundary=='after-replace': receipt()
os.replace=replace_owned
owner.poll(store,new,lambda _:None,flush=True)
if boundary=='edited-inflight':
    assert entered.wait(3)
    owner.poll(store,(row('NEWER NOT WRITTEN'),),lambda _:None)
    receipt()
if boundary=='receipt-unpolled':
    deadline=time.monotonic()+5
    while owner.io._thread.is_alive() and time.monotonic()<deadline:time.sleep(.001)
    assert not owner.io._thread.is_alive()
    receipt()
threading.Event().wait(30)
'''
    root = tmp_path / 'drafts'
    process = subprocess.Popen([sys.executable, '-I', '-c', script, str(root), surface, boundary],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 8
        data = bytearray()
        while not data.endswith(b'\n'):
            remaining = deadline - time.monotonic()
            assert remaining > 0 and select.select([process.stdout], [], [], remaining)[0], 'Missing owned receipt'
            byte = os.read(process.stdout.fileno(), 1)
            assert byte, 'Owned process exited before crash boundary'
            data.extend(byte)
        observed = json.loads(data)
        assert observed['state'] == ('pending' if boundary == 'debounce' else 'writing')
        process.kill()
        assert process.wait(timeout=3) == -9
        records = ChildDraftStore(root).list()
        assert len(records) == 1
        expected = 'NEW WRITING' if boundary in {'after-replace', 'receipt-unpolled'} else 'OLD COMMITTED'
        saved = records[0]
        assert saved.draft.text == expected and saved.draft.uncertain
        assert saved.draft.access == 'read-only' and saved.draft.config == {'inherit_context': False}
        assert not ChildDraftAutosave().enabled  # No composer, policy or automatic submission adoption.
        assert saved.source_action == ('parent' if surface == 'parent' else 'steer')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()


def test_status_durability_remains_width_bounded_and_survives_activity_updates():
    from xdog.tui.utils import string_width
    for structured in (False, True):
        status = StatusLine(lambda text: text, structured=structured)
        assert status.set_durability('parent draft:pending')
        status.set_activity('waiting for model')
        status.set_text('connected | idle')
        assert 'draft:pending' in status.render(80)[0]
        assert string_width(status.render(12)[0]) <= 12
        assert not status.set_durability('parent draft:pending')
        assert status.set_durability('parent draft:committed')
        assert 'draft:committed' in status.render(80)[0]


@pytest.mark.skipif(sys.platform != 'linux', reason='Current Linux owned link-publication SIGKILL proof')
def test_owned_initial_publication_crash_reads_only_valid_private_alias_without_cleanup(tmp_path):
    script = """import os,sys,json,threading
from pathlib import Path
from xdog.tui.child_draft_store import ChildDraftStore,encode_draft
from xdog.tui.child_drafts import ChildDraft
link=os.link
def owned_link(source,destination,**kwargs):
    link(source,destination,**kwargs)
    print(json.dumps({'id':destination[:-5]}),flush=True)
    threading.Event().wait(30)
os.link=owned_link
store=ChildDraftStore(Path(sys.argv[1]))
store.save(encode_draft(ChildDraft('INITIAL PUBLISHED'),label='old',scope='old',action='parent',slot=''))
"""
    root = tmp_path / 'drafts'
    process = subprocess.Popen([sys.executable, '-I', '-c', script, str(root)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        data = bytearray()
        deadline = time.monotonic() + 2
        while not data.endswith(b'\n'):
            remaining = deadline - time.monotonic()
            assert remaining > 0 and select.select([process.stdout], [], [], remaining)[0]
            byte = os.read(process.stdout.fileno(), 1)
            assert byte
            data.extend(byte)
        identifier = json.loads(data)['id']
        process.kill()
        assert process.wait(timeout=3) == -9
        path = root / (identifier + '.json')
        assert path.stat().st_nlink == 2
        saved = ChildDraftStore(root).load(identifier)
        assert saved.draft.text == 'INITIAL PUBLISHED' and saved.draft.uncertain
        assert path.stat().st_nlink == 2 and len(list(root.glob('.draft-*'))) == 1
        # Recovery reads never delete files, even confirmed publication aliases.
        assert ChildDraftStore(root).load(identifier).sha256 == saved.sha256
        ChildDraftStore(root).discard(identifier, saved.sha256)
        assert not path.exists() and not list(root.glob('.draft-*'))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()
