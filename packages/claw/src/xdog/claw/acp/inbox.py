"""Private, single-owner durable group intake; never constructs an agent."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sqlite3
import stat
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from xdog.agent.async_work import wait_owned
from xdog.agent.session_input import SessionInput, decode_input, input_data

T = TypeVar("T")


@dataclass(frozen=True)
class InboxRecord:
    group_id: str
    cwd: Path
    message: SessionInput
    status: str
    attempt_id: str = ""
    response: str = ""
    error: str = ""
    session_id: str = ""


def _row(row: sqlite3.Row) -> InboxRecord:
    return InboxRecord(row["group_id"], Path(row["cwd"]), decode_input(json.loads(row["payload"])),
                       row["status"], row["attempt_id"], row["response"], row["error"], row["session_id"])


class GroupInbox:
    """A receipt is committed before acknowledgement. Running crashes never replay.

    One lease protects both recovery and consumption, even across different socket
    paths. Transactions run off-loop and settle before releasing locks on cancel.
    """

    def __init__(self, path: Path, max_pending: int) -> None:
        self.path, self.max_pending = path, max_pending
        self._fd: int | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    @classmethod
    async def open(cls, path: Path, *, max_pending: int = 50) -> GroupInbox:
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("Invalid inbox capacity")
        store = cls(path.absolute(), max_pending)
        try:
            await store._settle(store._initialize)
            return store
        except BaseException:
            await store.close()
            raise

    async def _settle(self, operation: Callable[[], T]) -> T:
        async with self._lock:
            if self._closed:
                raise RuntimeError("Inbox is closed")
            task = asyncio.create_task(asyncio.to_thread(operation))
            return await wait_owned(task)

    def _transaction(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            return operation(db)

    async def _disk(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return await self._settle(lambda: self._transaction(operation))

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory = self.path.parent.lstat()
        if (self.path.parent.resolve() != self.path.parent or not stat.S_ISDIR(directory.st_mode)
                or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700):
            raise ValueError("Inbox directory must be private (0700), owned, and without symlinks")
        fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise RuntimeError("Inbox already has an owner") from exc
        self._fd = fd
        if self.path.exists() or self.path.is_symlink():
            info = self.path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("Inbox must be a private regular file, not a symlink")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)

        def initialize(db: sqlite3.Connection) -> None:
            db.execute("CREATE TABLE IF NOT EXISTS scopes (group_id TEXT, cwd TEXT, namespace TEXT NOT NULL, "
                       "PRIMARY KEY(group_id, cwd))")
            db.execute("CREATE TABLE IF NOT EXISTS messages (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "group_id TEXT, cwd TEXT, message_id TEXT, payload TEXT NOT NULL, status TEXT NOT NULL, "
                       "attempt_id TEXT NOT NULL DEFAULT '', response TEXT NOT NULL DEFAULT '', "
                       "error TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '', "
                       "UNIQUE(group_id, cwd, message_id))")
            from xdog.claw.core.observers.store import initialize, recover_started
            initialize(db)
            db.execute("UPDATE messages SET status='interrupted', error='Receiver stopped during execution; "
                       "inspect effects before explicitly sending a new message' WHERE status='running'")
            recover_started(db)
        self._transaction(initialize)

    async def scope(self, group_id: str, cwd: Path) -> str:
        def scope(db: sqlite3.Connection) -> str:
            db.execute("INSERT OR IGNORE INTO scopes VALUES (?, ?, ?)", (group_id, str(cwd), uuid.uuid4().hex))
            row = db.execute("SELECT namespace FROM scopes WHERE group_id=? AND cwd=?", (group_id, str(cwd))).fetchone()
            return str(row[0])
        return await self._disk(scope)

    async def enqueue(self, group_id: str, cwd: Path, message: SessionInput) -> tuple[InboxRecord, bool]:
        canonical = json.dumps(input_data(message), sort_keys=True, ensure_ascii=True, allow_nan=False)

        def enqueue(db: sqlite3.Connection) -> tuple[InboxRecord, bool]:
            existing = db.execute("SELECT * FROM messages WHERE group_id=? AND cwd=? AND message_id=?",
                                  (group_id, str(cwd), message.id)).fetchone()
            if existing is not None:
                if existing["payload"] != canonical:
                    raise ValueError("Message ID already has different content")
                return _row(existing), True
            owned = db.execute('SELECT o.*,s.enabled,s.revision AS current_revision '
                               'FROM managed_occurrences o JOIN managed_observers s ON s.observer_id=o.observer_id '
                               'WHERE o.group_id=? AND o.cwd=? AND o.message_id=?',
                               (group_id, str(cwd), message.id)).fetchone()
            status = 'pending'
            if owned is not None:
                if owned['payload'] != canonical:
                    raise ValueError('Managed occurrence content does not match the host outbox')
                if owned['cancelled'] or not owned['enabled'] or owned['revision'] != owned['current_revision']:
                    status = 'cancelled'
            active, total = db.execute("SELECT count(CASE WHEN status IN ('pending','running') THEN 1 END), count(*) "
                                      "FROM messages WHERE group_id=? AND cwd=?", (group_id, str(cwd))).fetchone()
            if status == 'pending' and active >= self.max_pending or total >= 4096:
                raise ValueError("Inbox capacity exceeded; no message was dropped")
            db.execute("INSERT INTO messages (group_id,cwd,message_id,payload,status) VALUES (?,?,?,?,?)",
                       (group_id, str(cwd), message.id, canonical, status))
            return InboxRecord(group_id, cwd, decode_input(json.loads(canonical)), status), False
        return await self._disk(enqueue)

    async def records(self, group_id: str, cwd: Path) -> tuple[InboxRecord, ...]:
        return await self._disk(lambda db: tuple(_row(row) for row in db.execute(
            "SELECT * FROM messages WHERE group_id=? AND cwd=? ORDER BY sequence", (group_id, str(cwd)))))

    async def get(self, group_id: str, cwd: Path, identifier: str) -> InboxRecord | None:
        def read(db: sqlite3.Connection) -> InboxRecord | None:
            row = db.execute("SELECT * FROM messages WHERE group_id=? AND cwd=? AND message_id=?",
                             (group_id, str(cwd), identifier)).fetchone()
            return _row(row) if row is not None else None
        return await self._disk(read)

    async def status(self, group_id: str, cwd: Path, *, selected: str | None = None,
                     cursor: int = 0, limit: int = 64) -> tuple[list[dict[str, object]], int | None]:
        def read(db: sqlite3.Connection) -> tuple[list[dict[str, object]], int | None]:
            query = ("SELECT message_id,status,attempt_id,session_id,substr(error,1,1024) AS error "
                     "FROM messages WHERE group_id=? AND cwd=?")
            args: list[object] = [group_id, str(cwd)]
            if selected is not None:
                query += " AND message_id=?"
                args.append(selected)
            rows = db.execute(query + " ORDER BY sequence LIMIT ? OFFSET ?", (*args, limit + 1, cursor)).fetchall()
            return ([{**dict(row), "branch_id": ""} for row in rows[:limit]],
                    cursor + limit if len(rows) > limit else None)
        return await self._disk(read)

    async def claim(self, group_id: str, cwd: Path) -> InboxRecord | None:
        def claim(db: sqlite3.Connection) -> InboxRecord | None:
            row = db.execute("SELECT * FROM messages WHERE group_id=? AND cwd=? AND status='pending' "
                             "ORDER BY sequence LIMIT 1", (group_id, str(cwd))).fetchone()
            if row is None:
                return None
            db.execute("UPDATE messages SET status='running', attempt_id=? WHERE sequence=?",
                       (uuid.uuid4().hex, row["sequence"]))
            return _row(db.execute("SELECT * FROM messages WHERE sequence=?", (row["sequence"],)).fetchone())
        return await self._disk(claim)

    async def finish(self, record: InboxRecord, status: str, *, response: str = "",
                     error: str = "", session_id: str = "") -> InboxRecord:
        if status not in {"succeeded", "failed", "interrupted"}:
            raise ValueError("Invalid terminal inbox state")
        if len(response.encode()) + len(error.encode()) > 1024 * 1024:
            status, response, error = "failed", "", "Agent result exceeds inbox capacity; inspect transcript"

        def finish(db: sqlite3.Connection) -> InboxRecord:
            updated = db.execute("UPDATE messages SET status=?,response=?,error=?,session_id=? "
                                 "WHERE group_id=? AND cwd=? "
                                 "AND message_id=? AND attempt_id=? AND status='running'",
                                 (status, response, error, session_id, record.group_id, str(record.cwd),
                                  record.message.id,
                                  record.attempt_id))
            if updated.rowcount != 1:
                raise ValueError("Inbox execution attempt changed")
            return _row(db.execute("SELECT * FROM messages WHERE group_id=? AND cwd=? AND message_id=?",
                                   (record.group_id, str(record.cwd), record.message.id)).fetchone())
        return await self._disk(finish)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
