"""Data-only live extension tool identities; callbacks and authority stay worker-owned."""
from __future__ import annotations

import re
from typing import Any


def declaration(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {'revision', 'tools'}
            or type(value['revision']) is not int or not 0 <= value['revision'] <= 2**53
            or not isinstance(value['tools'], list) or len(value['tools']) > 32):
        raise ValueError('Invalid live extension tool declaration')
    result, names, identities = [], set(), set()
    for row in value['tools']:
        if (not isinstance(row, dict) or set(row) != {'name', 'registration_id'}
                or not isinstance(row['name'], str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', row['name']) is None
                or row['name'].lower() in names or not isinstance(row['registration_id'], str)
                or re.fullmatch(r'[a-f0-9]{32}', row['registration_id']) is None
                or row['registration_id'] in identities):
            raise ValueError('Invalid/duplicate extension tool identity')
        names.add(row['name'].lower())
        identities.add(row['registration_id'])
        result.append(dict(row))
    return {'revision': value['revision'], 'tools': result}
