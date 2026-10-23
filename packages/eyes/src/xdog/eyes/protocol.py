"""Explicit live implementation negotiation, independent of wire frame version."""
from __future__ import annotations

from xdog.eyes.local_control import JsonValue

FEATURES = ('owner-registrations', 'durable-events', 'async-activation')


def hello() -> dict[str, JsonValue]:
    return {'runtime_type': 'eyes-daemon',
            'control_protocol': 1, 'feature_version': 1,
            'features': list(FEATURES)}


def require_compatible(response: dict[str, JsonValue]) -> None:
    body = response.get('body')
    expected = hello()
    features = body.get('features') if isinstance(body, dict) else None
    if (response.get('status') != 'ok' or not isinstance(body, dict)
            or any(type(body.get(key)) is not type(expected[key]) or body.get(key) != expected[key]
                   for key in ('runtime_type', 'control_protocol', 'feature_version'))
            or not isinstance(features, list)
            or any(feature not in features for feature in FEATURES)):
        raise RuntimeError('Incompatible live Eyes runtime (legacy or unsupported type/protocol/features). '
                           'It was not replaced, stopped or migrated; stop it explicitly or use another directory.')
