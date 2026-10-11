"""Inert operator-specified resource completion form codec; no URI interpretation."""
from __future__ import annotations

import json


def _string(value: object, maximum: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or not empty and not value.strip() or len(value.encode()) > maximum:
        raise ValueError('Invalid or oversized opaque completion string')
    return value


def resource_request(text: str) -> tuple[str, str, str, dict[str, str]]:
    try:
        if len(text.encode()) > 131072:
            raise ValueError('Opaque completion JSON exceeds its limit')
        value = json.loads(text)
        if not isinstance(value, dict) or set(value) != {'uri', 'argument', 'value', 'context'}:
            raise ValueError('Use exactly uri, argument, value and context fields')
        uri = _string(value['uri'], 8192, empty=False)
        argument = _string(value['argument'], 256, empty=False)
        prefix = _string(value['value'], 8192)
        source = value['context']
        if not isinstance(source, dict) or len(source) > 128:
            raise ValueError('Context must contain at most 128 named string arguments')
        context = {_string(key, 256, empty=False): _string(item, 8192) for key, item in source.items()}
        if argument in context:
            raise ValueError('Current argument must not also appear in context')
        if sum(len(key.encode()) + len(item.encode()) for key, item in context.items()) > 65536:
            raise ValueError('Opaque completion context exceeds its limit')
        if len(json.dumps(context, ensure_ascii=False).encode()) > 131072:
            raise ValueError('Opaque completion context encoding exceeds its limit')
        return uri, argument, prefix, context
    except (UnicodeError, RecursionError):
        raise ValueError('Invalid opaque completion JSON encoding') from None
