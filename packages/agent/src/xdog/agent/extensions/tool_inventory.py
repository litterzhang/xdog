"""Invocation-owned model-facing tool definitions. Inspection never grants execution."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from xdog.agent.agent import Agent
from xdog.agent.mcp.schema import json_value
from xdog.agent.mcp.stdio import McpError

MAX_BYTES = 512 * 1024
MAX_TOOLS = 2048
PAGE_SIZE = 32
SELECTION_FIELDS = {'active_tools', 'selection_revision', 'selection_mode'}


def validate(value: Any, *, session_id: str, owner_id: str) -> dict[str, Any]:
    fields = {'session_id', 'owner_id', 'model', 'api_enabled', 'tools'}
    if (not isinstance(value, dict) or set(value) not in (fields, fields | SELECTION_FIELDS)
            or value['session_id'] != session_id or value['owner_id'] != owner_id
            or not isinstance(session_id, str) or not 0 <= len(session_id.encode()) <= 256
            or not isinstance(owner_id, str) or not 0 <= len(owner_id.encode()) <= 256
            or not isinstance(value['model'], str) or len(value['model'].encode()) > 512
            or type(value['api_enabled']) is not bool or not isinstance(value['tools'], list)
            or len(value['tools']) > MAX_TOOLS):
        raise ValueError('Invalid tool inventory owner/fields')
    names = set()
    for tool in value['tools']:
        if (not isinstance(tool, dict) or set(tool) != {'name', 'description', 'parameters'}
                or not isinstance(tool['name'], str) or not 0 < len(tool['name'].encode()) <= 256
                or not isinstance(tool['description'], str) or len(tool['description'].encode()) > 65536
                or not isinstance(tool['parameters'], dict) or tool['name'] in names):
            raise ValueError('Invalid/duplicate public tool definition')
        names.add(tool['name'])
    if 'active_tools' in value:
        from xdog.agent.extensions.active_tools import names as validate_names
        active = validate_names(value['active_tools'])
        if (active is None or not set(active) <= names
                or type(value['selection_revision']) is not int or value['selection_revision'] < 0
                or value['selection_mode'] not in ('default', 'explicit')
                or value['selection_mode'] == 'default' and set(active) != names):
            raise ValueError('Invalid active/configured tool inventory relationship')
    try:
        return dict(json.loads(json_value(value, maximum=MAX_BYTES)))
    except McpError:
        raise ValueError('Tool inventory is not finite bounded JSON') from None


def capture(agent: Agent, session_id: str, owner_id: str) -> dict[str, Any]:
    state = agent.state  # One immutable state identity, not a second model/tool loop.
    capability = agent.supports_tool_calls
    revision = agent.tool_selection_revision
    selection = agent.active_tool_selection
    result = validate({'session_id': session_id, 'owner_id': owner_id, 'model': state.model,
        'api_enabled': capability is not False, 'selection_revision': revision,
        'selection_mode': 'default' if selection is None else 'explicit',
        'active_tools': [tool.name for tool in agent.active_tools],
        'tools': [{'name': tool.name, 'description': tool.description, 'parameters': tool.parameters}
                  for tool in state.tools]}, session_id=session_id, owner_id=owner_id)
    if (agent.state.model != state.model or agent.state.tools is not state.tools
            or agent.supports_tool_calls is not capability or agent.tool_selection_revision != revision):
        raise ValueError('Tool inventory owner changed during capture')
    return result


def page(snapshot: Any, *, query: str = '', offset: int = 0, revision: str = '') -> dict[str, Any] | None:
    if (not isinstance(query, str) or len(query.encode()) > 256 or any(ord(char) < 32 for char in query)
            or type(offset) is not int or not 0 <= offset <= MAX_TOOLS
            or not isinstance(revision, str) or revision and (len(revision) != 64
                or any(char not in '0123456789abcdef' for char in revision))):
        raise ValueError('Invalid tool inventory query/page/revision')
    if snapshot is None:
        return None  # Unavailable is not an empty or permission-approved inventory.
    if not isinstance(snapshot, dict):
        raise ValueError('Invalid tool inventory snapshot')
    session_id, owner_id = snapshot.get('session_id'), snapshot.get('owner_id')
    if not isinstance(session_id, str) or not isinstance(owner_id, str):
        raise ValueError('Invalid tool inventory identity')
    data = validate(snapshot, session_id=session_id, owner_id=owner_id)
    serial = fingerprint(data)
    rows = [row for row in data['tools'] if query.casefold() in (row['name'] + ' ' + row['description']).casefold()]
    stale = bool(revision and revision != serial)
    if not stale and offset > len(rows):
        raise ValueError('Tool inventory offset is past the selected catalog')
    selected = [] if stale else rows[offset:offset + PAGE_SIZE]
    following = offset + len(selected)
    selection = ({'selection_revision': data['selection_revision'], 'selection_mode': data['selection_mode'],
                  'active_tools': [row['name'] for row in selected if row['name'] in data['active_tools']],
                  'active_total': sum(row['name'] in data['active_tools'] for row in rows)}
                 if 'active_tools' in data else {})
    return {**selection, 'session_id': data['session_id'], 'owner_id': data['owner_id'], 'model': data['model'],
            'api_enabled': data['api_enabled'], 'source': 'invocation-configured-tools', 'revision': serial,
            'status': 'stale' if stale else 'ok', 'query': query, 'offset': offset, 'total': len(rows),
            'next_offset': following if not stale and following < len(rows) else None, 'tools': selected}


def fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json_value(value, maximum=MAX_BYTES).encode()).hexdigest()


class InventoryCache:
    """Bounded immutable-by-copy worker snapshots; missing refs are unavailable, never replayed."""
    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    def remember(self, value: dict[str, Any]) -> str:
        session_id, owner_id = value.get('session_id'), value.get('owner_id')
        if not isinstance(session_id, str) or not isinstance(owner_id, str):
            raise ValueError('Invalid cached inventory identity')
        captured = validate(value, session_id=session_id, owner_id=owner_id)
        key = fingerprint(captured)
        if key not in self._entries:
            if len(self._entries) >= 8:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = captured
        return key

    def resolve(self, key: Any, *, session_id: str, owner_id: str) -> dict[str, Any] | None:
        if (not isinstance(key, str) or len(key) != 64
                or any(char not in '0123456789abcdef' for char in key)):
            raise ValueError('Invalid tool inventory reference')
        value = self._entries.get(key)
        if value is None:
            return None
        return validate(value, session_id=session_id, owner_id=owner_id)
