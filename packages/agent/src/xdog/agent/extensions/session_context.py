"""Read-only owned session metadata/catalog requests and answers, never UI controls."""
from __future__ import annotations

from typing import Any

from xdog.agent.extensions.catalog import validate_page, validate_request

READ_ONLY_SESSION_OPERATIONS = frozenset({
    'snapshot', 'name', 'model', 'usage', 'active_tools', 'commands', 'catalog', 'pending_messages',
})


def validate_session_request(value: dict[str, Any]) -> None:
    operation = value.get('operation')
    if not isinstance(operation, str) or operation not in READ_ONLY_SESSION_OPERATIONS:
        raise ValueError('Extension host mutation/control APIs have been removed')
    fields = {'id', 'kind', 'operation', 'title', 'message', 'timeout', 'extension'}
    if operation in {'commands', 'catalog'}:
        fields.update({'query', 'offset', 'revision'})
    if operation == 'catalog':
        fields.update({'scope', 'session_id', 'branch_id'})
    if set(value) - fields:
        raise ValueError('Unsupported read-only session fields; no host control grant')
    if operation == 'pending_messages':
        from xdog.agent.extensions.pending_messages import request
        request(value)
    elif operation == 'active_tools':
        from xdog.agent.extensions.active_tools import request
        request(value)
    elif operation == 'commands':
        from xdog.agent.extensions.command_catalog import request
        request(value)
    elif operation == 'catalog':
        validate_request(value)


def validate_session_answer(request: dict[str, Any], value: Any) -> Any:
    validate_session_request(request)
    if value is None:
        return None
    if request['operation'] == 'active_tools':
        from xdog.agent.extensions.active_tools import answer as active_answer
        return active_answer(value)
    if request['operation'] == 'name':
        from xdog.agent.session_name import session_name
        if (not isinstance(value, dict) or set(value) != {'session_id', 'name'}
                or not isinstance(value['session_id'], str) or len(value['session_id'].encode()) > 256
                or value['name'] != session_name(value['name'])):
            raise ValueError('Invalid session name inspection')
        return dict(value)
    if request['operation'] == 'commands':
        from xdog.agent.extensions.command_catalog import answer
        return answer(request, value)
    if request["operation"] in ("model", "usage"):
        from xdog.agent.extensions.inspection import validate
        return validate(request["operation"], value)
    if request["operation"] == "catalog":
        return validate_page(request, value)
    if request["operation"] == "snapshot":
        if not isinstance(value, dict) or set(value) != {"session_id", "workspace", "group_id"}:
            raise ValueError("Invalid command session snapshot")
        for key, maximum in (("session_id", 256), ("workspace", 8192), ("group_id", 256)):
            if not isinstance(value[key], str) or len(value[key].encode()) > maximum:
                raise ValueError("Invalid command session metadata")
        return dict(value)
    if request['operation'] == 'pending_messages' and type(value) is bool:
        return value
    raise ValueError('Invalid read-only session answer')
