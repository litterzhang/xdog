"""Single-owner application intake and attempt facts, with no protocol/business policy."""
from __future__ import annotations

import asyncio
import fcntl
import os
import sqlite3
import stat
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, cast

from xdog.agent.async_work import wait_owned
from xdog.claw.core.inputs.models import InputEnvelope, InputScope, decode_envelope, encode_envelope

T = TypeVar('T')
FORMAT = 'xdog.claw.inbox.v1'
_INTERRUPTED = 'Execution interrupted; inspect prior effects before explicit retry'
_STATES = frozenset({'pending', 'claimed', 'running', 'succeeded', 'failed', 'interrupted', 'cancelled'})
_SCHEMA = '''
CREATE TABLE metadata (format TEXT NOT NULL);
CREATE TABLE inputs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT NOT NULL, workspace TEXT NOT NULL,
    message_id TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
    attempt_id TEXT NOT NULL DEFAULT '', response TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '', UNIQUE(group_id, workspace, message_id));
CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL REFERENCES inputs(sequence), status TEXT NOT NULL,
    response TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '');
'''


@dataclass(frozen=True)
class InputRecord:
    envelope: InputEnvelope
    status: str
    attempt_id: str = ''
    response: str = ''
    error: str = ''
    session_id: str = ''


@dataclass(frozen=True)
class InputAttempt:
    attempt_id: str
    status: str
    response: str
    error: str
    session_id: str


def _record(row: sqlite3.Row) -> InputRecord:
    if row['status'] not in _STATES:
        raise ValueError('Unknown input receipt state')
    envelope = decode_envelope(row['payload'])
    if (envelope.scope.group_id != row['group_id'] or str(envelope.scope.workspace) != row['workspace']
            or envelope.message.id != row['message_id']):
        raise ValueError('Input record identity does not match its envelope')
    return InputRecord(envelope, row['status'], row['attempt_id'], row['response'], row['error'], row['session_id'])


def _select(db: sqlite3.Connection, scope: InputScope, identifier: str) -> sqlite3.Row:
    row = db.execute('SELECT * FROM inputs WHERE group_id=? AND workspace=? AND message_id=?',
                     (scope.group_id, str(scope.workspace), identifier)).fetchone()
    if row is None:
        raise ValueError('Unknown input in this scope')
    return cast(sqlite3.Row, row)



def _validate_receipts(db: sqlite3.Connection) -> None:
    """Read-only validation must precede every recovery write, not repair facts."""
    db.row_factory = sqlite3.Row
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('Input attempt refers to a missing receipt')
    for row in db.execute('SELECT * FROM inputs ORDER BY sequence'):
        record = _record(row)
        attempts = db.execute('SELECT * FROM attempts WHERE sequence=? ORDER BY rowid',
                              (row['sequence'],)).fetchall()
        if any(attempt['status'] not in (_STATES - {'pending'}) | {'released'} for attempt in attempts):
            raise ValueError('Unknown execution attempt state')
        if any(attempt['status'] in {'claimed', 'running'} for attempt in attempts
               if attempt['attempt_id'] != record.attempt_id):
            raise ValueError('A noncurrent execution attempt remains active')
        if record.attempt_id:
            if (not attempts or attempts[-1]['attempt_id'] != record.attempt_id
                    or attempts[-1]['status'] != record.status
                    or any(attempts[-1][field] != row[field] for field in ('response', 'error', 'session_id'))):
                raise ValueError('Current input and execution attempt facts disagree')
        elif record.status not in {'pending', 'cancelled'}:
            raise ValueError('Started input is missing its execution attempt')
        if record.status == 'pending' and (record.attempt_id or record.response or record.error or record.session_id):
            raise ValueError('Pending input contains execution facts')
    duplicate = db.execute("SELECT 1 FROM inputs WHERE status IN ('claimed','running') "
                           'GROUP BY group_id,workspace HAVING count(*)>1').fetchone()
    if duplicate is not None:
        raise ValueError('Input scope has multiple active execution attempts')


def _directory(path: Path) -> None:
    """Create/open components without following symlinks; do not repair permissions."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open('/', flags)
    try:
        for component in path.parts[1:]:
            try:
                os.mkdir(component, 0o700, dir_fd=directory)
                os.fsync(directory)
            except FileExistsError:
                pass
            child = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Input directory must be private (0700) and owned')
    finally:
        os.close(directory)


class InputStore:
    """Durable ACK precedes execution. Every disk operation settles before unlock.

    This stores only core inputs and execution attempts. Goal/observer lifecycle
    and tables belong to their respective capabilities, not this receipt journal.
    """

    def __init__(self, path: Path, max_pending: int) -> None:
        self.path, self.max_pending = path, max_pending
        self._lock = asyncio.Lock()
        self._fd: int | None = None
        self._closed = False
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    async def open(cls, path: Path, *, max_pending: int = 50) -> InputStore:
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError('Invalid input capacity')
        path = path.absolute()
        if '..' in path.parts:
            raise ValueError('Input database path cannot traverse parent directories')
        store = cls(path, max_pending)
        try:
            await store._work(store._initialize)
            return store
        except BaseException:
            await store.close()
            raise

    async def _work(self, operation: Callable[[], T]) -> T:
        if self._closing:
            raise RuntimeError('Input store is closing')
        async with self._lock:
            if self._closed:
                raise RuntimeError('Input store is closed')
            # Cancellation cannot release this lease while its thread is still
            # committing. wait_owned reports the actual disk error first.
            return await wait_owned(asyncio.create_task(asyncio.to_thread(operation)))

    def _transaction(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                db.execute('BEGIN IMMEDIATE')
                return operation(db)

    async def _disk(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return await self._work(lambda: self._transaction(operation))

    def _initialize(self) -> None:
        _directory(self.path.parent)
        lock = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('Input lease must be private, regular and owned')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(lock)
            raise
        self._fd = lock
        created = False
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('Input database must be private, regular and owned')
        else:
            os.close(fd)
            created = True
        if created:
            with closing(sqlite3.connect(self.path)) as db, db:
                # executescript owns its own BEGIN; no implicit partial schema
                # upgrade is ever attempted for an existing file.
                db.executescript('BEGIN IMMEDIATE;\n' + _SCHEMA)
                db.execute('INSERT INTO metadata VALUES (?)', (FORMAT,))
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        else:
            try:
                with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as db:
                    metadata = db.execute('SELECT format FROM metadata').fetchall()
                    if metadata != [(FORMAT,)]:
                        raise ValueError('Unsupported input database format; no upgrade is performed')
                    if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
                        raise ValueError('Input database integrity check failed')
                    _validate_receipts(db)
            except sqlite3.DatabaseError as exc:
                raise ValueError('Unsupported or corrupt input database; original data retained') from exc

        def recover(db: sqlite3.Connection) -> None:
            db.execute("UPDATE attempts SET status='released' WHERE status='claimed'")
            db.execute("UPDATE inputs SET status='pending',attempt_id='' WHERE status='claimed'")
            db.execute("UPDATE attempts SET status='interrupted',error=? WHERE status='running'", (_INTERRUPTED,))
            db.execute("UPDATE inputs SET status='interrupted',error=? WHERE status='running'", (_INTERRUPTED,))
        self._transaction(recover)

    @staticmethod
    def _capacity(db: sqlite3.Connection, scope: InputScope, maximum: int) -> None:
        count = db.execute("SELECT count(*) FROM inputs WHERE group_id=? AND workspace=? "
                           "AND status IN ('pending','claimed','running')",
                           (scope.group_id, str(scope.workspace))).fetchone()[0]
        if count >= maximum:
            raise ValueError('Input capacity exceeded; no accepted message was dropped')

    async def enqueue(self, envelope: InputEnvelope) -> tuple[InputRecord, bool]:
        def enqueue(db: sqlite3.Connection) -> tuple[InputRecord, bool]:
            payload = encode_envelope(envelope)
            scope, identifier = envelope.scope, envelope.message.id
            row = db.execute('SELECT * FROM inputs WHERE group_id=? AND workspace=? AND message_id=?',
                             (scope.group_id, str(scope.workspace), identifier)).fetchone()
            if row is not None:
                if row['payload'] != payload:
                    raise ValueError('Message ID already has different content or host routing')
                return _record(row), True
            self._capacity(db, scope, self.max_pending)
            db.execute("INSERT INTO inputs(group_id,workspace,message_id,payload,status) VALUES (?,?,?,?,'pending')",
                       (scope.group_id, str(scope.workspace), identifier, payload))
            return _record(_select(db, scope, identifier)), False
        return await self._disk(enqueue)

    async def get(self, scope: InputScope, message_id: str) -> InputRecord | None:
        def get(db: sqlite3.Connection) -> InputRecord | None:
            row = db.execute('SELECT * FROM inputs WHERE group_id=? AND workspace=? AND message_id=?',
                             (scope.group_id, str(scope.workspace), message_id)).fetchone()
            return _record(row) if row is not None else None
        return await self._disk(get)

    async def records(self, scope: InputScope) -> tuple[InputRecord, ...]:
        return await self._disk(lambda db: tuple(_record(row) for row in db.execute(
            'SELECT * FROM inputs WHERE group_id=? AND workspace=? ORDER BY sequence',
            (scope.group_id, str(scope.workspace)))))

    async def attempts(self, scope: InputScope, message_id: str) -> tuple[InputAttempt, ...]:
        def read(db: sqlite3.Connection) -> tuple[InputAttempt, ...]:
            row = _select(db, scope, message_id)
            return tuple(InputAttempt(attempt['attempt_id'], attempt['status'], attempt['response'],
                                      attempt['error'], attempt['session_id']) for attempt in db.execute(
                'SELECT * FROM attempts WHERE sequence=? ORDER BY rowid', (row['sequence'],)))
        return await self._disk(read)

    async def claim(self, scope: InputScope) -> InputRecord | None:
        def claim(db: sqlite3.Connection) -> InputRecord | None:
            if db.execute("SELECT 1 FROM inputs WHERE group_id=? AND workspace=? AND status IN ('claimed','running')",
                          (scope.group_id, str(scope.workspace))).fetchone() is not None:
                return None
            row = db.execute("SELECT * FROM inputs WHERE group_id=? AND workspace=? AND status='pending' "
                             'ORDER BY sequence LIMIT 1', (scope.group_id, str(scope.workspace))).fetchone()
            if row is None:
                return None
            attempt = uuid.uuid4().hex
            db.execute("INSERT INTO attempts(attempt_id,sequence,status) VALUES (?,?,'claimed')",
                       (attempt, row['sequence']))
            db.execute("UPDATE inputs SET status='claimed',attempt_id=? WHERE sequence=?", (attempt, row['sequence']))
            return _record(_select(db, scope, row['message_id']))
        return await self._disk(claim)

    @staticmethod
    def _attempt(db: sqlite3.Connection, record: InputRecord, status: str) -> sqlite3.Row:
        row = _select(db, record.envelope.scope, record.envelope.message.id)
        if not record.attempt_id or row['attempt_id'] != record.attempt_id or row['status'] != status:
            raise ValueError('Input execution attempt changed or is not in the required state')
        return row

    async def start(self, record: InputRecord, *, session_id: str = '') -> InputRecord:
        def start(db: sqlite3.Connection) -> InputRecord:
            row = self._attempt(db, record, 'claimed')
            db.execute("UPDATE inputs SET status='running',session_id=? WHERE sequence=?",
                       (session_id, row['sequence']))
            db.execute("UPDATE attempts SET status='running',session_id=? WHERE attempt_id=?",
                       (session_id, record.attempt_id))
            return _record(_select(db, record.envelope.scope, record.envelope.message.id))
        return await self._disk(start)

    async def finish(self, record: InputRecord, status: str, *, response: str = '', error: str = '',
                     session_id: str = '') -> InputRecord:
        if status not in {'succeeded', 'failed', 'interrupted'}:
            raise ValueError('Invalid terminal input state')
        if len(response.encode()) + len(error.encode()) > 1024 * 1024:
            raise ValueError('Execution result exceeds receipt capacity; inspect transcript')
        def finish(db: sqlite3.Connection) -> InputRecord:
            row = self._attempt(db, record, 'running')
            session = session_id or row['session_id']
            db.execute('UPDATE inputs SET status=?,response=?,error=?,session_id=? WHERE sequence=?',
                       (status, response, error, session, row['sequence']))
            db.execute('UPDATE attempts SET status=?,response=?,error=?,session_id=? WHERE attempt_id=?',
                       (status, response, error, session, record.attempt_id))
            return _record(_select(db, record.envelope.scope, record.envelope.message.id))
        return await self._disk(finish)

    async def cancel(self, scope: InputScope, message_id: str) -> InputRecord:
        def cancel(db: sqlite3.Connection) -> InputRecord:
            row = _select(db, scope, message_id)
            if row['status'] == 'cancelled':
                return _record(row)
            if row['status'] not in {'pending', 'claimed'}:
                raise ValueError('Only unstarted input can be withdrawn; running work needs its execution owner')
            db.execute("UPDATE inputs SET status='cancelled' WHERE sequence=?", (row['sequence'],))
            if row['attempt_id']:
                db.execute("UPDATE attempts SET status='cancelled' WHERE attempt_id=?", (row['attempt_id'],))
            return _record(_select(db, scope, message_id))
        return await self._disk(cancel)

    async def release(self, record: InputRecord) -> InputRecord:
        """Return only this unstarted claim; never replay a running attempt."""
        def release(db: sqlite3.Connection) -> InputRecord:
            row = self._attempt(db, record, 'claimed')
            db.execute("UPDATE attempts SET status='released' WHERE attempt_id=?", (record.attempt_id,))
            db.execute("UPDATE inputs SET status='pending',attempt_id='' WHERE sequence=?", (row['sequence'],))
            return _record(_select(db, record.envelope.scope, record.envelope.message.id))
        return await self._disk(release)

    async def reject(self, record: InputRecord, error: str) -> InputRecord:
        """Persist a failed preparation without pretending Agent admission occurred."""
        if not isinstance(error, str) or not error.strip() or len(error.encode()) > 1024 * 1024:
            raise ValueError('Preparation failure requires a bounded reason')
        def reject(db: sqlite3.Connection) -> InputRecord:
            row = self._attempt(db, record, 'claimed')
            db.execute("UPDATE inputs SET status='failed',error=? WHERE sequence=?", (error, row['sequence']))
            db.execute("UPDATE attempts SET status='failed',error=? WHERE attempt_id=?", (error, record.attempt_id))
            return _record(_select(db, record.envelope.scope, record.envelope.message.id))
        return await self._disk(reject)

    async def retry(self, scope: InputScope, message_id: str) -> InputRecord:
        def retry(db: sqlite3.Connection) -> InputRecord:
            row = _select(db, scope, message_id)
            if row['status'] not in {'failed', 'interrupted'}:
                raise ValueError('Only failed or interrupted execution can be explicitly retried')
            self._capacity(db, scope, self.max_pending)
            db.execute("UPDATE inputs SET status='pending',attempt_id='',response='',error='',session_id='' "
                       'WHERE sequence=?', (row['sequence'],))
            return _record(_select(db, scope, message_id))
        return await self._disk(retry)

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        async with self._lock:
            if self._fd is not None:
                fd, self._fd = self._fd, None
                await wait_owned(asyncio.create_task(asyncio.to_thread(os.close, fd)))
            self._closed = True
