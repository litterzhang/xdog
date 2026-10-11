"""Explicit MCP client capability contract; declarations are not permission grants.

Server discovery/tools/content/subscriptions are client-initiated features, not
sampling/elicitation/roots support. Do not advertise reverse APIs without an owned
host handler, consent, cancellation and version-appropriate transport.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlsplit


def roots_config(value: Any) -> tuple[tuple[str, str], ...]:
    """Explicit configured metadata only; no filesystem reads or cwd inference."""
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError('MCP roots must be an explicit bounded array')
    rows = []
    seen = set()
    for row in value:
        if not isinstance(row, dict) or set(row) - {'uri', 'name'} or 'uri' not in row:
            raise ValueError('Invalid MCP root fields')
        uri, name = row['uri'], row.get('name', '')
        if (not isinstance(uri, str) or len(uri.encode()) > 4096
                or any(ord(c) < 0x21 or ord(c) == 0x7f for c in uri)
                or not isinstance(name, str) or len(name.encode()) > 256
                or any(ord(c) < 0x20 or ord(c) == 0x7f for c in name)):
            raise ValueError('Invalid MCP root metadata')
        parsed = urlsplit(uri)
        if re.search(r'%(?![0-9a-fA-F]{2})', parsed.path):
            raise ValueError('Invalid MCP root percent encoding')
        path = unquote(parsed.path, errors='strict')
        if (parsed.scheme != 'file' or parsed.netloc or parsed.query or parsed.fragment
                or not path.startswith('/') or '\\' in path or any(ord(c) < 0x20 for c in path)
                or any(ord(c) == 0x7f for c in path) or '..' in path.split('/') or path in seen):
            raise ValueError('MCP roots require unique local absolute file URIs')
        seen.add(path)
        rows.append((uri, name))
    return tuple(rows)


class RootCapability:
    """Connection-local configured ceiling; versioned transports own admission.

    Explicit config plus explicit connect admits disclosure to that peer only.
    Disconnect withdraws it; reviewed selection can narrow/re-enable the configured
    ceiling, never add filesystem authority or escalate a permission mode.
    """
    def __init__(self, roots: tuple[tuple[str, str], ...]) -> None:
        self.roots = roots_config([{'uri': uri, 'name': name} for uri, name in roots])
        self.ceiling = self.roots
        self.revision = 0
        self.active = False

    def advertised(self) -> dict[str, Any]:
        return {'roots': {'listChanged': True}} if self.active and self.ceiling else {}

    def replace(self, roots: tuple[tuple[str, str], ...], revision: int) -> None:
        """Publish only reviewed selection within immutable configured disclosure ceiling."""
        checked = roots_config([{'uri': uri, 'name': name} for uri, name in roots])
        if not self.active or not self.ceiling or revision != self.revision:
            raise ValueError('Root disclosure owner/revision changed')
        if not set(checked) <= set(self.ceiling):
            raise ValueError('Root selection exceeds configured disclosure consent')
        self.roots = checked
        self.revision += 1

    def response(self, message: dict[str, Any]) -> dict[str, Any]:
        answer = reverse_response(message)  # Validate identity and request shape first.
        if (message.get('method') != 'roots/list' or not self.active or not self.ceiling
                or answer.get('error', {}).get('code') != -32601):
            return answer
        params = message.get('params', {})
        if set(params) - {'_meta'} or '_meta' in params and not isinstance(params['_meta'], dict):
            return {'error': {'code': -32602, 'message': 'Invalid roots parameters'}}
        return {'result': {'roots': [{'uri': uri, **({'name': name} if name else {})}
                                     for uri, name in self.roots]}}


def advertised() -> dict[str, Any]:
    """Empty bootstrap/subscription snapshot, without request-owned input handlers."""
    return {}


def inventory() -> dict[str, str]:
    """Named SDK2.3.1 baseline families, not a claim of protocol completeness."""
    return {'roots': 'unsupported', 'sampling': 'unsupported', 'elicitation': 'unsupported',
            'tasks': 'unsupported', 'experimental': 'unsupported', 'extensions': 'unsupported'}


def reverse_response(message: dict[str, Any], *, task_creation: bool = False) -> dict[str, Any]:
    """Legacy reverse requests: bounded exact identity, no host capability borrowing.

    Modern unrelated server requests are refused by transport before this helper.
    Invalid identities are never reflected into a response or successor correlation.
    """
    identifier = message.get('id')
    if (type(identifier) is not int and not isinstance(identifier, str)
            or isinstance(identifier, str) and (not identifier or len(identifier.encode('utf-8')) > 128)
            or type(identifier) is int and not -(2**53 - 1) <= identifier <= 2**53 - 1):
        raise ValueError('Invalid MCP reverse request identity')
    if (set(message) - {'jsonrpc', 'id', 'method', 'params'} or 'result' in message or 'error' in message
            or not isinstance(message.get('method'), str) or not message['method']
            or len(message['method'].encode('utf-8')) > 256
            or not isinstance(message.get('params', {}), dict)):
        return {'error': {'code': -32600, 'message': 'Invalid client request'}}
    if task_augmented(message) and not task_creation:
        # A task envelope needs task lifecycle/receipt support, not ordinary execution.
        return {'error': {'code': -32602, 'message': 'Client task capability not supported'}}
    if message['method'] == 'ping':
        params = message.get('params', {})
        if set(params) - {'_meta'} or '_meta' in params and not isinstance(params['_meta'], dict):
            return {'error': {'code': -32602, 'message': 'Invalid ping parameters'}}
        return {'result': {}}
    return {'error': {'code': -32601, 'message': 'Client capability not supported'}}


def task_augmented(message: dict[str, Any]) -> bool:
    params = message.get('params', {})
    return (message.get('method') in {'sampling/createMessage', 'elicitation/create'}
            and isinstance(params, dict) and ('task' in params or isinstance(params.get('_meta'), dict)
            and 'io.modelcontextprotocol/related-task' in params['_meta']))
