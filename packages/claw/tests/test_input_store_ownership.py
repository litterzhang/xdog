"""Ownership, true process exit and admitted disk settlement on real sqlite stores."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputStore


def envelope(root, identifier='one'):
    return InputEnvelope(InputScope('main', root), SessionInput(identifier, 'human', 'TUI', 'check', (), {}))


@pytest.mark.parametrize('maximum', [True, False, 0, -1, 4097, 1.5, '5'])
async def test_capacity_is_a_strict_bounded_integer_before_touching_disk(tmp_path, maximum):
    path = tmp_path / 'private' / 'input.sqlite3'
    with pytest.raises(ValueError):
        await InputStore.open(path, max_pending=maximum)
    assert not path.parent.exists()


async def test_second_process_cannot_borrow_live_store_lease(tmp_path):
    path = tmp_path / 'private' / 'input.sqlite3'
    store = await InputStore.open(path)
    script = '''
import asyncio, sys
from pathlib import Path
from xdog.claw.core.inputs.store import InputStore
async def main():
    try:
        owner = await InputStore.open(Path(sys.argv[1]))
    except BlockingIOError:
        print('lease-refused')
    else:
        await owner.close()
        raise AssertionError('second process obtained a live lease')
asyncio.run(main())
'''
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', script, str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        assert process.returncode == 0, stderr.decode()
        assert stdout.strip() == b'lease-refused'
        assert (await store.enqueue(envelope(tmp_path)))[0].status == 'pending'
    finally:
        await store.close()


async def test_real_process_exit_recovers_unstarted_but_never_replays_running(tmp_path):
    path = tmp_path / 'private' / 'input.sqlite3'
    script = '''
import asyncio, os, sys
from pathlib import Path
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputStore
async def main():
    owner = await InputStore.open(Path(sys.argv[1]))
    for group, start in [('claimed', False), ('running', True)]:
        scope = InputScope(group, Path(sys.argv[2]))
        await owner.enqueue(InputEnvelope(scope, SessionInput('one','human','TUI','check',(),{})))
        claim = await owner.claim(scope)
        if start:
            await owner.start(claim)
    os._exit(0)
asyncio.run(main())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', script, str(path), str(tmp_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await asyncio.wait_for(process.communicate(), 15)
    assert process.returncode == 0, stderr.decode()
    store = await InputStore.open(path)
    try:
        claimed, running = InputScope('claimed', tmp_path), InputScope('running', tmp_path)
        assert (await store.get(claimed, 'one')).status == 'pending'
        assert (await store.claim(claimed)).status == 'claimed'
        assert (await store.get(running, 'one')).status == 'interrupted'
        assert await store.claim(running) is None
    finally:
        await store.close()


@pytest.mark.parametrize('raw', [b'not a sqlite file', b''])
async def test_unknown_or_corrupt_file_is_not_initialized_or_rewritten(tmp_path, raw):
    path = tmp_path / 'private' / 'input.sqlite3'
    path.parent.mkdir(mode=0o700)
    path.write_bytes(raw)
    path.chmod(0o600)
    for _ in range(2):
        with pytest.raises(ValueError, match='Unsupported|corrupt'):
            await InputStore.open(path)
        assert path.read_bytes() == raw


@pytest.mark.parametrize('mutation', ['payload', 'scope', 'status', 'attempt'])
async def test_invalid_receipt_facts_fail_before_any_recovery_mutation(tmp_path, mutation):
    path = tmp_path / 'private' / 'input.sqlite3'
    store = await InputStore.open(path)
    value = envelope(tmp_path)
    await store.enqueue(value)
    await store.claim(value.scope)
    await store.close()
    with sqlite3.connect(path) as db:
        if mutation in {'payload', 'scope'}:
            payload = json.loads(db.execute('SELECT payload FROM inputs').fetchone()[0])
            if mutation == 'payload':
                payload['human_authorized'] = 1
            else:
                payload['scope']['group_id'] = 'forged'
            db.execute('UPDATE inputs SET payload=?', (json.dumps(payload),))
        elif mutation == 'status':
            db.execute("UPDATE inputs SET status='invented-state'")
        else:
            db.execute("UPDATE attempts SET status='succeeded'")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        await InputStore.open(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize('link_parent', [False, True])
async def test_symlink_is_rejected_without_creating_or_changing_target(tmp_path, link_parent):
    target = tmp_path / 'target'
    target.mkdir(mode=0o700)
    if link_parent:
        alias = tmp_path / 'alias'
        alias.symlink_to(target, target_is_directory=True)
        path = alias / 'input.sqlite3'
    else:
        path = target / 'input.sqlite3'
        original = target / 'original'
        original.write_bytes(b'keep-me')
        path.symlink_to(original)
    with pytest.raises((ValueError, OSError)):
        await InputStore.open(path)
    if link_parent:
        assert list(target.iterdir()) == []
    else:
        assert original.read_bytes() == b'keep-me'


@pytest.mark.parametrize('disk_error', [False, True])
async def test_cancelled_commit_settles_before_close_releases_lease(tmp_path, monkeypatch, disk_error):
    path = tmp_path / 'private' / 'input.sqlite3'
    store = await InputStore.open(path)
    loop = asyncio.get_running_loop()
    admitted = asyncio.Event()
    release = threading.Event()
    threads = []
    real_connect = sqlite3.connect

    class PausedConnection(sqlite3.Connection):
        pause = False

        def execute(self, query, *args, **kwargs):
            threads.append(threading.get_ident())
            result = super().execute(query, *args, **kwargs)
            if query.startswith('INSERT INTO inputs('):
                self.pause = True
            return result

        def __exit__(self, kind, error, traceback):
            if self.pause:
                loop.call_soon_threadsafe(admitted.set)
                if not release.wait(10):
                    raise TimeoutError('test transaction release missing')
                if disk_error:
                    failure = OSError('admitted disk failure')
                    super().__exit__(OSError, failure, None)
                    raise failure
            return super().__exit__(kind, error, traceback)

    def connect(*args, **kwargs):
        return real_connect(*args, **kwargs, factory=PausedConnection)

    monkeypatch.setattr(sqlite3, 'connect', connect)
    writer = asyncio.create_task(store.enqueue(envelope(tmp_path)))
    closing = None
    try:
        await asyncio.wait_for(admitted.wait(), 5)
        writer.cancel()
        await asyncio.sleep(0)
        writer.cancel()
        closing = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not writer.done() and not closing.done()
        with pytest.raises(BlockingIOError):
            await InputStore.open(path)
        release.set()
        with pytest.raises(OSError if disk_error else asyncio.CancelledError):
            await writer
        await closing
        reopened = await InputStore.open(path)
        try:
            receipt = await reopened.get(InputScope('main', tmp_path), 'one')
            assert receipt is None if disk_error else receipt.status == 'pending'
        finally:
            await reopened.close()
        assert threads and threading.get_ident() not in threads
    finally:
        release.set()
        await asyncio.gather(writer, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await store.close()


@pytest.mark.parametrize('target', ['directory', 'database', 'lease'])
async def test_existing_permissions_are_rejected_not_silently_repaired(tmp_path, target):
    path = tmp_path / 'private' / 'input.sqlite3'
    store = await InputStore.open(path)
    await store.close()
    selected = {'directory': path.parent, 'database': path, 'lease': path.with_suffix('.sqlite3.lock')}[target]
    selected.chmod(0o755 if target == 'directory' else 0o644)
    before, mode = path.read_bytes(), selected.stat().st_mode
    with pytest.raises(ValueError, match='private'):
        await InputStore.open(path)
    assert path.read_bytes() == before and selected.stat().st_mode == mode
