"""Accepted inputs always retain bounded room for execution facts."""
from __future__ import annotations

import json

import pytest
from xdog.agent.session_input import SessionInput
from xdog.coding.core.sessions import store as storage
from xdog.coding.core.sessions.models import InputRecord


@pytest.mark.parametrize('error_character', ['\x01', '\U00010000'])
async def test_capacity_refuses_before_ack_and_reserves_terminal_facts(
        coding_session, monkeypatch, error_character):
    monkeypatch.setattr(storage, 'MAX_HISTORY_BYTES', 96 * 1024, raising=False)
    store = storage.InputStore(coding_session)
    accepted = []
    for index in range(100):
        value = SessionInput(f'input-{index}', 'human', 'test', 'x' * 2048, (), {})
        record = InputRecord(value, 'branch', 'generation', 'queued')
        previous = store.path.read_bytes() if store.path.exists() else None
        try:
            await store.accept(record)
        except ValueError as exc:
            assert 'capacity' in str(exc).lower()
            assert store.path.read_bytes() == previous
            break
        accepted.append(value.id)
    else:
        pytest.fail('InputStore acknowledged unbounded inputs past its reopen capacity')
    assert accepted
    assert store.path.stat().st_size <= storage.MAX_HISTORY_BYTES
    await store.transition_batch(tuple(accepted), 'running', batch_id='batch-1')
    await store.transition_batch(tuple(accepted), 'failed', batch_id='batch-1', error=error_character * 100_000)
    assert store.path.stat().st_size <= storage.MAX_HISTORY_BYTES
    reopened = storage.InputStore(coding_session)
    assert [row.input.id for row in reopened.records()] == accepted
    assert all(row.status == 'failed' and 'truncated' in row.error for row in reopened.records())
    assert len(json.loads(store.path.read_bytes())) == len(accepted)


async def test_edit_pending_cannot_spend_other_inputs_completion_reserve(coding_session, monkeypatch):
    monkeypatch.setattr(storage, 'MAX_HISTORY_BYTES', 128 * 1024, raising=False)
    store = storage.InputStore(coding_session)
    for index in range(2):
        value = SessionInput(f'input-{index}', 'human', 'test', 'hello', (), {})
        await store.accept(InputRecord(value, '', 'generation', 'queued'))
    original = store.path.read_bytes()
    with pytest.raises(ValueError, match='capacity'):
        await store.edit_pending(SessionInput('input-0', 'human', 'test', 'x' * 64_000, (), {}))
    assert store.path.read_bytes() == original
    await store.transition_batch(('input-0', 'input-1'), 'failed', batch_id='b', error='\x01' * 100_000)
    assert len(storage.InputStore(coding_session).records()) == 2


async def test_acknowledged_input_replacement_syncs_file_then_directory(coding_session, monkeypatch):
    import os
    import stat
    calls = []
    original = os.fsync
    def sync(fd):
        calls.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
        original(fd)
    monkeypatch.setattr(os, 'fsync', sync)
    store = storage.InputStore(coding_session)
    await store.accept(InputRecord(SessionInput('input', 'human', 'test', 'hello', (), {}), '', 'g', 'queued'))
    assert calls == ['file', 'directory']
    assert storage.InputStore(coding_session).records()[0].status == 'interrupted'


async def test_failed_replacement_preserves_previous_accepted_facts(coding_session, monkeypatch):
    import os
    store = storage.InputStore(coding_session)
    await store.accept(InputRecord(SessionInput('first', 'human', 'test', 'hello', (), {}), '', 'g', 'queued'))
    original = store.path.read_bytes()
    def fail(*args):
        raise OSError('replace failed')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError, match='replace failed'):
        await store.accept(InputRecord(SessionInput('second', 'human', 'test', 'bye', (), {}), '', 'g', 'queued'))
    assert store.path.read_bytes() == original
    assert [row.input.id for row in storage.InputStore(coding_session).records()] == ['first']


async def test_actual_64_mib_ledger_reopens_and_refuses_before_unfinishable_ack(coding_session):
    import asyncio
    from dataclasses import replace
    count = 65
    templates = tuple(InputRecord(SessionInput(f'completed-{i}', 'human', 'test', 'x', (), {}), '', 'g',
                                  'succeeded') for i in range(count))
    overhead = len(storage.InputStore._encode(templates)) - count
    length, remainder = divmod(storage.MAX_HISTORY_BYTES - 1024 - overhead, count)
    rows = tuple(replace(row, input=replace(row.input, instruction='x' * (length + (i < remainder))))
                 for i, row in enumerate(templates))
    store = storage.InputStore(coding_session)
    await asyncio.to_thread(store._commit, rows)
    original = store.path.stat()
    assert original.st_size == storage.MAX_HISTORY_BYTES - 1024
    reopened = await asyncio.to_thread(storage.InputStore, coding_session)
    assert len(reopened.records()) == count
    with pytest.raises(ValueError, match='capacity'):
        await reopened.accept(InputRecord(SessionInput('new', 'human', 'test', 'tiny', (), {}), '', 'g', 'queued'))
    current = store.path.stat()
    assert (current.st_ino, current.st_mtime_ns, current.st_size) == (
        original.st_ino, original.st_mtime_ns, original.st_size)
    assert len((await asyncio.to_thread(storage.InputStore, coding_session)).records()) == count
