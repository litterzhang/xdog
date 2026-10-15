"""Flocked durable intake; ingress can receive without becoming a session writer."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast

from xdog.coding.core.sessions.inbox_models import (
    MAX_MESSAGE_BYTES,
    MAX_RECORD_BYTES,
    RESOLVED,
    EnqueueReceipt,
    InboxAttempt,
    InboxMessage,
    InboxRecord,
    InboxState,
    encoded,
    label,
    message_data,
    message_from_data,
    record_data,
    record_from_data,
)
from xdog.coding.core.sessions.ownership import identifier, private_directory, private_open

MAX_PENDING = 128
MAX_RECORDS = 4096
MAX_SESSION_BYTES = 64 * 1024 * 1024
FOLDERS = ('pending', 'running', 'terminal')


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate inbox JSON field')
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError('Expected finite inbox JSON')


def _read(path: Path, maximum: int) -> object:
    fd = private_open(path, os.O_RDONLY)
    try:
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError('Inbox file exceeds capacity')
        try:
            return json.loads(data, object_pairs_hook=_unique, parse_constant=_constant)
        except (UnicodeError, RecursionError) as exc:
            raise ValueError('Invalid inbox JSON') from exc
    finally:
        os.close(fd)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_file(path: Path) -> None:
    fd = private_open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    _sync_directory(path.parent)


def _key(message_id: str) -> str:
    return hashlib.sha256(label(message_id).encode('utf-8')).hexdigest() + '.json'


def _folder(record: InboxRecord) -> str:
    if record.status == 'running':
        return 'running'
    return 'terminal' if record.status in RESOLVED or record.status == 'interrupted' else 'pending'


class InboxStore:
    """Owner mutation methods are trusted APIs; only the lease-owning controller uses them."""

    def __init__(self, sessions_dir: Path, root: Path) -> None:
        self.sessions_dir = sessions_dir.absolute()
        self.root = root.absolute()

    def scope_id(self, session_id: str, cwd: Path) -> str:
        """Persistent opaque dedup namespace, not a capability or a storage-path disclosure."""
        self.identity(session_id, cwd)
        root = private_directory(self.root)
        lock = private_open(root / '.store-id.lock', os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = root / '.store-id'
            if not path.exists() and not path.is_symlink():
                fd, name = tempfile.mkstemp(prefix='.identity-', dir=root)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(encoded({'version': 1, 'id': uuid.uuid4().hex}, 4096))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(name, path)
                    _sync_directory(root)
                    _sync_directory(root.parent)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
            value = _read(path, 4096)
            if (not isinstance(value, dict) or set(value) != {'version', 'id'}
                    or type(value['version']) is not int or value['version'] != 1
                    or not isinstance(value['id'], str) or re.fullmatch(r'[0-9a-f]{32}', value['id']) is None):
                raise ValueError('Invalid durable inbox namespace')
            _sync_file(path)
            return hashlib.sha256((value['id'] + '\0' + session_id).encode()).hexdigest()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)

    def identity(self, session_id: str, cwd: Path | None = None) -> tuple[Path, str]:
        session_id = identifier(session_id)
        if self.sessions_dir.resolve() != self.sessions_dir:
            raise ValueError('Session directory must not contain symbolic links')
        info = self.sessions_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError('Session identity directory is not owned or permits untrusted writes')
        data = _read(self.sessions_dir / f'session_{session_id}.json', MAX_SESSION_BYTES)
        if not isinstance(data, dict) or data.get('session_id') != session_id:
            raise ValueError('Unknown or invalid persisted session identity')
        working = Path(label(data.get('working_dir'), maximum=4096))
        if not working.is_absolute():
            raise ValueError('Session workspace must be absolute')
        working = working.resolve()
        if cwd is not None and (not isinstance(cwd, Path) or not cwd.is_absolute() or cwd.resolve() != working):
            raise ValueError('Session workspace mismatch')
        return working, label(data.get('active_branch', ''), empty=True)

    @contextmanager
    def _locked(self, session_id: str, cwd: Path | None = None) -> Iterator[tuple[Path, Path, str]]:
        # Validate before creating any intake files for an unknown session.
        self.identity(session_id, cwd)
        root = private_directory(self.root)
        directory = private_directory(root / identifier(session_id))
        for folder in FOLDERS:
            private_directory(directory / folder)
        _sync_directory(root.parent)
        _sync_directory(root)
        _sync_directory(directory)
        fd = private_open(directory / 'inbox.lock', os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            working, branch = self.identity(session_id, cwd)
            yield directory, working, branch
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _scan(self, directory: Path, working: Path) -> dict[str, tuple[Path, InboxRecord]]:
        result: dict[str, tuple[Path, InboxRecord]] = {}
        for folder in FOLDERS:
            parent = private_directory(directory / folder)
            for path in parent.iterdir():
                if path.name.startswith('.inbox-') and path.name.endswith('.tmp'):
                    continue  # A killed atomic writer cannot turn a temp file into work.
                if not re.fullmatch(r'[0-9a-f]{64}\.json', path.name):
                    raise ValueError('Unexpected inbox file')
                if len(result) >= MAX_RECORDS:
                    raise ValueError('Inbox identity capacity exceeded')
                row = record_from_data(_read(path, MAX_RECORD_BYTES))
                if row.message.cwd != working:
                    raise ValueError('Inbox record workspace mismatch')
                key = row.message.message_id
                if key in result:
                    raise ValueError('Duplicate authoritative inbox record')
                if path.name != _key(key) or row.message.session_id != directory.name:
                    raise ValueError('Inbox file/message identity mismatch')
                result[key] = path, row
        return result

    def _save(self, directory: Path, record: InboxRecord, old: Path | None = None) -> Path:
        destination = directory / _folder(record) / _key(record.message.message_id)
        current = old or destination
        data = encoded(record_data(record), MAX_RECORD_BYTES)
        fd, name = tempfile.mkstemp(prefix='.inbox-', suffix='.tmp', dir=current.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, current)
            _sync_directory(current.parent)
            if current != destination:
                if destination.exists() or destination.is_symlink():
                    raise ValueError('Duplicate inbox transition destination')
                os.replace(current, destination)
                _sync_directory(destination.parent)
                _sync_directory(current.parent)
            return destination
        finally:
            temp.unlink(missing_ok=True)

    def enqueue(self, message: InboxMessage) -> EnqueueReceipt:
        message = message_from_data(message_data(message))
        with self._locked(message.session_id, message.cwd) as (directory, working, branch):
            rows = self._scan(directory, working)
            previous = rows.get(message.message_id)
            if previous is not None:
                path, row = previous
                content = encoded(message_data(message), MAX_MESSAGE_BYTES)
                if content != encoded(message_data(row.message), MAX_MESSAGE_BYTES):
                    raise ValueError('Inbox message ID content conflict')
                _sync_file(path)
                return EnqueueReceipt(True, row)
            if len(rows) >= MAX_RECORDS or sum(r.status not in RESOLVED for _, r in rows.values()) >= MAX_PENDING:
                raise ValueError('Inbox capacity exceeded; explicit review/archive required')
            row = InboxRecord(message, branch, 'pending_review')
            self._save(directory, row)
            return EnqueueReceipt(False, row)

    def records(self, session_id: str, cwd: Path) -> tuple[InboxRecord, ...]:
        with self._locked(session_id, cwd) as (directory, working, branch):
            rows = self._scan(directory, working)
            return tuple(row for key, (path, row) in sorted(rows.items()))

    def approve(self, session_id: str, message_ids: tuple[str, ...], *, cwd: Path,
                branch_id: str, generation: str,
                expected: tuple[InboxRecord, ...] | None = None) -> tuple[InboxRecord, ...]:
        label(generation)
        label(branch_id, empty=True)
        if not message_ids or len(set(message_ids)) != len(message_ids):
            raise ValueError('Approval requires distinct explicit message IDs')
        reviewed = {r.message.message_id: r for r in expected} if expected is not None else None
        if reviewed is not None and (set(reviewed) != set(message_ids) or len(reviewed) != len(expected or ())):
            raise ValueError('Inbox reviewed selection changed')
        with self._locked(session_id, cwd) as (directory, working, branch):
            rows = self._scan(directory, working)
            if branch_id != branch:
                raise ValueError('Inbox approval branch mismatch')
            selected = []
            for key in message_ids:
                previous = rows.get(label(key))
                if previous is None:
                    raise ValueError('Unknown inbox message')
                path, row = previous
                if reviewed is not None and (
                    encoded(record_data(row), MAX_RECORD_BYTES)
                    != encoded(record_data(reviewed[key]), MAX_RECORD_BYTES)
                ):
                    raise ValueError('Inbox content/state changed after review')
                if row.branch_id != branch:
                    raise ValueError('Inbox message branch mismatch')
                if row.status not in {'pending_review', 'interrupted'}:
                    raise ValueError('Inbox message is not pending explicit review')
                attempt = InboxAttempt(uuid.uuid4().hex, uuid.uuid4().hex, generation, branch, 'approved')
                updated = replace(row, status='approved', owner_generation=generation, attempt_id=attempt.id,
                                  request_id=attempt.request_id, error='', attempts=row.attempts + (attempt,))
                encoded(record_data(updated), MAX_RECORD_BYTES)
                selected.append((path, updated))
            for path, updated in selected:
                self._save(directory, updated, path)
            return tuple(row for path, row in selected)

    def claim(self, session_id: str, message_id: str, *, generation: str, branch_id: str,
              attempt_id: str, request_id: str) -> InboxRecord:
        with self._locked(session_id) as (directory, working, branch):
            rows = self._scan(directory, working)
            path, row = self._selected(rows, message_id)
            if (row.status != 'approved' or row.owner_generation != generation or not generation
                    or row.branch_id != branch_id or branch != branch_id
                    or row.attempt_id != attempt_id or row.request_id != request_id):
                raise ValueError('Inbox claim context/attempt mismatch')
            updated = self._transition(row, 'running')
            self._save(directory, updated, path)
            return updated

    @staticmethod
    def _selected(rows: dict[str, tuple[Path, InboxRecord]], message_id: str) -> tuple[Path, InboxRecord]:
        previous = rows.get(label(message_id))
        if previous is None:
            raise ValueError('Unknown inbox message')
        return previous

    @staticmethod
    def _transition(row: InboxRecord, status: InboxState, error: str = '') -> InboxRecord:
        label(error, empty=True, maximum=4096)
        attempt_status = 'cancelled' if status == 'pending_review' else status
        attempts = row.attempts
        if row.attempt_id:
            attempts = attempts[:-1] + (replace(attempts[-1], status=attempt_status, error=error),)
        return replace(row, status=status, error=error, attempts=attempts,
                       owner_generation='' if status in {'pending_review', 'interrupted'} else row.owner_generation,
                       attempt_id='' if status == 'pending_review' else row.attempt_id,
                       request_id='' if status == 'pending_review' else row.request_id)

    def finish(self, session_id: str, message_id: str, *, generation: str, attempt_id: str,
               status: str, error: str = '') -> InboxRecord:
        if status not in RESOLVED | {'interrupted'}:
            raise ValueError('Invalid inbox attempt result')
        with self._locked(session_id) as (directory, working, branch):
            path, row = self._selected(self._scan(directory, working), message_id)
            if (not generation or row.owner_generation != generation or row.attempt_id != attempt_id
                    or row.status not in {'approved', 'running'}):
                raise ValueError('Inbox result owner/attempt mismatch')
            final = 'pending_review' if row.status == 'approved' else cast(InboxState, status)
            updated = self._transition(row, final, error)
            self._save(directory, updated, path)
            return updated

    def retire(self, session_id: str, generation: str) -> None:
        self._recover(session_id, generation, retiring=True)

    def recover(self, session_id: str, generation: str) -> tuple[InboxRecord, ...]:
        return self._recover(session_id, generation, retiring=False)

    def _recover(self, session_id: str, generation: str, *, retiring: bool) -> tuple[InboxRecord, ...]:
        label(generation)
        with self._locked(session_id) as (directory, working, branch):
            rows = self._scan(directory, working)
            result = []
            for key, (path, row) in sorted(rows.items()):
                stale = row.owner_generation == generation if retiring else row.owner_generation != generation
                if stale and row.status in {'approved', 'running'}:
                    row = self._transition(row, 'pending_review' if row.status == 'approved' else 'interrupted',
                                           'Owner retired; explicit review required')
                    self._save(directory, row, path)
                elif path.parent.name != _folder(row):
                    self._save(directory, row, path)
                result.append(row)
            return tuple(result)
