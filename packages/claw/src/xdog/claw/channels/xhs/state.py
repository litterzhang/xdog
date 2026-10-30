"""Connector scan/dedup facts only. Core Inbox remains the execution authority."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class ScanState:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._open() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS baseline (account TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS gaps (account TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS seen (
                    account TEXT NOT NULL, comment TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(account,comment));
            ''')

    def _open(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def snapshot(self, account: str, comments: tuple[str, ...]) -> tuple[bool, dict[str, str]]:
        with self._open() as db:
            initialized = db.execute('SELECT 1 FROM baseline WHERE account=?', (account,)).fetchone() is not None
            if len(comments) > 200:
                raise ValueError('XHS scan exceeds bounded page capacity')
            marks = ','.join('?' for _ in comments)
            return initialized, dict(db.execute(
                'SELECT comment,payload FROM seen WHERE account=? AND comment IN (' + marks + ')',
                (account, *comments)))

    def gap(self, account: str, *, found: bool = False) -> bool:
        with self._open() as db:
            if found:
                db.execute('INSERT OR IGNORE INTO gaps VALUES (?)', (account,))
            return db.execute('SELECT 1 FROM gaps WHERE account=?', (account,)).fetchone() is not None

    def accept(self, account: str, items: list[tuple[str, dict[str, object]]], *, baseline: bool = False) -> None:
        with self._open() as db:
            for comment, payload in items:
                encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
                previous = db.execute('SELECT payload FROM seen WHERE account=? AND comment=?',
                                      (account, comment)).fetchone()
                if previous is not None and previous[0] != encoded:
                    raise ValueError('Previously observed XHS comment changed; no automatic second reply')
                db.execute('INSERT OR IGNORE INTO seen VALUES (?,?,?)', (account, comment, encoded))
            if baseline:
                db.execute('INSERT OR IGNORE INTO baseline VALUES (?)', (account,))
