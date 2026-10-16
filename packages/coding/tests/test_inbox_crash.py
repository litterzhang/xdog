"""Real process death leaves facts to review, never an executable replay queue."""

import os
import subprocess
import sys

from packages.coding.tests.test_inbox_store import box as inbox_box

box = inbox_box


def test_running_process_crash_requires_explicit_retry(box):
    store, message, manager, data = box
    store.enqueue(message)
    script = '''
import os,sys
from pathlib import Path
from xdog.coding.core.sessions.inbox_store import InboxStore
s=InboxStore(Path(sys.argv[1]),Path(sys.argv[2]))
a=s.reserve(sys.argv[3],('m1',),cwd=Path(sys.argv[4]),branch_id='',generation='old')
s.begin_batch(sys.argv[3],a,generation='old',batch_id='crash-batch')
os._exit(17)
'''
    process = subprocess.run([sys.executable, '-c', script, str(manager._dir), str(store.root),
                              message.session_id, str(message.cwd)], capture_output=True, text=True, timeout=20)
    assert process.returncode == 17, process.stderr
    assert store.records(message.session_id, message.cwd)[0].status == 'running'
    recovered = store.recover(message.session_id, 'new')
    assert recovered[0].status == 'interrupted'
    assert recovered[0].attempts[0].status == 'interrupted'
    assert store.recover(message.session_id, 'new') == recovered


def test_terminal_fact_survives_death_between_publication_and_directory_move(box, monkeypatch):
    store, message, manager, data = box
    store.enqueue(message)
    a = store.reserve(message.session_id, ('m1',), cwd=message.cwd, branch_id='', generation='g')[0]
    store.begin_batch(message.session_id, (a,), generation='g', batch_id='test-batch')[0]
    original = os.replace

    def fail_move(source, target, **kwargs):
        if str(source).endswith('.json') and str(target).endswith('.json'):
            raise OSError('died before directory move')
        return original(source, target, **kwargs)

    monkeypatch.setattr(os, 'replace', fail_move)
    try:
        store.finish(message.session_id, 'm1', generation='g', attempt_id=a.attempt_id, status='succeeded')
    except OSError:
        pass
    monkeypatch.setattr(os, 'replace', original)
    recovered = store.recover(message.session_id, 'new')
    assert recovered[0].status == 'succeeded'
    assert len(list(store.root.rglob('*.json'))) == 1
