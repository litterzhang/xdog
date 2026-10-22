"""Bounded read-only terminal command metadata, not execution or permission authority."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

PAGE_SIZE = 32
MAX_COMMANDS = 10000
MAX_BYTES = 1024 * 1024


def text(value: Any, maximum: int, *, empty: bool = True) -> str:
    if (not isinstance(value, str) or len(value.encode()) > maximum or not empty and not value
            or any(unicodedata.category(char) in {'Cc', 'Cs', 'Cf', 'Zl', 'Zp'} for char in value)):
        raise ValueError('Invalid plain command catalog text')
    return value


def request(value: dict[str, Any]) -> None:
    text(value.get('query', ''), 256)
    if type(value.get('offset', 0)) is not int or not 0 <= value.get('offset', 0) <= MAX_COMMANDS:
        raise ValueError('Invalid command catalog offset')
    revision = value.get('revision', '')
    if not isinstance(revision, str) or revision and not re.fullmatch(r'[a-f0-9]{64}', revision):
        raise ValueError('Invalid command catalog revision')
    if any(key in value for key in ('target', 'session_id', 'branch_id', 'scope', 'index', 'name', 'expected_name')):
        raise ValueError('Command inspection cannot select a different session or host action')


def page(commands: Mapping[str, str], query: dict[str, Any], *, session_id: str, owner: str) -> dict[str, Any]:
    request(query)
    text(session_id, 256)
    text(owner, 512, empty=False)
    if len(commands) > MAX_COMMANDS:
        raise ValueError('Command catalog capacity exceeded; no partial catalog')
    rows: dict[str, str] = {}
    size = 0
    for raw, description in commands.items():
        name = '/' + text(raw, 256, empty=False).removeprefix('/')
        if name == '/' or any(char.isspace() for char in name):
            raise ValueError('Invalid command catalog name')
        detail = text(description, 8192)
        if name in rows and rows[name] != detail:
            raise ValueError('Conflicting command catalog entry')
        rows[name] = detail
        size += len(name.encode()) + len(detail.encode())
        if size > MAX_BYTES:
            raise ValueError('Command catalog byte capacity exceeded; no partial catalog')
    catalog = [{'name': name, 'description': description}
               for name, description in sorted(rows.items(), key=lambda row: row[0].casefold())]
    revision = hashlib.sha256(json.dumps([session_id, owner, catalog], ensure_ascii=True,
                                        separators=(',', ':')).encode()).hexdigest()
    selected = [row for row in catalog if query.get('query', '').strip().casefold() in
                (row['name'] + ' ' + row['description']).casefold()]
    offset = query.get('offset', 0)
    stale = bool(query.get('revision') and query['revision'] != revision)
    if not stale and offset > len(selected):
        raise ValueError('Command catalog offset is past the selected menu')
    result = [] if stale else selected[offset:offset + PAGE_SIZE]
    following = offset + len(result)
    result_page = answer(query, {'session_id': session_id, 'source': 'terminal-menu', 'revision': revision,
        'query': query.get('query', ''), 'offset': offset, 'total': len(selected),
        'status': 'stale' if stale else 'ok', 'commands': result,
        'next_offset': following if not stale and following < len(selected) else None})
    assert result_page is not None
    return result_page


def answer(query: dict[str, Any], value: Any) -> dict[str, Any] | None:
    request(query)
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'session_id', 'source', 'revision', 'query', 'offset', 'total',
        'status', 'commands', 'next_offset'} or value['source'] != 'terminal-menu'
            or value['query'] != query.get('query', '') or value['offset'] != query.get('offset', 0)
            or type(value['offset']) is not int or type(value['total']) is not int
            or not 0 <= value['total'] <= MAX_COMMANDS or value['status'] not in {'ok', 'stale'}
            or not isinstance(value['revision'], str) or not re.fullmatch(r'[a-f0-9]{64}', value['revision'])
            or not isinstance(value['commands'], list) or len(value['commands']) > PAGE_SIZE):
        raise ValueError('Invalid command catalog page')
    text(value['session_id'], 256)
    names = set()
    for row in value['commands']:
        if not isinstance(row, dict) or set(row) != {'name', 'description'}:
            raise ValueError('Invalid command catalog entry')
        name = text(row['name'], 257, empty=False)
        if not name.startswith('/') or name == '/' or name in names or any(char.isspace() for char in name):
            raise ValueError('Invalid/duplicate command catalog name')
        names.add(name)
        text(row['description'], 8192)
    if value['status'] == 'stale':
        if (value['commands'] or value['next_offset'] is not None or not query.get('revision')
                or query['revision'] == value['revision']):
            raise ValueError('Stale command catalog cannot contain partial data')
    else:
        following = value['offset'] + len(value['commands'])
        if (value['offset'] > value['total'] or following > value['total']
                or len(value['commands']) != min(PAGE_SIZE, value['total'] - value['offset'])
                or value['next_offset'] != (following if following < value['total'] else None)
                or value['next_offset'] is not None and type(value['next_offset']) is not int
                or query.get('revision') and query['revision'] != value['revision']):
            raise ValueError('Inconsistent command catalog pagination/revision')
    return dict(json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False)))


# Read-only menu authority is independent of session/editor mutation. Render,
# provider, setup, terminal interception and silent inherited validation are absent.
METHODS = frozenset({'xdog/command', 'xdog/event', 'xdog/pre', 'xdog/post',
                     'xdog/input', 'xdog/context_finish'})


def invocation_scope(value: Any) -> dict[str, str] | None:
    if (not isinstance(value, dict) or set(value) != {'invocation', 'session_id', 'child_id', 'method'}
            or any(not isinstance(item, str) for item in value.values())
            or not value['invocation'] or not value['session_id'] or value['method'] not in METHODS):
        return None
    return dict(value)
