"""Explicit MCP-scoped completed conversation projection for disclosure review.

No disk/resource/URI reads. Hosts supply immutable current history and their text
redactor. Only completed turns involving selected MCP tools are included; unrelated
turns, local/sibling tools, reasoning/signatures and custom entries never enter the
snapshot. This is a disclosure projection, not resumable history or a tool grant.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any, cast

from xdog.agent.core import AgentMessage
from xdog.agent.mcp.sampling import MAX_SAMPLING_BYTES, request
from xdog.agent.mcp.schema import json_value
from xdog.agent.mcp.stdio import McpError
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, ToolCall, ToolResultMessage, UserMessage

_SECRET = re.compile(r'password|passwd|secret|token|authorization|api[_-]?key|credential|signature|encrypted', re.I)


def _safe(value: Any, redact: Callable[[str], str], *, depth: int = 0) -> Any:
    if depth > 64:
        raise McpError('Sampling context metadata nesting exceeds capacity')
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise McpError('Invalid sampling context metadata key; no partial projection')
        return {key: '<redacted>' if _SECRET.search(key) else _safe(item, redact, depth=depth + 1)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item, redact, depth=depth + 1) for item in value]
    return redact(value) if isinstance(value, str) else value


def _structured(message: ToolResultMessage, server: str, redact: Callable[[str], str]) -> dict[str, Any]:
    details = message.details
    # Arbitrary/local host detail wrappers are not MCP structured content.
    if not isinstance(details, dict) or not ({'mcp_server', 'structured_content_present'} & details.keys()):
        return {}
    present = details.get('structured_content_present')
    if (details.get('mcp_server') != server or type(present) is not bool
            or present and 'structured_content' not in details):
        raise McpError('Sampling context structured-result provenance mismatch')
    if not present:
        return {}
    return {'structuredContent': _safe(details['structured_content'], redact)}


def project(history: tuple[AgentMessage, ...], aliases: dict[str, tuple[str, str]], server: str, scope: str,
            redact: Callable[[str], str]) -> list[dict[str, Any]]:
    if scope not in ('thisServer', 'allServers') or not server:
        raise McpError('Explicit MCP context scope/server required')
    if len(history) > 2000:
        raise McpError('Sampling context history exceeds capacity; no truncation')
    selected = {alias: identity for alias, identity in aliases.items()
                if scope == 'allServers' or identity[0] == server}
    turns: list[list[AgentMessage]] = []
    current: list[AgentMessage] = []
    for message in history:
        if isinstance(message, UserMessage) and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    rows: list[dict[str, Any]] = []
    for turn_index, turn in enumerate(turns):
        # Only completed assistant turns; do not disclose a partial pending batch.
        if (not turn or not isinstance(turn[0], UserMessage) or not isinstance(turn[-1], AssistantMessage)
                or any(isinstance(part, ToolCall) for part in turn[-1].content)
                or turn[-1].error_message or turn[-1].stop_reason in ('error', 'aborted')):
            continue
        calls = {part.id: part for message in turn if isinstance(message, AssistantMessage)
                 for part in message.content if isinstance(part, ToolCall) and part.name in selected}
        if not calls:
            continue
        all_calls = [part for message in turn if isinstance(message, AssistantMessage)
                     for part in message.content if isinstance(part, ToolCall)]
        if len({part.id for part in all_calls}) != len(all_calls):
            raise McpError('Ambiguous sampling context tool-call identity')
        results = [message for message in turn if isinstance(message, ToolResultMessage)
                   and message.tool_call_id in calls]
        if (len({message.tool_call_id for message in results}) != len(results)
                or any(message.tool_name != calls[message.tool_call_id].name for message in results)):
            raise McpError('Sampling context tool-result provenance mismatch')
        if not calls.keys() <= {message.tool_call_id for message in results}:
            continue
        # IDs are local to their original turn, but the disclosure is one
        # combined conversation. Assign stable scoped IDs before review.
        identities = {identifier: 'context-' + hashlib.sha256(
            f'{turn_index}:{identifier}'.encode()).hexdigest()[:32] for identifier in calls}
        for message in turn:
            if isinstance(message, UserMessage):
                if isinstance(message.content, str):
                    parts = [{'type': 'text', 'text': redact(message.content)}]
                else:
                    parts = [_part(part, redact) for part in message.content]
                rows.append({'role': 'user', 'content': parts})
            elif isinstance(message, AssistantMessage):
                parts = []
                for part in message.content:
                    if isinstance(part, TextContent):
                        parts.append({'type': 'text', 'text': redact(part.text)})
                    elif isinstance(part, ToolCall) and part.id in calls:
                        parts.append({'type': 'tool_use', 'id': identities[part.id],
                                      'name': selected[part.name][1], 'input': _safe(part.arguments, redact)})
                if parts:
                    rows.append({'role': 'assistant', 'content': parts})
            elif isinstance(message, ToolResultMessage) and message.tool_call_id in calls:
                rows.append({'role': 'user', 'content': [{'type': 'tool_result',
                    'toolUseId': identities[message.tool_call_id],
                    'content': [_part(part, redact) for part in message.content],
                    'isError': message.is_error,
                    **_structured(message, selected[message.tool_name][0], redact)}]})
    # Same codec/schema and budget as the subsequent exact disclosure review.
    envelope = {'jsonrpc': '2.0', 'id': 'context', 'method': 'sampling/createMessage',
                'params': {'messages': rows, 'maxTokens': 1}}
    validated = request(json.loads(json_value(envelope, maximum=MAX_SAMPLING_BYTES)))
    return cast(list[dict[str, Any]], validated['params']['messages'])


def _part(part: TextContent | ImageContent, redact: Callable[[str], str]) -> dict[str, Any]:
    if isinstance(part, TextContent):
        return {'type': 'text', 'text': redact(part.text)}
    if isinstance(part, ImageContent):
        return {'type': 'image', 'data': part.data, 'mimeType': part.mime_type}
    raise McpError('Unsupported scoped sampling context part; no repr conversion')


def bind_context(rows: list[dict[str, Any]], source: dict[str, Any]) -> list[dict[str, Any]]:
    """Avoid context/server ID collisions before exact combined disclosure review.

    Validate context independently first: never repair forged/duplicate/unsettled
    input. Server IDs stay unchanged; only colliding context pairs get fresh scope.
    """
    envelope = {'jsonrpc': '2.0', 'id': 'context', 'method': 'sampling/createMessage',
                'params': {'messages': rows, 'maxTokens': 1}}
    checked = request(envelope)['params']['messages']
    reserved = {part['id'] for row in source['params']['messages']
                for part in (row['content'] if isinstance(row['content'], list) else [row['content']])
                if part['type'] == 'tool_use'}
    original = {part['id'] for row in checked
                for part in (row['content'] if isinstance(row['content'], list) else [row['content']])
                if part['type'] == 'tool_use'}
    occupied = reserved | original
    mapping: dict[str, str] = {}
    for identifier in sorted(original & reserved):
        index = 0
        while True:
            candidate = 'context-' + hashlib.sha256(f'{identifier}:{index}'.encode()).hexdigest()[:32]
            if candidate not in occupied:
                break
            index += 1
        occupied.add(candidate)
        mapping[identifier] = candidate
    for row in checked:
        for part in (row['content'] if isinstance(row['content'], list) else [row['content']]):
            key = 'id' if part['type'] == 'tool_use' else 'toolUseId' if part['type'] == 'tool_result' else ''
            if key and part[key] in mapping:
                part[key] = mapping[part[key]]
    return cast(list[dict[str, Any]], checked)
