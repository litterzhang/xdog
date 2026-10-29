"""UID-private daemon journal. Serialized, settled worker IO; no domain callbacks."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from xdog.eyes.local_control import private_open

T = TypeVar('T')


class DaemonStore:
    def __init__(self, directory: Path) -> None:
        self.path = directory / 'daemon.sqlite3'
        self.lock = asyncio.Lock()

    async def run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        def execute() -> T:
            fd = private_open(self.path, os.O_CREAT | os.O_RDWR)
            os.close(fd)
            with sqlite3.connect(self.path) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA synchronous=FULL')
                return operation(db)
        async with self.lock:
            task = asyncio.create_task(asyncio.to_thread(execute))
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
            result = task.result()
            if cancelled:
                raise asyncio.CancelledError
            return result

    async def open(self) -> None:
        def schema(db: sqlite3.Connection) -> None:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version != 2 and (version != 0 or tables):
                raise ValueError(
                    f'Incompatible Eyes daemon journal version {version}; expected 2. '
                    'Old storage is not migrated or replayed; use a new daemon directory.')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS registrations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    key TEXT NOT NULL, content TEXT NOT NULL, mode TEXT NOT NULL,
                    recover INTEGER NOT NULL, state TEXT NOT NULL, checkpoint TEXT,
                    error TEXT NOT NULL DEFAULT '', UNIQUE(owner, key));
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    registration_id TEXT NOT NULL, delivery_id TEXT NOT NULL UNIQUE,
                    delivery TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS handler_bindings (
                    registration_id TEXT NOT NULL, handler_id TEXT NOT NULL, binding TEXT NOT NULL,
                    PRIMARY KEY(registration_id, handler_id));
                CREATE TABLE IF NOT EXISTS acknowledgements (
                    owner TEXT NOT NULL, sequence INTEGER NOT NULL,
                    PRIMARY KEY(owner));
                CREATE TABLE IF NOT EXISTS source_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    registration_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    event_id TEXT NOT NULL, record_key TEXT NOT NULL, digest TEXT NOT NULL,
                    payload TEXT, received_at REAL NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(registration_id, source_id, event_id));
                CREATE INDEX IF NOT EXISTS source_events_pending
                    ON source_events(registration_id, source_id, processed, sequence);
                PRAGMA user_version=2;
            ''')
        await self.run(schema)

    async def rows(self, owner: str | None = None) -> list[dict[str, Any]]:
        return await self.run(lambda db: [dict(row) for row in db.execute(
            'SELECT * FROM registrations' + (' WHERE owner=?' if owner is not None else ''),
            (owner,) if owner is not None else ())])

    async def checkpoint(self, identifier: str, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, allow_nan=False, sort_keys=True)
        def commit(db: sqlite3.Connection) -> None:
            db.execute('UPDATE registrations SET checkpoint=? WHERE id=? '
                       'AND (checkpoint IS NULL OR checkpoint != ?)', (encoded, identifier, encoded))
            # Never ACK intake before detector state and immutable output identities.
            # Retain hashes/IDs forever within the explicit finite ledger bound.
            for source_id, sequence in value.get('event_sources', {}).items():
                db.execute('UPDATE source_events SET processed=1, payload=NULL '
                    'WHERE registration_id=? AND source_id=? AND sequence<=? AND processed=0',
                    (identifier, source_id, sequence))
        await self.run(commit)
