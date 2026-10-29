"""Explicit live implementation negotiation, independent of wire frame version."""
from __future__ import annotations

from xdog.eyes.local_control import JsonValue

FEATURES = ('owner-registrations', 'durable-events', 'async-activation')
SUPPORTED_FEATURES = (*FEATURES, 'remote-sources-v1', 'content-sources-v1',
                      'observation-contracts-v1', 'event-sources-v1')


def source_features(definition: JsonValue) -> set[str]:
    """Finite built-in feature negotiation; no code or plugins named by JSON."""
    sources = definition.get('sources', []) if isinstance(definition, dict) else []
    needed: set[str] = set()
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict):
                continue
            if source.get('type') == 'event':
                needed.update(('event-sources-v1', 'observation-contracts-v1'))
            if source.get('format') == 'json':
                needed.add('observation-contracts-v1')
            if source.get('type') in ('http', 'github', 'feed'):
                needed.add('remote-sources-v1')
            if (source.get('type') == 'feed' or source.get('mode') == 'html'
                    or source.get('resource') == 'releases'):
                needed.add('content-sources-v1')
    detectors = definition.get('detectors', []) if isinstance(definition, dict) else []
    by_id = ({item.get('id'): item.get('type') for item in sources if isinstance(item, dict)}
             if isinstance(sources, list) else {})
    for detector in detectors if isinstance(detectors, list) else []:
        if not isinstance(detector, dict):
            continue
        if (detector.get('compare_fields') or detector.get('type') == 'event.received' or
                detector.get('type') in ('record.change', 'value.condition', 'availability') and
                by_id.get(detector.get('source')) in ('file', 'scheduler', 'event')):
            needed.add('observation-contracts-v1')
    return needed


def hello() -> dict[str, JsonValue]:
    return {'runtime_type': 'eyes-daemon',
            'control_protocol': 1, 'feature_version': 1,
            'features': list(SUPPORTED_FEATURES)}


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
