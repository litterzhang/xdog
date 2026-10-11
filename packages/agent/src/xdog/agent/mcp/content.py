"""Bounded inert MCP content validation shared by tools and context previews.

No URI, MIME type or annotation grants authority to fetch, play or open content.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime
from typing import Any, cast

from xdog.agent.mcp.schema import json_value
from xdog.agent.mcp.stdio import MAX_FRAME, McpError
from xdog.ai.utils.image_files import decode_image

MAX_BINARY_BYTES = 4 * 1024 * 1024
_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})')


def text(value: Any, maximum: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or not empty and not value:
        raise McpError('Invalid MCP content string')
    try:
        if len(value.encode()) > maximum:
            raise McpError('MCP content string exceeds its retention limit')
    except UnicodeError:
        raise McpError('Invalid MCP content string encoding') from None
    return value


def uri(value: Any) -> str:
    result = text(value, 8192, empty=False)
    if any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise McpError('Invalid MCP content URI')
    return result


def mime(value: Any, *, required: bool = False) -> str:
    result = text(value, 256, empty=not required)
    if any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise McpError('Invalid MCP content MIME type')
    return result


def binary(value: Any) -> str:
    result = text(value, 4 * ((MAX_BINARY_BYTES + 2) // 3))
    try:
        decoded = base64.b64decode(result, validate=True)
    except (ValueError, binascii.Error):
        raise McpError('Invalid MCP content binary encoding') from None
    if len(decoded) > MAX_BINARY_BYTES:
        raise McpError('MCP decoded content exceeds its retention limit')
    return result


def metadata(value: dict[str, Any]) -> None:
    if '_meta' in value and not isinstance(value['_meta'], dict):
        raise McpError('Invalid MCP content metadata')
    if 'annotations' not in value:
        return
    annotations = value['annotations']
    if not isinstance(annotations, dict):
        raise McpError('Invalid MCP content annotations')
    if 'audience' in annotations:
        audience = annotations['audience']
        if (not isinstance(audience, list) or len(audience) > 128
                or any(not isinstance(role, str) or role not in ('user', 'assistant') for role in audience)):
            raise McpError('Invalid MCP content annotation audience')
    if 'priority' in annotations:
        priority = annotations['priority']
        if type(priority) not in (int, float) or not 0 <= priority <= 1:
            raise McpError('Invalid MCP content annotation priority')
    if 'lastModified' in annotations:
        timestamp = text(annotations['lastModified'], 128, empty=False)
        try:
            if _TIME.fullmatch(timestamp) is None or datetime.fromisoformat(timestamp).tzinfo is None:
                raise ValueError
        except ValueError:
            raise McpError('Invalid MCP content annotation timestamp') from None


def resource(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise McpError('Invalid embedded MCP resource')
    json_value(value, maximum=MAX_FRAME)
    uri(value.get('uri'))
    mime(value.get('mimeType', ''))
    metadata(value)
    if ('text' in value) == ('blob' in value):
        raise McpError('Resource content must contain exactly one text/blob payload')
    if 'text' in value:
        text(value['text'], MAX_FRAME)
    else:
        binary(value['blob'])
    return value


def content(value: Any, *, image_projection: bool = True) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise McpError('Invalid MCP content block')
    # Snapshot the complete finite JSON, including opaque vendor metadata, before
    # typed projection. Unknown metadata is retained rather than coerced/dropped.
    encoded = json_value(value, maximum=MAX_FRAME)
    metadata(value)
    kind = value.get('type')
    if kind == 'text':
        text(value.get('text'), MAX_FRAME)
    elif kind in ('image', 'audio'):
        data = binary(value.get('data'))
        media = mime(value.get('mimeType'), required=True)
        if kind == 'image' and image_projection:
            try:
                decode_image(data, media, max_bytes=MAX_BINARY_BYTES)
            except ValueError:
                raise McpError("Invalid MCP image content") from None
    elif kind == 'resource':
        resource(value.get('resource'))
    elif kind == 'resource_link':
        uri(value.get('uri'))
        text(value.get('name'), 256, empty=False)
        for key, maximum in (('title', 8192), ('description', 16384)):
            if key in value:
                text(value[key], maximum)
        mime(value.get('mimeType', ''))
        if 'size' in value and (type(value['size']) is not int or not 0 <= value['size'] <= 2**53 - 1):
            raise McpError('Invalid MCP resource-link size')
    else:
        raise McpError('Unsupported MCP content block; content was not silently discarded')
    return cast(dict[str, Any], json.loads(encoded))
