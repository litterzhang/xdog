"""Filesystem edit transactions shared by real, independently owned debug loops."""
from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import queue
import threading
import weakref
from pathlib import Path

import pytest
from xdog.agent.core import AgentToolResult
from xdog.agent.tools import tool_filesystem as filesystem


class Owner:
    def __init__(self):
        ready = queue.Queue()
        self.errors = []

        def run():
            with asyncio.Runner(debug=True) as runner:
                loop = runner.get_loop()
                loop.set_exception_handler(lambda _, context: self.errors.append(context))
                ready.put(loop)
                loop.run_forever()

        self.thread = threading.Thread(target=run, name='filesystem-test-owner')
        self.thread.start()
        self.loop = ready.get(timeout=5)

    def submit(self, work):
        return asyncio.run_coroutine_threadsafe(work, self.loop)

    def ping(self):
        async def ping():
            assert asyncio.get_running_loop().get_debug()
            return threading.get_ident()

        assert self.submit(ping()).result(timeout=2) == self.thread.ident

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()
        assert not self.errors


@pytest.fixture
def owners():
    first, second = Owner(), Owner()
    try:
        yield first, second
    finally:
        try:
            first.close()
        finally:
            second.close()


@pytest.mark.parametrize('alias', [False, True])
def test_cross_owner_edits_serialize_the_whole_transaction_without_blocking_loops(
    tmp_path, monkeypatch, owners, alias,
):
    first, second = owners
    path = tmp_path / 'shared.txt'
    path.write_text('alpha\nbeta\n', encoding='utf-8')
    other_path = tmp_path / 'other.txt'
    other_path.write_text('other', encoding='utf-8')
    second_path = tmp_path / 'alias.txt' if alias else path
    if alias:
        second_path.symlink_to(path)
    reading, release, second_read, admitted = (threading.Event() for _ in range(4))
    reads, writes, validations = [], [], []
    competing_validated = threading.Event()
    original_validate = filesystem.validate_path
    original_read, original_write = Path.read_text, Path.write_text

    def read(target, *args, **kwargs):
        content = original_read(target, *args, **kwargs)
        if target.resolve() == path:
            reads.append((threading.get_ident(), content))
            if len(reads) == 1:
                reading.set()
                assert release.wait(5), 'test did not release first transaction'
            else:
                second_read.set()
        return content

    def write(target, *args, **kwargs):
        if target.resolve() == path:
            writes.append(threading.get_ident())
        return original_write(target, *args, **kwargs)

    def validate(*args, **kwargs):
        validations.append(threading.get_ident())
        result = original_validate(*args, **kwargs)
        if reading.is_set() and args[0] == str(second_path):
            competing_validated.set()
        return result

    monkeypatch.setattr(filesystem, 'validate_path', validate)
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(Path, 'write_text', write)

    async def edit(target, old, new, *, started=None):
        # Distinct tool instances stand in for independent host owners.
        if started is not None:
            started.set()
        return await filesystem.FilesystemTool().edit({}, str(target), old_string=old, new_string=new)

    left = first.submit(edit(path, 'alpha', 'ALPHA'))
    right = None
    checked = False
    try:
        assert reading.wait(3)
        right = second.submit(edit(second_path, 'beta', 'BETA', started=admitted))
        assert admitted.wait(3)
        assert competing_validated.wait(3)  # Both real owners have entered edit on the shared file.
        second.ping()  # Waiting for the path lock must also be off-loop.
        first.ping()  # The first owner's blocked read must not stall its loop.
        shared_lock = filesystem._edit_lock(path)
        assert shared_lock.locked()
        assert filesystem._edit_lock(second_path) is shared_lock
        # A different file must progress while the original transaction is held.
        assert isinstance(second.submit(edit(other_path, 'other', 'OTHER')).result(timeout=2), AgentToolResult)
        assert not second_read.is_set(), 'same-path transaction read before its predecessor wrote'
        checked = True
    finally:
        # On a regression failure, cancel a pending foreign-loop waiter before
        # releasing the first transaction. A broken asyncio.Lock release can
        # otherwise lose its wakeup and strand the debug loop during teardown.
        if not checked and right is not None:
            right.cancel()
            second.ping()
        release.set()
        left.result(timeout=5)
        if right is not None and not right.cancelled():
            right.result(timeout=5)
    assert original_read(path, encoding='utf-8') == 'ALPHA\nBETA\n'
    assert [content for _, content in reads] == ['alpha\nbeta\n', 'ALPHA\nbeta\n']
    assert len(writes) == 2
    io_threads = validations + writes + [ident for ident, _ in reads]
    assert not {first.thread.ident, second.thread.ident}.intersection(io_threads)


@pytest.mark.parametrize('late_failure', [False, True])
def test_cancelled_edit_joins_actual_worker_and_never_publishes_late_result(
    tmp_path, monkeypatch, owners, late_failure,
):
    first, second = owners
    path = tmp_path / 'cancel.txt'
    path.write_text('before', encoding='utf-8')
    writing, release, committed = (threading.Event() for _ in range(3))
    tasks = queue.Queue()
    published, calls = [], []
    original_write = Path.write_text

    def write(target, *args, **kwargs):
        if target == path:
            calls.append(threading.get_ident())
            writing.set()
            assert release.wait(5)
            result = original_write(target, *args, **kwargs)
            committed.set()
            if late_failure:
                raise OSError('late worker failure after write')
            return result
        return original_write(target, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', write)

    async def edit():
        tasks.put(asyncio.current_task())
        result = await filesystem.FilesystemTool().edit({}, str(path), old_string='before', new_string='after')
        published.append(result)
        return result

    future = first.submit(edit())
    try:
        assert writing.wait(3)
        task = tasks.get(timeout=3)
        for _ in range(2):
            first.loop.call_soon_threadsafe(task.cancel)
            first.ping()  # Both initial and repeated cancellation remain responsive.
            assert not future.done(), 'cancel returned before the actual write settled'
        second.ping()
        assert not committed.is_set() and not published
    finally:
        release.set()
    with pytest.raises(concurrent.futures.CancelledError):
        future.result(timeout=5)
    assert committed.is_set() and path.read_text(encoding='utf-8') == 'after'
    assert len(calls) == 1 and not published
    monkeypatch.setattr(Path, 'write_text', original_write)
    # A later explicit edit from a different loop can acquire the released path lock.
    result = second.submit(filesystem.FilesystemTool().edit(
        {}, str(path), old_string='after', new_string='explicit',
    )).result(timeout=3)
    assert isinstance(result, AgentToolResult)
    assert path.read_text(encoding='utf-8') == 'explicit'


@pytest.mark.parametrize('args,expected,error', [
    ({'old_string': 'alpha', 'new_string': 'ALPHA'}, '\ufeffalpha\nbeta\nalpha\n', 'must be unique'),
    ({'old_string': 'alpha', 'new_string': 'ALPHA', 'replace_all': True}, '\ufeffALPHA\nbeta\nALPHA\n', None),
    ({'edits': [{'old_string': 'beta', 'new_string': 'BETA'},
                {'old_string': 'missing', 'new_string': 'new'}]}, '\ufeffalpha\nbeta\nalpha\n', 'Could not find'),
    ({'edits': [{'old_string': 'beta', 'new_string': 'BETA'}]}, '\ufeffalpha\nBETA\nalpha\n', None),
    ({}, '\ufeffalpha\nbeta\nalpha\n', 'provide'),
])
async def test_off_loop_edit_preserves_bom_batch_errors_and_replace_all(tmp_path, args, expected, error):
    path = tmp_path / 'edit.txt'
    initial = '\ufeffalpha\nbeta\nalpha\n'
    path.write_text(initial, encoding='utf-8')
    result = await filesystem.FilesystemTool().edit({}, str(path), **args)
    if error:
        assert isinstance(result, str) and error in result
        assert path.read_text(encoding='utf-8') == initial  # Failed batches never partially write.
    else:
        assert isinstance(result, AgentToolResult)
        assert path.read_text(encoding='utf-8') == expected


def test_cancel_while_waiting_for_another_owner_still_settles_exactly_once(tmp_path, monkeypatch, owners):
    first, second = owners
    path = tmp_path / 'queued.txt'
    path.write_text('alpha beta', encoding='utf-8')
    reading, release, dispatched = (threading.Event() for _ in range(3))
    tasks = queue.Queue()
    calls, published = [], []
    original_read = Path.read_text
    original_transaction = filesystem._edit_transaction
    original_edit = filesystem._fs_edit

    def read(target, *args, **kwargs):
        result = original_read(target, *args, **kwargs)
        if target == path and not reading.is_set():
            reading.set()
            assert release.wait(5)
        return result

    def transaction(target, ctx, args):
        if args['old_string'] == 'beta':
            dispatched.set()
        return original_transaction(target, ctx, args)

    def edit(target, args):
        calls.append(args['old_string'])
        return original_edit(target, args)

    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(filesystem, '_edit_transaction', transaction)
    monkeypatch.setattr(filesystem, '_fs_edit', edit)

    async def queued():
        tasks.put(asyncio.current_task())
        result = await filesystem.FilesystemTool().edit({}, str(path), old_string='beta', new_string='BETA')
        published.append(result)

    left = first.submit(filesystem.FilesystemTool().edit({}, str(path), old_string='alpha', new_string='ALPHA'))
    right = None
    try:
        assert reading.wait(3)
        right = second.submit(queued())
        assert dispatched.wait(3)
        task = tasks.get(timeout=3)
        for _ in range(2):
            second.loop.call_soon_threadsafe(task.cancel)
            second.ping()
            assert not right.done()
        first.ping()
        assert calls == ['alpha']
    finally:
        release.set()
        left.result(timeout=5)
    assert right is not None
    with pytest.raises(concurrent.futures.CancelledError):
        right.result(timeout=5)
    assert calls == ['alpha', 'beta'] and not published
    assert path.read_text(encoding='utf-8') == 'ALPHA BETA'


def test_path_locks_are_shared_but_not_retained_forever(tmp_path):
    paths = [tmp_path / f'file-{index}' for index in range(64)]
    locks = [filesystem._edit_lock(path) for path in paths]
    assert len({id(lock) for lock in locks}) == len(paths)
    assert filesystem._edit_lock(paths[0]) is locks[0]
    alias = tmp_path / 'alias'
    alias.symlink_to(paths[0])
    assert filesystem._edit_lock(alias) is locks[0]
    references = [weakref.ref(lock) for lock in locks]
    del locks
    gc.collect()
    assert all(reference() is None for reference in references)
    with filesystem._file_locks_guard:
        assert not any(str(path.resolve()) in filesystem._file_locks for path in paths)


@pytest.mark.parametrize('path_kind,error', [
    ('missing', 'file not found'), ('directory', 'directory'), ('relative', 'absolute'),
    ('outside', 'outside'), ('traversal', 'traversal'),
])
async def test_off_loop_edit_preserves_path_validation(tmp_path, path_kind, error):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    ctx = {}
    path = str(tmp_path / 'missing')
    if path_kind == 'directory':
        path = str(workspace)
    elif path_kind == 'relative':
        path = 'relative'
    elif path_kind == 'outside':
        ctx[filesystem.CONFINE_CTX_KEY] = [workspace]
    elif path_kind == 'traversal':
        path = str(workspace / '..' / 'missing')
    result = await filesystem.FilesystemTool().edit(ctx, path, old_string='old', new_string='new')
    assert isinstance(result, str) and error in result


async def test_off_loop_edit_keeps_workspace_fuzzy_matching_and_diff(tmp_path):
    path = tmp_path / 'edit.txt'
    path.write_text('say “hello”\n', encoding='utf-8')
    result = await filesystem.FilesystemTool().edit(
        {filesystem.WORKSPACE_CTX_KEY: tmp_path, filesystem.CONFINE_CTX_KEY: [tmp_path]},
        'edit.txt', old_string='say "hello"', new_string='say goodbye',
    )
    assert isinstance(result, AgentToolResult)
    assert path.read_text(encoding='utf-8') == 'say goodbye'  # Existing fuzzy normalization strips the final newline.
    assert '-1 say "hello"' in result.content[0].text
    assert '+1 say goodbye' in result.content[0].text


def test_uncancelled_worker_failure_is_not_hidden_and_releases_path(tmp_path, monkeypatch, owners):
    first, second = owners
    path = tmp_path / 'failure.txt'
    path.write_text('before', encoding='utf-8')
    original_write = Path.write_text

    def fail(target, *args, **kwargs):
        if target == path:
            raise OSError('write refused')
        return original_write(target, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', fail)
    with pytest.raises(OSError, match='write refused'):
        first.submit(filesystem.FilesystemTool().edit(
            {}, str(path), old_string='before', new_string='after',
        )).result(timeout=3)
    assert path.read_text(encoding='utf-8') == 'before'
    monkeypatch.setattr(Path, 'write_text', original_write)
    second.submit(filesystem.FilesystemTool().edit(
        {}, str(path), old_string='before', new_string='after',
    )).result(timeout=3)
    assert path.read_text(encoding='utf-8') == 'after'
