"""Lossless bounded sampling review data, never model/host authorization.

Preserve explicit context/tool preferences for host review, not implicit admission.
Image/audio data stays inert until the separately reviewed model adapter accepts it.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from xdog.agent.mcp.content import content, mime, text, uri
from xdog.agent.mcp.schema import compile_schema, json_value
from xdog.agent.mcp.stdio import McpError

MAX_SAMPLING_BYTES = 256 * 1024


def _object(value: Any, allowed: set[str], required: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise McpError('Invalid sampling object fields')
    return value


def _meta(row: dict[str, Any]) -> None:
    if '_meta' in row:
        value = row['_meta']
        if not isinstance(value, dict):
            raise McpError('Invalid sampling metadata')
        if 'progressToken' in value and (type(value['progressToken']) not in (str, int)
                or isinstance(value['progressToken'], str) and len(value['progressToken'].encode()) > 128):
            raise McpError('Invalid sampling progress token')


def _number(value: Any, minimum: float, maximum: float) -> None:
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise McpError('Invalid sampling numeric range')


def _block(value: Any, *, tool_content: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise McpError('Invalid sampling content block')
    kind = value.get('type')
    if kind in {'text', 'image', 'audio'}:
        fields = {'type', 'text', 'annotations', '_meta'} if kind == 'text' else {
            'type', 'data', 'mimeType', 'annotations', '_meta'}
        _object(value, fields)
        # No file/URL access or playback; validation/decode happens off the owner loop.
        return content(value)
    if tool_content and kind == 'tool_use':
        _object(value, {'type', 'name', 'id', 'input', '_meta'}, {'type', 'name', 'id', 'input'})
        text(value['name'], 256, empty=False)
        text(value['id'], 128, empty=False)
        if not isinstance(value['input'], dict):
            raise McpError('Invalid sampling tool-use input')
    elif tool_content and kind == 'tool_result':
        _object(value, {'type', 'toolUseId', 'content', 'structuredContent', 'isError', '_meta'},
                {'type', 'toolUseId', 'content'})
        text(value['toolUseId'], 128, empty=False)
        if not isinstance(value['content'], list) or len(value['content']) > 64:
            raise McpError('Invalid sampling tool-result content')
        for part in value['content']:
            content(part)
        if 'isError' in value and type(value['isError']) is not bool:
            raise McpError('Invalid sampling tool-result status')
    else:
        raise McpError('Unsupported sampling content; not silently discarded')
    _meta(value)
    return value


def request(value: Any, *, modern: bool = False) -> dict[str, Any]:
    """Validate and copy every named baseline request field for later explicit review."""
    snapshot = json.loads(json_value(value, maximum=MAX_SAMPLING_BYTES))
    envelope = _object(snapshot, {'jsonrpc', 'id', 'method', 'params'}, {'jsonrpc', 'id', 'method', 'params'})
    from xdog.agent.mcp.client_capabilities import reverse_response
    reverse_response(envelope)  # Includes bounded/safe exact request identity.
    if envelope['jsonrpc'] != '2.0' or envelope['method'] != 'sampling/createMessage':
        raise McpError('Invalid sampling request method')
    params = _object(envelope['params'], {'messages', 'modelPreferences', 'systemPrompt', 'includeContext',
        'temperature', 'maxTokens', 'stopSequences', 'metadata', 'tools', 'toolChoice', '_meta', 'task'},
        {'messages', 'maxTokens'})
    _meta(params)
    if modern and ('task' in params or 'io.modelcontextprotocol/related-task' in params.get('_meta', {})):
        raise McpError('Modern sampling has no task augmentation')
    if type(params['maxTokens']) is not int or not 1 <= params['maxTokens'] <= 65536:
        raise McpError('Sampling maxTokens exceeds the explicit client ceiling')
    if not isinstance(params['messages'], list) or len(params['messages']) > 128:
        raise McpError('Sampling message capacity exceeded')
    pending: set[str] = set()
    seen_calls: set[str] = set()
    for message in params['messages']:
        row = _object(message, {'role', 'content', '_meta'}, {'role', 'content'})
        if row['role'] not in ('user', 'assistant'):
            raise McpError('Invalid sampling message role')
        _meta(row)
        if row['role'] == 'assistant' and pending:
            raise McpError('Sampling assistant continuation precedes tool settlement')
        parts = row['content'] if isinstance(row['content'], list) else [row['content']]
        if not parts or len(parts) > 64:
            raise McpError('Sampling message content capacity exceeded')
        for part in parts:
            _block(part, tool_content=True)
            if part['type'] == 'tool_use':
                if row['role'] != 'assistant' or part['id'] in seen_calls:
                    raise McpError('Sampling tool-use role/identity is invalid')
                seen_calls.add(part['id'])
                pending.add(part['id'])
            elif part['type'] == 'tool_result':
                if row['role'] != 'user' or part['toolUseId'] not in pending:
                    raise McpError('Sampling tool result lacks its original pending call')
                pending.remove(part['toolUseId'])
    if pending:
        raise McpError('Sampling request has unsettled tool calls')
    if 'systemPrompt' in params:
        text(params['systemPrompt'], 65536)
    if params.get('includeContext', 'none') not in ('none', 'thisServer', 'allServers'):
        raise McpError('Invalid sampling context preference')
    if 'temperature' in params:
        _number(params['temperature'], 0, 2)
    if 'modelPreferences' in params:
        preferences = _object(params['modelPreferences'],
                              {'hints', 'costPriority', 'speedPriority', 'intelligencePriority'})
        for key in ('costPriority', 'speedPriority', 'intelligencePriority'):
            if key in preferences:
                _number(preferences[key], 0, 1)
        if 'hints' in preferences:
            hints = preferences['hints']
            if not isinstance(hints, list) or len(hints) > 32:
                raise McpError('Sampling model hint capacity exceeded')
            for hint in hints:
                row = _object(hint, {'name'})
                if 'name' in row:
                    text(row['name'], 256)
    if 'stopSequences' in params:
        sequences = params['stopSequences']
        if not isinstance(sequences, list) or len(sequences) > 32:
            raise McpError('Sampling stop-sequence capacity exceeded')
        for sequence in sequences:
            text(sequence, 1024)
    if 'metadata' in params and not isinstance(params['metadata'], dict):
        raise McpError('Invalid sampling opaque metadata')
    if 'task' in params:
        task = _object(params['task'], {'ttl', 'pollInterval'})
        if 'ttl' in task:
            if type(task['ttl']) not in (int, float) or not 0 <= task['ttl'] <= 86400000:
                raise McpError('Invalid sampling task retention')
    if 'task' in params and 'pollInterval' in params['task']:
        interval = params['task']['pollInterval']
        if type(interval) not in (int, float) or not 1 <= interval <= 600000:
            raise McpError('Invalid task polling hint')
    if 'tools' in params:
        tools = params['tools']
        if not isinstance(tools, list) or len(tools) > 32:
            raise McpError('Sampling tool capacity exceeded')
        names = set()
        for tool in tools:
            row = _object(tool, {'name', 'title', 'description', 'inputSchema', 'outputSchema',
                                 'annotations', 'execution', '_meta', 'icons'}, {'name', 'inputSchema'})
            name = text(row['name'], 256, empty=False)
            if name in names:
                raise McpError('Duplicate sampling tool name')
            names.add(name)
            _meta(row)
            if 'icons' in row:
                icons = row['icons']
                if not isinstance(icons, list) or len(icons) > 16:
                    raise McpError('Sampling tool icon capacity exceeded')
                for icon in icons:
                    item = _object(icon, {'src', 'mimeType', 'sizes', 'theme'}, {'src'})
                    uri(item['src'])  # Inert metadata only: never retrieve an icon URL/file.
                    if 'mimeType' in item:
                        mime(item['mimeType'], required=True)
                    if 'theme' in item and item['theme'] not in ('light', 'dark'):
                        raise McpError('Invalid sampling icon theme')
                    if 'sizes' in item:
                        if not isinstance(item['sizes'], list) or len(item['sizes']) > 32:
                            raise McpError('Sampling icon size capacity exceeded')
                        for size in item['sizes']:
                            text(size, 128, empty=False)
            for key in ('title', 'description'):
                if key in row:
                    text(row[key], 16384)
            compile_schema(row['inputSchema'], input_schema=True, legacy=True)
            if 'outputSchema' in row:
                compile_schema(row['outputSchema'], legacy=True)
            if 'annotations' in row:
                annotations = _object(row['annotations'], {'title', 'readOnlyHint', 'destructiveHint',
                                                            'idempotentHint', 'openWorldHint'})
                for key, annotation in annotations.items():
                    if key == 'title':
                        text(annotation, 256)
                    elif type(annotation) is not bool:
                        raise McpError('Invalid sampling tool annotation')
            if 'execution' in row:
                if modern:
                    raise McpError('Modern sampling tool has no task execution metadata')
                execution = _object(row['execution'], {'taskSupport'})
                if execution.get('taskSupport', 'forbidden') not in ('required', 'optional', 'forbidden'):
                    raise McpError('Invalid sampling tool task capability')
    if 'toolChoice' in params:
        choice = _object(params['toolChoice'], {'mode'})
        mode = choice.get('mode', 'auto')
        if mode not in ('auto', 'required', 'none') or mode == 'required' and not params.get('tools'):
            raise McpError('Sampling required tool choice requires explicit tools')
    return cast(dict[str, Any], snapshot)


def result(value: Any, original: dict[str, Any], *, modern: bool = False) -> dict[str, Any]:
    """Validate exact result without converting unknown/binary/tool blocks to prose."""
    snapshot = json.loads(json_value(value, maximum=MAX_SAMPLING_BYTES))
    row = _object(snapshot, {'model', 'role', 'content', 'stopReason', '_meta'}, {'model', 'role', 'content'})
    text(row['model'], 256, empty=False)
    if row['role'] not in ('user', 'assistant'):
        raise McpError('Invalid sampling result role')
    _meta(row)
    if modern and 'io.modelcontextprotocol/related-task' in row.get('_meta', {}):
        raise McpError('Modern sampling result has no related task')
    if 'stopReason' in row:
        text(row['stopReason'], 256)
    with_tools = 'tools' in original['params'] or 'toolChoice' in original['params']
    if isinstance(row['content'], list) and not with_tools and not modern:
        raise McpError('Sampling result array requires reviewed tool request')
    parts = row['content'] if isinstance(row['content'], list) else [row['content']]
    if not parts or len(parts) > 64:
        raise McpError('Sampling result content capacity exceeded')
    output_calls: set[str] = set()
    for part in parts:
        _block(part, tool_content=with_tools)
        if isinstance(part, dict) and part.get('type') == 'tool_result':
            raise McpError('Sampling output cannot impersonate a server tool result')
        if isinstance(part, dict) and part.get('type') == 'tool_use':
            if original['params'].get('toolChoice', {}).get('mode', 'auto') == 'none':
                raise McpError('Sampling result contradicts reviewed tool choice')
            if row['role'] != 'assistant' or part['id'] in output_calls:
                raise McpError('Sampling output tool-use role/identity is invalid')
            output_calls.add(part['id'])
            allowed = {tool['name'] for tool in original['params'].get('tools', [])}
            if part['name'] not in allowed:
                raise McpError('Sampling result tool outside reviewed request')
            tool = next(tool for tool in original['params']['tools'] if tool['name'] == part['name'])
            compile_schema(tool['inputSchema'], input_schema=True, legacy=True).validate(
                part['input'], label='sampling result tool input')
    if original['params'].get('toolChoice', {}).get('mode', 'auto') == 'required' and not output_calls:
        raise McpError('Sampling result lacks reviewed required tool proposal')
    return cast(dict[str, Any], snapshot)


def review(value: Any) -> dict[str, Any]:
    """Bounded exact review body and digest; no implicit context or tool grant."""
    snapshot = request(value)
    digest = hashlib.sha256(json_value(snapshot, maximum=MAX_SAMPLING_BYTES).encode()).hexdigest()
    return {'request': snapshot, 'sha256': digest,
            'notice': 'Untrusted server sampling request. Review model/context/tools separately; '
                      'no parent history, filesystem, tool or permission grant is implied.'}
