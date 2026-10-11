"""Local editable notification filters; hosts independently validate protocol admission."""
from __future__ import annotations

import json
from typing import Any

FLAGS = ('toolsListChanged', 'promptsListChanged', 'resourcesListChanged')


def notification_filter(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() - {*FLAGS, 'resourceSubscriptions'}:
        raise ValueError('Use catalog boolean flags and an explicit resourceSubscriptions URI array')
    result: dict[str, Any] = {}
    for key in FLAGS:
        if key in value:
            if type(value[key]) is not bool:
                raise ValueError('Catalog notification flags must be booleans')
            if value[key]:
                result[key] = True
    if 'resourceSubscriptions' in value:
        uris = value['resourceSubscriptions']
        if (not isinstance(uris, list) or len(uris) > 16
                or any(not isinstance(uri, str) or not uri or len(uri.encode()) > 8192
                       or any(ord(char) < 32 or ord(char) == 127 for char in uri) for uri in uris)):
            raise ValueError('Provide at most 16 explicit bounded resource URIs')
        if len(set(uris)) != len(uris) or sum(len(uri.encode()) for uri in uris) > 65536:
            raise ValueError('Duplicate/excessive resource subscription URIs')
        if uris:
            result['resourceSubscriptions'] = sorted(uris)
    if not result:
        raise ValueError('Select at least one notification filter')
    return result


def filter_text(value: Any) -> str:
    return json.dumps(notification_filter(value), sort_keys=True, ensure_ascii=True, separators=(',', ':'))
