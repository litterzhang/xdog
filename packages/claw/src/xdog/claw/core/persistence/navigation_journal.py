"""Durable undo journal for managed navigation files; not an OS sandbox or database."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

JOURNAL = '.navigation-journal.json'
_BACKUP = re.compile(r'\.navigation-backup-[a-f0-9]{32}-[0-9]{1,3}\.bak')
_TARGET = re.compile(
    r'[A-Za-z0-9][A-Za-z0-9_.-]{0,269}\.(?:jsonl|plan\.json|access\.json|extensions\.json|tools\.json|model\.json|thinking\.json|skills\.json)')


class NavigationRecoveryError(RuntimeError):
    """Unfinished state must be recovered before the store may expose it."""


def sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def ownership(directory: Path) -> Iterator[None]:
    path = directory / '.navigation.lock'
    if path.is_symlink():
        raise NavigationRecoveryError('Navigation recovery lock is not owned')
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        import fcntl
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise NavigationRecoveryError('Another process owns navigation persistence') from None
        yield
    finally:
        os.close(descriptor)


def digest(path: Path) -> tuple[int, str]:
    if path.is_symlink() or not path.is_file():
        raise NavigationRecoveryError('Navigation backup unavailable or not owned')
    checksum = hashlib.sha256()
    size = 0
    with path.open('rb') as source:
        while data := source.read(1024 * 1024):
            size += len(data)
            checksum.update(data)
    return size, checksum.hexdigest()


def write_manifest(directory: Path, value: dict[str, Any]) -> None:
    path = directory / JOURNAL
    if path.is_symlink():
        raise NavigationRecoveryError('Navigation journal is not owned')
    descriptor, temporary = tempfile.mkstemp(prefix='.navigation-manifest-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(value, output, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_manifest(directory: Path) -> dict[str, Any] | None:
    path = directory / JOURNAL
    if path.is_symlink():
        raise NavigationRecoveryError('Navigation journal is not owned')
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 65536:
        raise NavigationRecoveryError('Invalid navigation journal size/type')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or set(value) != {'version', 'id', 'phase', 'files'}
                or type(value['version']) is not int or value['version'] != 1
                or not isinstance(value['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', value['id'])
                or value['phase'] not in ('prepared', 'committed')
                or not isinstance(value['files'], list) or not 1 <= len(value['files']) <= 128):
            raise ValueError('Invalid journal fields')
        names = set()
        backup_names = set()
        for row in value['files']:
            if (not isinstance(row, dict) or set(row) != {'name', 'backup', 'bytes', 'sha256', 'mode'}
                    or not isinstance(row['name'], str) or row['name'] in names
                    or row['name'] not in {'catalog.json', 'sessions.json'} and not _TARGET.fullmatch(row['name'])
                    or type(row['bytes']) is not int or row['bytes'] < 0
                    or type(row['mode']) is not int or not 0 <= row['mode'] <= 0o777):
                raise ValueError('Invalid journal destination')
            names.add(row['name'])
            if row['backup'] is None:
                if row['bytes'] != 0 or row['sha256'] != '' or row['mode'] != 0:
                    raise ValueError('Invalid absent-original journal record')
            elif (not isinstance(row['backup'], str) or not _BACKUP.fullmatch(row['backup'])
                    or not row['backup'].startswith('.navigation-backup-' + value['id'] + '-')
                    or not isinstance(row['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])):
                raise ValueError('Invalid journal backup')
            if row['backup'] is not None:
                if row['backup'] in backup_names:
                    raise ValueError('Repeated navigation backup identity')
                backup_names.add(row['backup'])
            destination = directory / row['name']
            if (destination.exists() and not destination.is_file() or destination.is_symlink()
                    or row['backup'] is not None and (directory / row['backup']).is_symlink()):
                raise ValueError('Journal destination/backup is not owned')
        return value
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        raise NavigationRecoveryError('Invalid navigation journal; '
                                      'repair recovery state before opening sessions') from exc


def cleanup(directory: Path, value: dict[str, Any]) -> None:
    # Remove the manifest FIRST and persist its removal. A committed marker or
    # completed rollback never points to backups already removed by cleanup.
    (directory / JOURNAL).unlink(missing_ok=True)
    sync_directory(directory)
    for row in value['files']:
        if row['backup'] is not None:
            (directory / row['backup']).unlink(missing_ok=True)
    sync_directory(directory)


def recover_locked(directory: Path) -> bool:
    value = load_manifest(directory)
    if value is None:
        return False
    if value['phase'] == 'prepared':
        # Validate EVERY backup before changing ANY target. Keep originals of
        # backups throughout replay, making interrupted recovery idempotent.
        for row in value['files']:
            if row['backup'] is not None and digest(directory / row['backup']) != (row['bytes'], row['sha256']):
                raise NavigationRecoveryError('Navigation backup checksum mismatch; no recovery applied')
        for row in value['files']:
            destination = directory / row['name']
            if row['backup'] is None:
                destination.unlink(missing_ok=True)
                continue
            descriptor, temporary = tempfile.mkstemp(prefix='.navigation-restore-', dir=directory)
            try:
                with os.fdopen(descriptor, 'wb') as output, (directory / row['backup']).open('rb') as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                    os.fchmod(output.fileno(), row['mode'])
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        sync_directory(directory)
    cleanup(directory, value)
    return True


def recover(directory: Path) -> None:
    with ownership(directory):
        recover_locked(directory)
        # Preparation may have died before its manifest became durable. These
        # private unreferenced temporary files cannot represent changed targets.
        for pattern in ('.navigation-backup-*.bak', '.navigation-manifest-*', '.navigation-restore-*'):
            for path in directory.glob(pattern):
                if path.is_symlink() or not path.is_file():
                    raise NavigationRecoveryError('Unowned navigation temporary artifact')
                path.unlink()
        sync_directory(directory)


class NavigationJournal:
    def __init__(self, directory: Path, paths: set[Path]) -> None:
        self.directory = directory
        self.value: dict[str, Any] = {'version': 1, 'id': uuid.uuid4().hex, 'phase': 'prepared', 'files': []}
        self.durable = False
        if (directory / JOURNAL).exists() or (directory / JOURNAL).is_symlink():
            raise NavigationRecoveryError('Unfinished navigation journal; reopen store to recover')
        try:
            for index, path in enumerate(sorted(paths)):
                if path.parent != directory or path.is_symlink():
                    raise NavigationRecoveryError('Navigation destination is not owned')
                row: dict[str, Any] = {'name': path.name, 'backup': None, 'bytes': 0, 'sha256': '', 'mode': 0}
                self.value['files'].append(row)
                if not path.exists():
                    continue
                backup = f".navigation-backup-{self.value['id']}-{index}.bak"
                row['backup'] = backup
                row['mode'] = stat.S_IMODE(path.stat().st_mode)
                descriptor = os.open(directory / backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, 'wb') as output, path.open('rb') as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                row['bytes'], row['sha256'] = digest(directory / backup)
            write_manifest(directory, self.value)
            self.durable = True
        except BaseException:
            # A published manifest must retain its complete recovery files.
            if (directory / JOURNAL).exists():
                self.durable = True
            else:
                for row in self.value['files']:
                    if row['backup'] is not None:
                        (directory / row['backup']).unlink(missing_ok=True)
            raise

    def commit(self) -> None:
        sync_directory(self.directory)  # All managed replacement names before the commit marker.
        self.value = {**self.value, 'phase': 'committed'}
        write_manifest(self.directory, self.value)
        try:
            cleanup(self.directory, self.value)
        except OSError:
            # The complete new state and commit marker are durable. Cleanup is
            # restart-recoverable housekeeping, not failure/rollback of that commit.
            pass

    def rollback(self) -> None:
        # A known error while attempting the commit marker must undo rather than
        # interpret a newly renamed but unacknowledged marker as success.
        self.value = {**self.value, 'phase': 'prepared'}
        write_manifest(self.directory, self.value)
        recover_locked(self.directory)
