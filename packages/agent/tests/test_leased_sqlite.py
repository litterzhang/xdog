"""Actual private journal IO; no schema migration or background/runtime startup."""
from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest
from xdog.agent.leased_sqlite import LeasedSqlite

SCHEMA = 'CREATE TABLE facts(value TEXT NOT NULL);'


def validate(db):
    db.execute('SELECT value FROM facts LIMIT 0')


async def test_atomic_new_schema_failure_does_not_poison_the_final_path(tmp_path):
    path = tmp_path / 'private' / 'state.sqlite3'
    with pytest.raises(sqlite3.DatabaseError):
        await LeasedSqlite.open(path, format='test.v1', schema='CREATE BROKEN SQL;', validate=validate)
    assert not path.exists()
    owner = await LeasedSqlite.open(path, format='test.v1', schema=SCHEMA, validate=validate)
    try:
        await owner.disk(lambda db: db.execute("INSERT INTO facts VALUES ('durable')"))
        assert await owner.disk(lambda db: db.execute('SELECT value FROM facts').fetchone()[0]) == 'durable'
    finally:
        await owner.close()


async def test_unknown_format_and_failed_validation_never_modify_existing_data(tmp_path):
    path = tmp_path / 'private' / 'state.sqlite3'
    owner = await LeasedSqlite.open(path, format='test.v1', schema=SCHEMA, validate=validate)
    await owner.disk(lambda db: db.execute("INSERT INTO facts VALUES ('keep me')"))
    await owner.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match='Unsupported'):
        await LeasedSqlite.open(path, format='test.v2', schema='ALTER TABLE facts ADD COLUMN unwanted TEXT;',
                                validate=validate)
    assert path.read_bytes() == before
    def mutating_validation(db):
        db.execute("UPDATE facts SET value='not allowed'")
    with pytest.raises(ValueError, match='corrupt|Unsupported'):
        await LeasedSqlite.open(path, format='test.v1', schema=SCHEMA, validate=mutating_validation)
    assert path.read_bytes() == before


@pytest.mark.parametrize('target', ['directory', 'file', 'lease'])
async def test_private_permissions_are_enforced_without_repairs(tmp_path, target):
    path = tmp_path / 'private' / 'state.sqlite3'
    owner = await LeasedSqlite.open(path, format='test.v1', schema=SCHEMA, validate=validate)
    await owner.close()
    altered = {'directory': path.parent, 'file': path, 'lease': path.with_suffix('.sqlite3.lock')}[target]
    altered.chmod(0o755 if target == 'directory' else 0o644)
    before, mode = path.read_bytes(), altered.stat().st_mode
    with pytest.raises(ValueError, match='private'):
        await LeasedSqlite.open(path, format='test.v1', schema=SCHEMA, validate=validate)
    assert path.read_bytes() == before and altered.stat().st_mode == mode


@pytest.mark.parametrize('parent_link', [False, True])
async def test_symlink_targets_are_not_followed_or_written(tmp_path, parent_link):
    real = tmp_path / 'real'
    real.mkdir(mode=0o700)
    target = real / 'original'
    target.write_bytes(b'preserve')
    if parent_link:
        link = tmp_path / 'link'
        link.symlink_to(real, target_is_directory=True)
        path = link / 'state.sqlite3'
    else:
        path = real / 'state.sqlite3'
        path.symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        await LeasedSqlite.open(path, format='test.v1', schema=SCHEMA, validate=validate)
    assert target.read_bytes() == b'preserve'


@pytest.mark.parametrize('disk_error', [False, True])
async def test_cancelled_admitted_transaction_settles_before_lease_release(tmp_path, disk_error):
    path = tmp_path / 'private' / 'state.sqlite3'
    owner = await LeasedSqlite.open(path, format='test.v1', schema=SCHEMA, validate=validate)
    loop, entered, release = asyncio.get_running_loop(), asyncio.Event(), threading.Event()
    def transaction(db):
        db.execute("INSERT INTO facts VALUES ('committed once')")
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(5):
            raise TimeoutError('Missing test transaction release')
        if disk_error:
            raise OSError('disk failure')
    write = asyncio.create_task(owner.disk(transaction))
    closer = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        write.cancel()
        await asyncio.sleep(0)
        write.cancel()
        closer = asyncio.create_task(owner.close())
        await asyncio.sleep(0)
        assert not write.done() and not closer.done()
        with pytest.raises(BlockingIOError):
            await LeasedSqlite.open(path, format='test.v1', schema=SCHEMA, validate=validate)
        release.set()
        with pytest.raises(OSError if disk_error else asyncio.CancelledError):
            await write
        await closer
        fresh = await LeasedSqlite.open(path, format='test.v1', schema=SCHEMA, validate=validate)
        try:
            values = await fresh.disk(lambda db: [row[0] for row in db.execute('SELECT value FROM facts')])
            assert values == ([] if disk_error else ['committed once'])
        finally:
            await fresh.close()
    finally:
        release.set()
        await asyncio.gather(write, return_exceptions=True)
        if closer is not None:
            await asyncio.gather(closer, return_exceptions=True)
        await owner.close()
