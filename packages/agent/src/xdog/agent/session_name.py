"""Bounded plain-text session names, independent of terminal rendering or model state."""
from __future__ import annotations

import unicodedata


def session_name(value: object) -> str:
    """An empty string clears a name; unsafe controls are rejected rather than hidden."""
    if not isinstance(value, str) or len(value.encode('utf-8', errors='surrogatepass')) > 256:
        raise ValueError('Session names must be plain text of at most 256 UTF-8 bytes')
    if any(unicodedata.category(char) in {'Cc', 'Cs', 'Zl', 'Zp'}
           or unicodedata.category(char) == 'Cf' and char not in {'\u200c', '\u200d'}
           or unicodedata.bidirectional(char) in {'LRE', 'RLE', 'LRO', 'RLO', 'PDF', 'LRI', 'RLI', 'FSI', 'PDI'}
           for char in value):
        raise ValueError('Session names cannot contain terminal controls, line breaks or bidi overrides')
    return value.strip()


def name_snapshot(value: object, *, session_id: str, owner_id: str) -> dict[str, str] | None:
    """Validate copied invocation metadata; missing/foreign names never borrow another owner."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'session_id', 'owner_id', 'name'}
            or value['session_id'] != session_id or value['owner_id'] != owner_id
            or not isinstance(session_id, str) or len(session_id.encode()) > 256
            or not isinstance(owner_id, str) or len(owner_id.encode()) > 256
            or value['name'] != session_name(value['name'])):
        raise ValueError('Invalid session name snapshot owner/fields')
    return {'session_id': session_id, 'owner_id': owner_id, 'name': value['name']}
