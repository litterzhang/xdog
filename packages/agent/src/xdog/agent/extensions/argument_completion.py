"""Registration-bound command argument data; it never invokes the command itself."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


def plain(value: Any, maximum: int) -> str:
    if (not isinstance(value, str) or len(value.encode()) > maximum
            or any(unicodedata.category(char) in {'Cc', 'Cs', 'Cf', 'Zl', 'Zp'} for char in value)):
        raise ValueError('Argument completion requires bounded single-line plain text')
    return value


def items(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError('Argument completion capacity/type invalid')
    result, seen = [], set()
    for row in value:
        if (not isinstance(row, dict) or not {'value'} <= set(row)
                or set(row) - {'value', 'label', 'description'}):
            raise ValueError('Argument completion item fields invalid')
        text = plain(row['value'], 2048)
        if not text or text in seen:
            raise ValueError('Argument completions require unique nonempty values')
        seen.add(text)
        result.append({'value': text, 'label': plain(row.get('label', text), 2048),
                       'description': plain(row.get('description', ''), 4096)})
    if len(json.dumps(result, ensure_ascii=True).encode()) > 64 * 1024:
        raise ValueError('Argument completion byte capacity exceeded')
    return result


def completion_id(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[a-f0-9]{32}', value) is None:
        raise ValueError('Invalid command argument registration identity')
    return value


def argument_context(value: Any, prefix: str) -> dict[str, Any] | None:
    """Copied read-only argument draft coordinates; None explicitly denotes legacy prefix-only input."""
    plain(prefix, 4096)
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'arguments', 'cursor', 'start', 'end'}:
        raise ValueError('Invalid argument context fields')
    arguments = plain(value['arguments'], 8192)
    start, cursor, end = value['start'], value['cursor'], value['end']
    if (any(type(offset) is not int for offset in (start, cursor, end))
            or not 0 <= start <= cursor <= end <= len(arguments)
            or arguments[start:cursor] != prefix):
        raise ValueError('Argument context prefix/coordinates do not match')
    return {'arguments': arguments, 'cursor': cursor, 'start': start, 'end': end}
