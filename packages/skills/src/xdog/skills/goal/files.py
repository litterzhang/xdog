"""Authoritative task files and single-file Markdown reports; bounded atomic Linux IO."""
from __future__ import annotations

import fcntl
import html
import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from .codec import decode_task, encode_task, json_text, read_json
from .models import Task, identity, text

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_MAX_FILE = 262144
_REPORT_ARRAYS = ('completed', 'evidence', 'blockers', 'next_steps')
_REPORT_FIELDS = {'expected_version', 'progress', 'summary', *_REPORT_ARRAYS}


def _report_data(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != _REPORT_FIELDS:
        raise ValueError('Report requires exactly expected_version, progress, summary and four text arrays')
    expected = data['expected_version']
    if type(expected) is not int or not 0 <= expected < 2**53 - 1:
        raise ValueError('Report expected_version must be a nonnegative integer')
    progress = data['progress']
    if not isinstance(progress, str) or progress not in {'baseline', 'advanced', 'unchanged', 'regressed'}:
        raise ValueError('Invalid report progress')
    if (expected == 0) != (progress == 'baseline'):
        raise ValueError('Only the first report must be baseline')
    result = {**data, 'summary': text(data['summary'])}
    total = len(result['summary'].encode('utf-8'))
    for key in _REPORT_ARRAYS:
        if not isinstance(data[key], list) or len(data[key]) > 1024:
            raise ValueError('Report collections must be bounded arrays of strings')
        result[key] = []
        for item in data[key]:
            value = text(item)
            total += len(value.encode('utf-8'))
            if total > _MAX_FILE:
                raise ValueError('Report exceeds bounded capacity; no history was dropped')
            result[key].append(value)
    return result


def _markdown_text(value: str) -> str:
    # Text cannot inject report fences/headings; the JSON block is authoritative.
    return html.escape(value).replace('`', '&#96;').replace('\n', '<br>')


def _report_document(goal_id: str, rows: list[dict[str, Any]]) -> str:
    parts = [f'# Goal progress\n\nGoal: `{goal_id}`\n\n']
    for row in rows:
        parts.append(f"## Report {row['version']}: {row['progress']}\n\n")
        parts.append(_markdown_text(row['summary']) + '\n\n')
        for key in _REPORT_ARRAYS:
            parts.append(f"### {key.replace('_', ' ').title()}\n\n")
            parts.extend('- ' + _markdown_text(item) + '\n' for item in row[key])
            parts.append('\n')
        payload = json_text(row).replace('`', r'\u0060')
        parts.append('```json goal-report-v1\n' + payload + '\n```\n\n')
    return ''.join(parts)


def _report_rows(payload: str, goal_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in re.finditer(r'^```json goal-report-v1\n([^\n]+)\n```\n', payload, re.MULTILINE):
        row = read_json(match[1])
        if set(row) != (_REPORT_FIELDS - {'expected_version'}) | {'version'}:
            raise ValueError('Invalid saved report fields')
        if type(row['version']) is not int or row['version'] != len(rows) + 1:
            raise ValueError('Invalid saved report version sequence')
        _report_data({key: value for key, value in row.items() if key != 'version'} |
                     {'expected_version': len(rows)})
        rows.append(row)
    if not rows or payload != _report_document(goal_id, rows):
        raise ValueError('Damaged or foreign Goal progress document; refusing recovery')
    return rows


@contextmanager
def _locked(fd: int, name: str) -> Iterator[None]:
    lock = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                   0o600, dir_fd=fd)
    try:
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            raise ValueError('Expected a regular Goal write lock')
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
    finally:
        os.close(lock)


@contextmanager
def directory(path: Path, *, create: bool = False) -> Iterator[int]:
    if not path.is_absolute() or '..' in path.parts or '\x00' in str(path):
        raise ValueError('Goal workspace path must be absolute and confined')
    fd = os.open('/', _DIR)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read(fd: int, name: str, maximum: int = _MAX_FILE) -> str:
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    try:
        before = os.fstat(child)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError('Goal file must be a bounded regular file')
        with os.fdopen(os.dup(child), 'rb') as stream:
            payload = stream.read(maximum + 1)
        after = os.fstat(child)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(payload) > maximum:
            raise ValueError('Goal file changed during read; retry from a fresh snapshot')
        return payload.decode('utf-8')
    finally:
        os.close(child)


def _write(fd: int, name: str, payload: str) -> None:
    data = payload.encode('utf-8')
    if len(data) > _MAX_FILE:
        raise ValueError('Goal file exceeds bounded capacity')
    temporary = '.write-' + uuid.uuid4().hex
    child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(child, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            existing = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(existing.st_mode):
                raise ValueError('Cannot replace a non-regular Goal file')
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


class TaskFiles:
    def __init__(self, workspace: Path, goal_id: str) -> None:
        if not workspace.is_absolute() or '..' in workspace.parts or '\x00' in str(workspace):
            raise ValueError('Goal workspace path must be absolute and confined')
        self.root = workspace / '.xdog' / 'goals' / identity(goal_id)
        self.tasks_dir = self.root / 'tasks'

    @property
    def progress_path(self) -> Path:
        return self.root / 'progress.md'

    def latest_report(self) -> dict[str, Any] | None:
        """Read the last assessment, validating the complete original history."""
        # Missing is distinct from an empty, truncated, malformed or unsafe file.
        try:
            with directory(self.root) as fd:
                payload = _read(fd, 'progress.md')
        except FileNotFoundError:
            return None
        return _report_rows(payload, self.root.name)[-1]

    def report(self, data: dict[str, Any]) -> dict[str, Any]:
        """Append a caller-judged assessment with CAS; never infer Goal state.

        Input has expected_version (0 for baseline), progress, summary and
        completed/evidence/blockers/next_steps text arrays. Output replaces
        expected_version with the newly committed report version. Only
        progress.md contains report data; the stable lock file is not state.
        Callers own permission, current-owner checks and async offloading.
        """
        validated = _report_data(data)
        with directory(self.root, create=True) as fd, _locked(fd, '.progress-write.lock'):
            try:
                rows = _report_rows(_read(fd, 'progress.md'), self.root.name)
            except FileNotFoundError:
                rows = []
            if validated['expected_version'] != len(rows):
                raise ValueError('Report version conflict; re-read before updating')
            row = {key: value for key, value in validated.items() if key != 'expected_version'}
            row['version'] = len(rows) + 1
            _write(fd, 'progress.md', _report_document(self.root.name, [*rows, row]))
        return row

    def initialize(self) -> None:
        with directory(self.tasks_dir, create=True):
            pass

    def list(self) -> tuple[Task, ...]:
        with directory(self.tasks_dir) as fd:
            names = sorted(name for name in os.listdir(fd) if name.endswith('.json'))
            if len(names) > 10000:
                raise ValueError('Task workspace exceeds current scan capacity; nothing was truncated')
            rows = []
            total = 0
            for name in names:
                identifier = identity(name[:-5])
                payload = _read(fd, name)
                total += len(payload.encode())
                if total > 64 * 1024 * 1024:
                    raise ValueError('Task workspace exceeds scan bytes; nothing was truncated')
                task = decode_task(payload)
                if task.id != identifier:
                    raise ValueError('Task filename and identity disagree')
                rows.append(task)
            return tuple(rows)

    def write(self, task: Task, *, expected_version: int) -> Task:
        if type(expected_version) is not int or expected_version < 0 or task.version != expected_version + 1:
            raise ValueError('Task version conflict; re-read before updating')
        self.initialize()
        with directory(self.root) as root:
            with _locked(root, '.task-write.lock'):
                child = os.open('tasks', _DIR, dir_fd=root)
                try:
                    name = task.id + '.json'
                    try:
                        current = decode_task(_read(child, name))
                    except FileNotFoundError:
                        if expected_version != 0:
                            raise ValueError('Task no longer exists') from None
                    else:
                        if current.id != task.id or current.version != expected_version:
                            raise ValueError('Task version conflict; re-read before updating')
                    _write(child, name, encode_task(task))
                finally:
                    os.close(child)
        return task

    def plan(self) -> str:
        with directory(self.root) as fd:
            try:
                return _read(fd, 'plan.md', 65536)
            except FileNotFoundError:
                return ''

    def write_plan(self, content: str) -> None:
        text(content, empty=True)
        self.initialize()
        with directory(self.root) as fd:
            _write(fd, 'plan.md', content)

    def update(self, identifier: str, expected: int, changes: dict[str, Any]) -> Task:
        identity(identifier)
        if type(expected) is not int or expected < 1:
            raise ValueError('Task update requires an observed positive expected_version')
        if set(changes) - (set(Task.__dataclass_fields__) - {'id', 'version'}):
            raise ValueError('Task changes cannot replace identity/version or contain unknown fields')
        current = next((task for task in self.list() if task.id == identifier), None)
        if current is None:
            raise ValueError('Unknown task')
        if current.version != expected:
            raise ValueError('Task version conflict; re-read before updating')
        return self.write(replace(current, **changes, version=expected + 1), expected_version=expected)
