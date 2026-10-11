"""Bounded inert elicitation form/URL codecs; no browser, auth or model admission.

Native hosts must separately review requested data and any external navigation.
Unknown form contracts refuse rather than becoming arbitrary JSON-schema prompts.
"""
from __future__ import annotations

import json
from typing import Any, cast
from urllib.parse import urlsplit

from jsonschema import FormatChecker
from xdog.agent.mcp.client_capabilities import reverse_response
from xdog.agent.mcp.content import text
from xdog.agent.mcp.schema import compile_schema, json_value
from xdog.agent.mcp.stdio import McpError

LIMIT = 256 * 1024


def _object(value: Any, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise McpError('Invalid elicitation fields')
    return value


def _field(value: Any) -> None:
    row = _object(value, {'type', 'title', 'description', 'default', 'minLength', 'maxLength', 'format',
                         'minimum', 'maximum', 'enum', 'enumNames', 'oneOf', 'minItems', 'maxItems', 'items'}, {'type'})
    kind = row['type']
    variants = {
        'boolean': {'type', 'title', 'description', 'default'},
        'number': {'type', 'title', 'description', 'default', 'minimum', 'maximum'},
        'integer': {'type', 'title', 'description', 'default', 'minimum', 'maximum'},
        'string': {'type', 'title', 'description', 'default', 'minLength', 'maxLength', 'format',
                   'enum', 'enumNames', 'oneOf'},
        'array': {'type', 'title', 'description', 'default', 'minItems', 'maxItems', 'items'},
    }
    if not isinstance(kind, str) or kind not in variants or set(row) - variants[kind]:
        raise McpError('Unsupported elicitation primitive contract')
    for key in ('title', 'description'):
        if key in row:
            text(row[key], 8192)
    for key in ('minLength', 'maxLength', 'minItems', 'maxItems'):
        if key in row and (type(row[key]) is not int or not 0 <= row[key] <= 65536):
            raise McpError('Invalid elicitation size constraint')
    for key in ('minimum', 'maximum'):
        if key in row and type(row[key]) not in (int, float):
            raise McpError('Invalid elicitation numeric constraint')
    for lower, upper in (('minLength', 'maxLength'), ('minItems', 'maxItems'), ('minimum', 'maximum')):
        if lower in row and upper in row and row[lower] > row[upper]:
            raise McpError('Contradictory elicitation constraints')
    if 'format' in row and row['format'] not in ('email', 'uri', 'date', 'date-time'):
        raise McpError('Unsupported elicitation string format')
    if 'enum' in row:
        values = row['enum']
        if (not isinstance(values, list) or not values or len(values) > 128
                or any(not isinstance(item, str) or len(item.encode()) > 1024 for item in values)
                or len(set(values)) != len(values) or 'oneOf' in row):
            raise McpError('Invalid elicitation enum')
        if 'enumNames' in row and (not isinstance(row['enumNames'], list)
                or len(row['enumNames']) != len(values) or any(not isinstance(label, str)
                    or len(label.encode()) > 1024 for label in row['enumNames'])):
            raise McpError('Invalid elicitation enum titles')
    elif 'enumNames' in row:
        raise McpError('Elicitation enum titles require values')
    if 'oneOf' in row:
        _options(row['oneOf'])
    if kind == 'array':
        items = _object(row.get('items'), {'type', 'enum', 'anyOf'}, set())
        if 'anyOf' in items:
            if set(items) != {'anyOf'}:
                raise McpError('Invalid elicitation multiple-select items')
            _options(items['anyOf'])
        else:
            if set(items) != {'type', 'enum'} or items['type'] != 'string':
                raise McpError('Elicitation array must enumerate strings')
            _field({'type': 'string', 'enum': items['enum']})
    if 'default' in row:
        _validate_value(row['default'], row)


def _options(value: Any) -> None:
    if not isinstance(value, list) or not value or len(value) > 128:
        raise McpError('Invalid elicitation titled options')
    seen = set()
    for option in value:
        row = _object(option, {'const', 'title'}, {'const', 'title'})
        text(row['const'], 1024)
        text(row['title'], 1024)
        if row['const'] in seen:
            raise McpError('Duplicate elicitation option')
        seen.add(row['const'])


def _validate_value(value: Any, schema: dict[str, Any]) -> None:
    compile_schema(schema, legacy=True).validate(value, label='elicitation response')
    if schema.get('type') == 'string' and schema.get('format'):
        try:
            FormatChecker().check(value, schema['format'])
        except Exception:
            raise McpError('Elicitation response does not satisfy declared string format') from None


def request(value: Any, *, modern: bool = False) -> dict[str, Any]:
    snapshot = json.loads(json_value(value, maximum=LIMIT))
    row = _object(snapshot, {'jsonrpc', 'id', 'method', 'params'}, {'jsonrpc', 'id', 'method', 'params'})
    reverse_response(row)
    if row['jsonrpc'] != '2.0' or row['method'] != 'elicitation/create':
        raise McpError('Invalid elicitation method')
    params = _object(row['params'], {'mode', 'message', 'requestedSchema', 'elicitationId', 'url', '_meta', 'task'},
                     {'message'})
    text(params['message'], 16384, empty=False)
    if '_meta' in params and not isinstance(params['_meta'], dict):
        raise McpError('Invalid elicitation metadata')
    if modern and ('task' in params or 'io.modelcontextprotocol/related-task' in params.get('_meta', {})):
        raise McpError('Modern elicitation has no task augmentation')
    if 'task' in params:
        task = _object(params['task'], {'ttl', 'pollInterval'}, set())
        if 'ttl' in task and (type(task['ttl']) not in (int, float) or not 0 <= task['ttl'] <= 86400000):
            raise McpError('Invalid elicitation task retention')
    if 'task' in params and 'pollInterval' in params['task']:
        interval = params['task']['pollInterval']
        if type(interval) not in (int, float) or not 1 <= interval <= 600000:
            raise McpError('Invalid task polling hint')
    if params.get('mode', 'form') == 'url':
        if set(params) & {'requestedSchema'}:
            raise McpError('Mixed elicitation URL/form contract')
        if modern:
            if 'elicitationId' in params:
                raise McpError('Modern URL elicitation has no legacy completion identity')
        else:
            text(params.get('elicitationId'), 128, empty=False)
        url = text(params.get('url'), 8192, empty=False)
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or any(ord(c) < 0x21 or ord(c) == 0x7f for c in url)):
            raise McpError('Elicitation URL requires explicit safe HTTPS review; no navigation')
    elif params.get('mode', 'form') == 'form':
        if set(params) & {'elicitationId', 'url'}:
            raise McpError('Mixed elicitation form/URL contract')
        schema = _object(params.get('requestedSchema'), {'type', 'properties', 'required', 'additionalProperties',
                         '$schema', '$id', '$comment', 'title', 'description'},
                         {'type', 'properties'})
        if schema['type'] != 'object' or not isinstance(schema['properties'], dict) or len(schema['properties']) > 32:
            raise McpError('Elicitation form capacity/type invalid')
        for annotation in ('$schema', '$id', '$comment', 'title', 'description'):
            if annotation in schema:
                text(schema[annotation], 8192)
        for name, field in schema['properties'].items():
            text(name, 128, empty=False)
            _field(field)
        required = schema.get('required', [])
        if (not isinstance(required, list) or any(not isinstance(name, str) for name in required)
                or len(set(required)) != len(required) or not set(required) <= schema['properties'].keys()
                or 'additionalProperties' in schema and schema['additionalProperties'] is not False):
            raise McpError('Invalid elicitation required/extra fields')
        compile_schema(schema, input_schema=True, legacy=True)
    else:
        raise McpError('Unsupported elicitation mode')
    return cast(dict[str, Any], snapshot)


def result(value: Any, original: dict[str, Any], *, modern: bool = False) -> dict[str, Any]:
    snapshot = json.loads(json_value(value, maximum=LIMIT))
    row = _object(snapshot, {'action', 'content', '_meta'}, {'action'})
    if row['action'] not in ('accept', 'decline', 'cancel'):
        raise McpError('Invalid elicitation response action')
    if '_meta' in row and not isinstance(row['_meta'], dict):
        raise McpError('Invalid elicitation result metadata')
    if modern and 'io.modelcontextprotocol/related-task' in row.get('_meta', {}):
        raise McpError('Modern elicitation result has no related task')
    if modern and 'content' in row and row['content'] is None:
        raise McpError('Modern elicitation content cannot be null')
    payload = row.get('content')
    if row['action'] != 'accept' or original['params'].get('mode', 'form') == 'url':
        if payload is not None:
            raise McpError('Declined/canceled/URL elicitation cannot carry form data')
        return cast(dict[str, Any], snapshot)
    if not isinstance(payload, dict):
        raise McpError('Accepted elicitation requires explicit form data')
    schema = original['params']['requestedSchema']
    if set(payload) - schema['properties'].keys():
        raise McpError('Elicitation result has unrequested fields')
    for name, item in payload.items():
        _validate_value(item, schema['properties'][name])
    compile_schema(schema, input_schema=True, legacy=True).validate(payload, label='elicitation response')
    return cast(dict[str, Any], snapshot)
