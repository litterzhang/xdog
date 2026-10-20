"""Bounded data-only live command/shortcut declarations; no callbacks on the wire."""
from __future__ import annotations

import re
from typing import Any, cast

from xdog.agent.extensions.shortcuts import registrations


def public_name(value: Any) -> str:
    if (not isinstance(value, str) or re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', value) is None
            or value.startswith('ext-')):
        raise ValueError('Public command names require 1-32 lowercase ASCII letters/digits/_/-; ext- is reserved')
    return value


def external_name(extension: str, row: dict[str, Any]) -> str:
    return cast(str, row['public_name']) if 'public_name' in row else f"ext-{extension}-{row['name']}".lower()


def owner_for(command: str, owners: dict[str, str]) -> str | None:
    """Public names need explicit attribution; qualified identities retain their source."""
    name = command.removeprefix('/').lower()
    if name in owners:
        return owners[name]
    qualified = re.fullmatch(r'ext-([a-z][a-z0-9_]{0,31})-[a-z][a-z0-9_]{0,31}', name)
    return qualified[1] if qualified else None


def declaration(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {'revision', 'commands', 'shortcuts'}
            or type(value['revision']) is not int or not 0 <= value['revision'] <= 2**53
            or not isinstance(value['commands'], list) or len(value['commands']) > 32):
        raise ValueError('Invalid live extension command declaration')
    rows, names, public = [], set(), set()
    for row in value['commands']:
        if (not isinstance(row, dict) or not {'name', 'description'} <= set(row)
                or set(row) - {'name', 'description', 'completion_id', 'public_name'}
                or not isinstance(row['name'], str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', row['name']) is None
                or row['name'].lower() in names or not isinstance(row['description'], str)
                or len(row['description'].encode()) > 1000):
            raise ValueError('Invalid/duplicate live extension command')
        if 'public_name' in row:
            selected = public_name(row['public_name'])
            if selected in public:
                raise ValueError('Duplicate public extension command')
            public.add(selected)
        names.add(row['name'].lower())
        rows.append(dict(row))
    shortcuts = registrations(value['shortcuts'])
    if any(row['name'].lower() not in names for row in shortcuts):
        raise ValueError('Live shortcut requires an actual registered command')
    return {'revision': value['revision'], 'commands': rows, 'shortcuts': shortcuts}
