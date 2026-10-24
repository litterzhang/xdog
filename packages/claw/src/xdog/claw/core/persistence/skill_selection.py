"""Small owned skill selection sidecar; no instruction bodies or model history."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from xdog.agent.skills.repo import is_valid_skill_name


def validate(value: object) -> tuple[str, ...]:
    if (not isinstance(value, list) or len(value) > 128
            or any(not isinstance(slug, str) or not is_valid_skill_name(slug) for slug in value)
            or len(set(value)) != len(value)):
        raise ValueError('Invalid saved skill selection')
    return tuple(value)


def path(directory: Path, session_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_id):
        raise ValueError('Invalid skill session owner')
    return directory / (session_id + '.skills.json')


def load(directory: Path, session_id: str) -> tuple[str, ...]:
    file = path(directory, session_id)
    if file.is_symlink():
        raise ValueError('Saved skill selection is not owned')
    if not file.exists():
        return ()
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 16384:
        raise ValueError('Saved skill selection unavailable')
    value = json.loads(file.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or set(value) != {'active'}:
        raise ValueError('Invalid saved skill selection fields')
    return validate(value['active'])


def save(directory: Path, session_id: str, active: tuple[str, ...]) -> None:
    validate(list(active))
    file = path(directory, session_id)
    if file.is_symlink():
        raise ValueError('Skill selection destination is not owned')
    descriptor, temporary = tempfile.mkstemp(prefix='.skills-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump({'active': list(active)}, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
