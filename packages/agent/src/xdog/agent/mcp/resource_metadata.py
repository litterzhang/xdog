"""Inert resource/catalog metadata; no subscription registry or URI resolution."""
from __future__ import annotations

from typing import Any

CATALOGS = frozenset({'tools', 'prompts', 'resources'})
UNSUPPORTED_WATCH_METHODS = frozenset({'subscriptions/listen', 'resources/subscribe', 'resources/unsubscribe'})


class ResourceMetadataError(ValueError):
    pass


def resource_uri(value: Any) -> str:
    """Bounded opaque URI data only, without filesystem reads or normalization."""
    try:
        if (not isinstance(value, str) or not value or len(value.encode()) > 8192
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ResourceMetadataError('Invalid resource URI')
    except UnicodeError:
        raise ResourceMetadataError('Invalid resource URI encoding') from None
    return value


def catalog_notice(message: dict[str, Any]) -> str | None:
    """Validate passive legacy invalidation only; never refresh/read/call a callback."""
    kind = next((kind for kind in CATALOGS if message.get('method') ==
                 'notifications/' + kind + '/list_changed'), None)
    if kind is None:
        return None
    params = message.get('params', {})
    if (message.get('jsonrpc') != '2.0' or set(message) - {'jsonrpc', 'method', 'params'}
            or not isinstance(params, dict) or params.keys() - {'_meta'}
            or '_meta' in params and not isinstance(params['_meta'], dict)):
        raise ResourceMetadataError('Invalid MCP catalog notification')
    return kind
