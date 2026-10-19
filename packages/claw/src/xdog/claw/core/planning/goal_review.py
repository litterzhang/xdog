"""Data-only review validation and evidence snapshots, never a script verdict."""
from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any, cast

from xdog.claw.core.types import Goal
from xdog.eyes.models import SourceDef
from xdog.eyes.sources.file import scan


def validate_verdict(value: object, conditions: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {'verdict', 'conditions', 'summary'}:
        raise ValueError('Review requires verdict, conditions and summary')
    if value['verdict'] not in {'passed', 'continue', 'blocked'}:
        raise ValueError('Invalid review verdict')
    if not isinstance(value['summary'], str) or not value['summary'].strip():
        raise ValueError('Review requires a nonempty summary')
    rows = value['conditions']
    if not isinstance(rows, list) or len(rows) != len(conditions) or not conditions:
        raise ValueError('Review must cover every original condition, with no auto-pass')
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or set(row) != {'condition', 'status', 'evidence', 'reason'}
                or row['condition'] != conditions[index] or row['status'] not in {'passed', 'failed', 'blocked'}
                or not isinstance(row['evidence'], list) or not row['evidence']
                or any(not isinstance(item, str) or not item.strip() for item in row['evidence'])
                or not isinstance(row['reason'], str) or not row['reason'].strip()):
            raise ValueError('Each original condition needs an ordered finding, evidence and reason')
    if value['verdict'] == 'passed' and any(row['status'] != 'passed' for row in rows):
        raise ValueError('Passed requires every condition to pass')
    raw = json.dumps(value, allow_nan=False, ensure_ascii=True)
    if len(raw.encode()) > 64 * 1024:
        raise ValueError('Review exceeds 64 KiB')
    return cast(dict[str, Any], json.loads(raw))  # Sever caller-owned mutable references.


def fingerprint(goal: Goal, workspace: Path) -> str:
    """Bind the review to actual regular-file evidence, not only a task checklist.

    Default is the workspace, excluding runtime journals and dependency/cache
    trees. Explicit evidence paths are relative confined file/dir selections.
    The verifier still has to judge whether the selected evidence is sufficient.
    Bounded scans fail closed rather than silently omit oversized evidence.
    """
    paths = goal.review_paths or ('.',)
    include: list[str] = []
    selections: list[tuple[str, str]] = []
    selected_paths: list[Path] = []
    for value in paths:
        path = Path(value)
        if path.is_absolute() or '..' in path.parts or not value or any(char in value for char in '*?'):
            raise ValueError('Review evidence paths must be relative to the workspace')
        selected_paths.append(path)
        current = workspace.absolute()
        kind = 'directory'
        for index, part in enumerate(path.parts):
            current /= part
            try:
                mode = current.lstat().st_mode
            except (FileNotFoundError, NotADirectoryError):
                kind = 'absent'
                break
            if stat.S_ISLNK(mode):
                raise ValueError('Review evidence cannot select or traverse a symlink')
            if stat.S_ISDIR(mode):
                kind = 'directory'
            elif stat.S_ISREG(mode):
                kind = 'file' if index == len(path.parts) - 1 else 'absent'
            else:
                raise ValueError('Review evidence must be regular files, directories or explicit absence')
        selections.append((path.as_posix(), kind))
        if path == Path('.'):
            include.append('**')
        else:
            include.extend((path.as_posix(), path.as_posix().rstrip('/') + '/**'))
    ignored = ('.git', '.venv', '.xdog', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'node_modules')
    excludes = [name + '/**' for name in ignored
                if not any(path != Path('.') and path.is_relative_to(name) for path in selected_paths)]
    if not any('__pycache__' in path.parts for path in selected_paths):
        excludes.append('**/__pycache__/**')
    source = SourceDef('goal-review', 'file', workspace.absolute(), include=tuple(include), exclude=tuple(excludes))
    snapshot = scan(source)
    payload = {'goal': goal.id, 'revision': goal.revision, 'conditions': goal.verification.conditions,
               'selections': selections,
               'tasks': [(task.id, task.attempt_id, task.report_revision, task.report_digest) for task in goal.tasks],
               'files': [(path, state.digest, state.size) for path, state in snapshot.entries]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
