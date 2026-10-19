"""Protocol-neutral scope identities and bounded status inspection on the real store."""
from __future__ import annotations

import sqlite3

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputStore


def value(root, identifier='one', group='main'):
    return InputEnvelope(InputScope(group, root), SessionInput(identifier, 'acp', 'Eyes', 'check', (), {}))


async def test_namespace_is_stable_across_reopen_and_isolated_by_explicit_scope(tmp_path):
    path = tmp_path / 'private' / 'inputs.sqlite3'
    store = await InputStore.open(path)
    scope = InputScope('main', tmp_path)
    first = await store.bind_scope(scope)
    assert first and await store.bind_scope(scope) == first
    other = await store.bind_scope(InputScope('main', tmp_path / 'other'))
    assert other != first
    await store.close()
    store = await InputStore.open(path)
    try:
        assert await store.namespace(scope) == first
        assert await store.namespace(InputScope('main', tmp_path / 'other')) == other
    finally:
        await store.close()


async def test_enqueue_registers_namespace_and_inspection_does_not_create_unknown_scopes(tmp_path):
    path = tmp_path / 'private' / 'inputs.sqlite3'
    store = await InputStore.open(path)
    item = value(tmp_path)
    try:
        await store.enqueue(item)
        namespace = await store.namespace(item.scope)
        original = path.read_bytes()
        assert await store.namespace(item.scope) == namespace
        assert await store.status(InputScope('unknown', tmp_path)) == ([], None)
        with pytest.raises(ValueError, match='scope'):
            await store.namespace(InputScope('unknown', tmp_path))
        assert path.read_bytes() == original
    finally:
        await store.close()


async def test_status_pages_keep_stable_order_and_only_execution_receipt_fields(tmp_path):
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    scope = InputScope('main', tmp_path)
    try:
        for identifier in ('one', 'two', 'three'):
            await store.enqueue(value(tmp_path, identifier))
        run = await store.start(await store.claim(scope), session_id='actual-context')
        await store.finish(run, 'failed', error='x' * 2048)
        first, cursor = await store.status(scope, limit=2)
        assert [r['message_id'] for r in first] == ['one', 'two'] and cursor == 2
        assert first[0]['status'] == 'failed' and first[0]['session_id'] == 'actual-context'
        assert first[0]['attempt_id'] == run.attempt_id and first[0]['error'] == 'x' * 1024
        assert set(first[0]) == {'message_id', 'status', 'attempt_id', 'session_id', 'error'}
        last, cursor = await store.status(scope, cursor=cursor, limit=2)
        assert [r['message_id'] for r in last] == ['three'] and cursor is None
        selected, cursor = await store.status(scope, selected='two')
        assert selected[0]['message_id'] == 'two' and cursor is None
        assert await store.status(InputScope('other', tmp_path), selected='one') == ([], None)
    finally:
        await store.close()


@pytest.mark.parametrize('options', [{'cursor': True}, {'cursor': -1}, {'cursor': '0'}, {'limit': True},
                                     {'limit': 0}, {'limit': 129}, {'selected': ''},
                                     {'selected': 'one', 'cursor': 1}, {'selected': 'bad\x00id'}])
async def test_status_rejects_invalid_selection_without_mutating_receipts(tmp_path, options):
    path = tmp_path / 'private' / 'inputs.sqlite3'
    store = await InputStore.open(path)
    item = value(tmp_path)
    try:
        await store.enqueue(item)
        before = path.read_bytes()
        with pytest.raises(ValueError):
            await store.status(item.scope, **options)
        assert path.read_bytes() == before
    finally:
        await store.close()


async def test_previous_core_format_is_rejected_without_schema_upgrade(tmp_path):
    path = tmp_path / 'private' / 'inputs.sqlite3'
    path.parent.mkdir(mode=0o700)
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE metadata(format TEXT);
            CREATE TABLE inputs(sequence INTEGER PRIMARY KEY,group_id TEXT,workspace TEXT,message_id TEXT,
                payload TEXT,status TEXT,attempt_id TEXT,response TEXT,error TEXT,session_id TEXT);
            CREATE TABLE attempts(attempt_id TEXT PRIMARY KEY,sequence INTEGER,status TEXT,
                response TEXT,error TEXT,session_id TEXT);''')
        db.execute("INSERT INTO metadata VALUES ('xdog.claw.inbox.v1')")
    path.chmod(0o600)
    original = path.read_bytes()
    with pytest.raises(ValueError, match='Unsupported'):
        await InputStore.open(path)
    assert path.read_bytes() == original
