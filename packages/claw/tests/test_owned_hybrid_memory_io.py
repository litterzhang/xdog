"""Optional search owns actual files, SQLite workers and local-model preparation."""
from __future__ import annotations

import asyncio
import builtins
import io
import os
import sqlite3
import sys
import threading
from pathlib import Path
from types import ModuleType

import aiosqlite
import pytest
from xdog.claw.core.memory.indexer import EmbeddingModel, MemoryIndexer
from xdog.claw.core.memory.search import HybridMemorySearch


class ScriptedEmbedding:
    async def encode(self, texts):
        return [[1.0] * EmbeddingModel.DIMENSIONS for _ in texts]

    async def encode_query(self, text):
        return [1.0] * EmbeddingModel.DIMENSIONS


@pytest.mark.parametrize('mode', ['indexer', 'hybrid'])
async def test_actual_optional_index_files_and_directories_are_read_off_loop(tmp_path, monkeypatch, mode):
    workspace = tmp_path / 'workspace'
    (workspace / 'memory').mkdir(parents=True)
    (workspace / 'conversations').mkdir()
    (workspace / 'MEMORY.md').write_text('Original evergreen evidence')
    (workspace / 'memory' / 'daily.md').write_text('Actual daily evidence')
    (workspace / 'conversations' / 'archive.md').write_text('Actual archived evidence')
    indexer = MemoryIndexer(tmp_path / 'index' / 'memory.db', ScriptedEmbedding())
    search = HybridMemorySearch(workspace, indexer.db_path, ScriptedEmbedding())
    if mode == 'hybrid':
        indexer = search._indexer
    owner, seen = threading.get_ident(), []
    original_open, original_io, original_stat, original_scan = builtins.open, io.open, os.stat, os.scandir

    def observe(actual, path, *args, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(path).is_relative_to(tmp_path):
            seen.append((str(path), threading.get_ident()))
        return actual(path, *args, **kwargs)

    try:
        with monkeypatch.context() as trace:
            trace.setattr(builtins, 'open', lambda p, *a, **kw: observe(original_open, p, *a, **kw))
            trace.setattr(io, 'open', lambda p, *a, **kw: observe(original_io, p, *a, **kw))
            trace.setattr(os, 'stat', lambda p, *a, **kw: observe(original_stat, p, *a, **kw))
            trace.setattr(os, 'scandir', lambda p, *a, **kw: observe(original_scan, p, *a, **kw))
            if mode == 'hybrid':
                await search.index_workspace()
            else:
                await indexer.reindex(workspace)
            chunks = await indexer.get_all_chunks()
        assert {chunk.source for chunk in chunks} == {'MEMORY.md', 'daily.md', 'archive.md'}
        assert seen and all(thread != owner for _path, thread in seen), ('Blocking optional memory IO', seen)
    finally:
        await indexer.close()


async def test_cancelled_sqlite_open_settles_real_connection_and_close_before_return(tmp_path, monkeypatch):
    indexer = MemoryIndexer(tmp_path / 'memory.db')
    entered, release, exited = (threading.Event() for _ in range(3))
    opened = []
    actual = sqlite3.connect

    def connect(*args, **kwargs):
        connection = actual(*args, **kwargs)
        entered.set()
        try:
            assert release.wait(5)
            return connection
        finally:
            exited.set()

    original = aiosqlite.connect

    def proxy(*args, **kwargs):
        connection = original(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setattr(aiosqlite, 'connect', proxy)
    work = asyncio.create_task(indexer.get_all_chunks())
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        assert not work.done() and not exited.is_set(), 'SQLite acquisition escaped its actual owner'
        closing = asyncio.create_task(indexer.close())
        await asyncio.sleep(0)
        assert not closing.done(), 'Indexer closed before its admitted connection factory settled'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        await closing
        assert indexer._conn is None and len(opened) == 1
        await asyncio.to_thread(getattr(opened[0], "_thread", opened[0]).join, 3)
        assert not getattr(opened[0], "_thread", opened[0]).is_alive()
    finally:
        release.set()
        await asyncio.gather(*(task for task in (work, closing) if task is not None), return_exceptions=True)
        await indexer.close()
        # Original broken aiosqlite acquisition can lose the proxy before its
        # worker exits. Explicitly settle this real test-owned resource on RED.
        for connection in opened:
            await connection.close()
            await asyncio.to_thread(getattr(connection, "_thread", connection).join, 3)


@pytest.mark.parametrize('cancel', [False, True])
async def test_local_embedding_load_and_encode_are_owned_worker_work(tmp_path, monkeypatch, cancel):
    module = ModuleType('sentence_transformers')
    owner, calls = threading.get_ident(), []
    entered, release = threading.Event(), threading.Event()
    weights = tmp_path / 'weights.txt'
    weights.write_text('actual local model fixture')

    class Row:
        def tolist(self):
            return [1.0, 2.0]

    class LocalModel:
        def __init__(self, *args, **kwargs):
            assert weights.read_text() == 'actual local model fixture'
            calls.append(('load', threading.get_ident()))
            entered.set()
            if cancel and threading.get_ident() != owner:
                assert release.wait(5)

        def encode(self, texts, **kwargs):
            calls.append(('encode', threading.get_ident()))
            return [Row() for _ in texts]

    module.SentenceTransformer = LocalModel
    monkeypatch.setitem(sys.modules, 'sentence_transformers', module)
    model = EmbeddingModel(cache_dir=tmp_path / 'model-cache')
    work = asyncio.create_task(model.encode(['original text']))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        if cancel:
            for _ in range(3):
                work.cancel()
                await asyncio.sleep(0)
            assert not work.done(), 'Local model preparation escaped cancellation ownership'
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await work
        else:
            assert await work == [[1.0, 2.0]]
        assert calls and all(thread != owner for _action, thread in calls), calls
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)


async def test_cancelled_index_commit_settles_real_sqlite_before_releasing_owner(tmp_path, monkeypatch):
    entered, release, exited = (threading.Event() for _ in range(3))
    armed = False
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def commit(self):
            value = super().commit()
            if armed:
                entered.set()
                try:
                    assert release.wait(5)
                finally:
                    exited.set()
            return value

    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: original(*a, factory=Connection, **kw))
    file = tmp_path / 'MEMORY.md'
    file.write_text('A committed original evidence row')
    indexer = MemoryIndexer(tmp_path / 'memory.db')
    work = None
    try:
        await indexer._init_db()
        armed = True
        work = asyncio.create_task(indexer.index_file(file, 'MEMORY.md'))
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        assert not work.done() and not exited.is_set(), 'Actual SQLite commit outlived cancelled index ownership'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert exited.is_set()
        assert [row.text for row in await indexer.get_all_chunks()] == ['A committed original evidence row']
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await indexer.close()


async def test_index_readers_cannot_observe_partial_source_replacement(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    armed = False
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            value = super().execute(statement, *args, **kwargs)
            if armed and statement.startswith('DELETE FROM chunks WHERE source'):
                entered.set()
                assert release.wait(5)
            return value

    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: original(*a, factory=Connection, **kw))
    file = tmp_path / 'MEMORY.md'
    file.write_text('Original source snapshot')
    indexer = MemoryIndexer(tmp_path / 'memory.db')
    work = reading = None
    try:
        await indexer.index_file(file, 'MEMORY.md')
        previous = file.stat().st_mtime
        file.write_text('Replacement committed snapshot')
        os.utime(file, (previous + 2, previous + 2))
        armed = True
        work = asyncio.create_task(indexer.index_file(file, 'MEMORY.md'))
        assert await asyncio.to_thread(entered.wait, 3)
        read_started = asyncio.Event()

        async def read():
            read_started.set()
            return await indexer.get_all_chunks()

        reading = asyncio.create_task(read())
        await read_started.wait()
        release.set()
        await work
        assert [row.text for row in await reading] == ['Replacement committed snapshot']
    finally:
        release.set()
        await asyncio.gather(*(task for task in (work, reading) if task is not None), return_exceptions=True)
        await indexer.close()


async def test_failed_index_replacement_rolls_back_original_rows_before_next_reader(tmp_path, monkeypatch):
    original = sqlite3.connect
    armed = False

    class Connection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            value = super().execute(statement, *args, **kwargs)
            if armed and statement.startswith('INSERT INTO chunks'):
                raise sqlite3.OperationalError('Actual derived-index insert boundary failed')
            return value

    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: original(*a, factory=Connection, **kw))
    file = tmp_path / 'MEMORY.md'
    file.write_text('Original stable index evidence')
    indexer = MemoryIndexer(tmp_path / 'memory.db')
    try:
        await indexer.index_file(file, 'MEMORY.md')
        previous = file.stat().st_mtime
        file.write_text('Failed replacement evidence')
        os.utime(file, (previous + 2, previous + 2))
        armed = True
        with pytest.raises(sqlite3.OperationalError, match='insert boundary'):
            await indexer.index_file(file, 'MEMORY.md')
        assert [row.text for row in await indexer.get_all_chunks()] == ['Original stable index evidence']
    finally:
        await indexer.close()


async def test_indexer_close_joins_actual_sqlite_thread_exit(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    target, name = ((aiosqlite.core, '_connection_worker_thread')
                    if hasattr(aiosqlite.core, '_connection_worker_thread') else (aiosqlite.Connection, 'run'))
    original = getattr(target, name)

    def actual_worker(*args):
        original(*args)
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(target, name, actual_worker)
    indexer = MemoryIndexer(tmp_path / 'memory.db')
    closing = None
    try:
        await indexer._init_db()
        connection = indexer._conn
        worker = getattr(connection, '_thread', connection)
        closing = asyncio.create_task(indexer.close())
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            closing.cancel()
            await asyncio.sleep(0)
        assert not closing.done() and worker.is_alive(), 'SQLite worker exited after its close owner returned'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        await indexer.close()
        assert not worker.is_alive() and indexer._conn is None
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await indexer.close()


async def test_api_embedding_fallback_keeps_original_loop_and_detached_texts(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'sentence_transformers', None)
    owner = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def embed(text):
        assert threading.get_ident() == owner
        calls.append(text)
        return [1.0, 2.0]

    model = EmbeddingModel(cache_dir=tmp_path / 'cache', embed_fn=embed)
    original = model._encode_local

    def actual_prepare(texts):
        value = original(texts)
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(model, '_encode_local', actual_prepare)
    texts = ['Original immutable file evidence']
    work = asyncio.create_task(model.encode(texts))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        texts.append('Not part of the prepared batch')
        release.set()
        assert await work == [[1.0, 2.0]] and calls == ['Original immutable file evidence']
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('operation', ['read_text', 'stat'])
async def test_optional_index_io_failure_is_not_reported_as_absent_memory(tmp_path, monkeypatch, operation):
    file = tmp_path / 'MEMORY.md'
    file.write_text('Original retained index evidence')
    indexer = MemoryIndexer(tmp_path / 'memory.db')
    original = getattr(Path, operation)

    def actual_read_then_fail(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == file:
            raise PermissionError('Actual memory IO unavailable')
        return result

    try:
        await indexer.index_file(file, 'MEMORY.md')
        with monkeypatch.context() as fault:
            fault.setattr(Path, operation, actual_read_then_fail)
            with pytest.raises(PermissionError, match='memory IO unavailable'):
                await indexer.index_file(file, 'MEMORY.md')
        assert [row.text for row in await indexer.get_all_chunks()] == ['Original retained index evidence']
    finally:
        await indexer.close()
