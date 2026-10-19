"""Private leased SQLite IO, without application schema, recovery or model policy."""
from __future__ import annotations

import asyncio
import fcntl
import os
import sqlite3
import stat
import tempfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import TypeVar

from xdog.agent.async_work import wait_owned

T = TypeVar('T')


def _directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    owner = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            try:
                os.mkdir(part, 0o700, dir_fd=owner)
                os.fsync(owner)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=owner)
            os.close(owner)
            owner = child
        info = os.fstat(owner)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Journal directory must be private and owned (0700)')
    finally:
        os.close(owner)


class LeasedSqlite:
    """Own only IO/lease settlement. A domain supplies its schema and validations.

    Validation always uses a read-only connection before any recovery transaction.
    Unknown versions are refused, never upgraded. No background daemon or worker
    loop is started; individual disk operations use the existing owned-work helper.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._fd: int | None = None
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    async def open(cls, path: Path, *, format: str, schema: str,
                   validate: Callable[[sqlite3.Connection], None],
                   recover: Callable[[sqlite3.Connection], None] | None = None) -> LeasedSqlite:
        path = path.absolute()
        if '..' in path.parts or '\x00' in str(path):
            raise ValueError('Invalid journal path')
        owner = cls(path)
        try:
            await owner.run(lambda: owner._initialize(format, schema, validate, recover))
            return owner
        except BaseException:
            await owner.close()
            raise

    async def run(self, operation: Callable[[], T]) -> T:
        if self._closing:
            raise RuntimeError('Journal is closing')
        async with self._lock:
            if self._closed:
                raise RuntimeError('Journal is closed')
            return await wait_owned(asyncio.create_task(asyncio.to_thread(operation)))

    def transaction(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                db.execute('BEGIN IMMEDIATE')
                return operation(db)

    async def disk(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return await self.run(lambda: self.transaction(operation))

    def _initialize(self, format: str, schema: str, validate: Callable[[sqlite3.Connection], None],
                    recover: Callable[[sqlite3.Connection], None] | None) -> None:
        _directory(self.path.parent)
        fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('Journal lease must be private, regular and owned')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            self._create(format, schema)
        else:
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('Journal must be private, regular and owned')
        try:
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('SELECT format FROM metadata').fetchall() != [(format,)]:
                    raise ValueError('Unsupported journal format; no upgrade is performed')
                if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise ValueError('Journal integrity check failed')
                db.row_factory = sqlite3.Row
                validate(db)
        except sqlite3.DatabaseError as exc:
            raise ValueError('Unsupported or corrupt journal; original data retained') from exc
        if recover is not None:
            self.transaction(recover)

    def _create(self, format: str, schema: str) -> None:
        fd, name = tempfile.mkstemp(prefix='.journal-', dir=self.path.parent)
        os.close(fd)
        try:
            with closing(sqlite3.connect(name)) as db, db:
                db.executescript('BEGIN IMMEDIATE; CREATE TABLE metadata(format TEXT NOT NULL);\n' + schema)
                db.execute('INSERT INTO metadata VALUES (?)', (format,))
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            # Atomic exclusive installation; a failed schema never poisons the
            # final path, and no existing user's file can be overwritten.
            os.link(name, self.path, follow_symlinks=False)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.unlink(name)

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
