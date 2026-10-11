"""Bounded data-only live command/shortcut declarations; no callbacks on the wire."""
from __future__ import annotations

import re
from typing import Any

from xdog.agent.extensions.shortcuts import registrations


def declaration(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {'revision', 'commands', 'shortcuts'}
            or type(value['revision']) is not int or not 0 <= value['revision'] <= 2**53
            or not isinstance(value['commands'], list) or len(value['commands']) > 32):
        raise ValueError('Invalid live extension command declaration')
    rows, names = [], set()
    for row in value['commands']:
        if (not isinstance(row, dict) or not {'name', 'description'} <= set(row)
                or set(row) - {'name', 'description', 'completion_id'}
                or not isinstance(row['name'], str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', row['name']) is None
                or row['name'].lower() in names or not isinstance(row['description'], str)
                or len(row['description'].encode()) > 1000):
            raise ValueError('Invalid/duplicate live extension command')
        names.add(row['name'].lower())
        rows.append(dict(row))
    shortcuts = registrations(value['shortcuts'])
    if any(row['name'].lower() not in names for row in shortcuts):
        raise ValueError('Live shortcut requires an actual registered command')
    return {'revision': value['revision'], 'commands': rows, 'shortcuts': shortcuts}
