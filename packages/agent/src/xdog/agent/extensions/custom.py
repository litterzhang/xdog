"""Bounded custom-view protocol; callback code never crosses into the UI process."""
from __future__ import annotations

import json
from typing import Any

from xdog.agent.mcp.schema import json_value
from xdog.agent.mcp.stdio import McpError


def answer(value: Any) -> Any:
    try:
        return json.loads(json_value(value, maximum=65536))
    except McpError:
        raise ValueError('Invalid/excessive custom-view answer') from None



_ANCHORS = frozenset({'center', 'top-left', 'top-right', 'bottom-left', 'bottom-right',
                      'top-center', 'bottom-center', 'left-center', 'right-center'})


def overlay_options(value: Any) -> dict[str, Any] | None:
    """Data-only placement/capture preference; never a callback or protected-owner override."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.keys() - {
            'width', 'min_width', 'max_height', 'anchor', 'offset_x', 'offset_y', 'row', 'col', 'margin',
            'non_capturing'}:
        raise ValueError('Invalid custom overlay placement fields')
    result = dict(value)
    if 'non_capturing' in value and type(value['non_capturing']) is not bool:
        raise ValueError('Invalid custom overlay capture policy')
    for key in ('width', 'max_height', 'row', 'col'):
        if key not in value:
            continue
        item = value[key]
        if type(item) is int and 0 <= item <= 10000 and (item > 0 or key in ('row', 'col')):
            continue
        if (isinstance(item, str) and len(item) <= 4 and item.endswith('%')
                and item[:-1].isascii() and item[:-1].isdecimal() and 0 <= int(item[:-1]) <= 100
                and (int(item[:-1]) > 0 or key in ('row', 'col'))):
            continue
        raise ValueError('Invalid custom overlay size/position')
    if 'min_width' in value and (type(value['min_width']) is not int or not 1 <= value['min_width'] <= 10000):
        raise ValueError('Invalid custom overlay minimum width')
    for key in ('offset_x', 'offset_y'):
        if key in value and (type(value[key]) is not int or not -10000 <= value[key] <= 10000):
            raise ValueError('Invalid custom overlay offset')
    anchor = value.get('anchor', 'center')
    if not isinstance(anchor, str) or anchor not in _ANCHORS:
        raise ValueError('Invalid custom overlay anchor')
    margin = value.get('margin', 0)
    if isinstance(margin, dict):
        if margin.keys() - {'top', 'right', 'bottom', 'left'} or any(
                type(item) is not int or not 0 <= item <= 10000 for item in margin.values()):
            raise ValueError('Invalid custom overlay margins')
        result['margin'] = dict(margin)
    elif type(margin) is not int or not 0 <= margin <= 10000:
        raise ValueError('Invalid custom overlay margin')
    return result


def options(value: dict[str, Any]) -> dict[str, Any]:
    fullscreen = value.get('fullscreen', False)
    keys = value.get('key_events', ['press'])
    if type(fullscreen) is not bool or not isinstance(keys, (list, tuple)) or not 1 <= len(keys) <= 3:
        raise ValueError('Invalid custom-view presentation/input options')
    if any(not isinstance(key, str) or key not in ('press', 'repeat', 'release') for key in keys):
        raise ValueError('Invalid custom-view key event kinds')
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate custom-view key event kinds')
    overlay = overlay_options(value.get('overlay'))
    if fullscreen and overlay is not None:
        raise ValueError('Custom views cannot request fullscreen and overlay together')
    live = value.get('live_overlay', False)
    if type(live) is not bool or live and overlay is None:
        raise ValueError('Live overlay controls require an overlay')
    result = {'fullscreen': fullscreen, 'key_events': list(keys)}
    if live or overlay is not None and overlay.get('non_capturing') is True:
        result['live_overlay'] = True
    if overlay is not None:
        result['overlay'] = overlay
    return result


def rich(value: dict[str, Any]) -> bool:
    return (value.get('overlay') is not None or value.get('fullscreen') is True
            or value.get('key_events', ['press']) != ['press']
            or bool(value.get('message', '')))

def event(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or value.get('kind') not in ('resize', 'key', 'paste')
            or type(value.get('width')) is not int or not 1 <= value['width'] <= 10000
            or type(value.get('height')) is not int or not 0 <= value['height'] <= 1000
            or type(value.get('painted')) is not int or value['painted'] < -1):
        raise ValueError('Invalid custom-view viewport/event')
    fields = {'kind', 'width', 'height', 'painted'}
    fields |= {'key', 'ctrl', 'alt', 'shift'} if value['kind'] == 'key' else (
        {'text'} if value['kind'] == 'paste' else set())
    if value['kind'] == 'key' and 'event_type' in value:
        if value['event_type'] not in ('press', 'repeat', 'release'):
            raise ValueError('Invalid custom-view key event type')
        fields.add('event_type')
    if 'generation' in value:
        if type(value['generation']) is not int or not 0 <= value['generation'] <= 2**53 - 1:
            raise ValueError('Invalid custom-view generation')
        fields.add('generation')
    if set(value) != fields:
        raise ValueError('Invalid custom-view event fields')
    result = dict(value)
    if value['kind'] == 'key':
        if (not isinstance(value.get('key'), str) or not 0 < len(value['key'].encode()) <= 128
                or any(type(value.get(key)) is not bool for key in ('ctrl', 'alt', 'shift'))
                or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value['key'])
                or value['key'].lower() == 'escape'
                or value['ctrl'] and value['key'].lower() in ('c', 'd', 'l', 'z')):
            raise ValueError('Reserved/invalid custom-view key')
    if value['kind'] == 'paste':
        if not isinstance(value.get('text'), str) or len(value['text'].encode()) > 16000:
            raise ValueError('Invalid custom-view paste')
    return result


def frame(value: Any, request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {'lines', 'cursor', 'done', 'value'}:
        raise ValueError('Invalid custom-view frame fields')
    lines = value.get('lines')
    if (not isinstance(lines, list) or len(lines) > 200
            or any(not isinstance(line, str) or '\n' in line or '\r' in line or len(line.encode()) > 8192
                   for line in lines)
            or sum(len(line.encode()) for line in lines) > 128 * 1024
            or type(value.get('done', False)) is not bool):
        raise ValueError('Invalid/excessive custom-view lines')
    cursor = value.get('cursor')
    if cursor is not None and (not isinstance(cursor, (list, tuple)) or len(cursor) != 2
            or any(type(item) is not int for item in cursor) or not 0 <= cursor[0] < min(len(lines), request['height'])
            or not 0 <= cursor[1] < request['width']):
        raise ValueError('Invalid custom-view cursor')
    result: dict[str, Any] = {'lines': list(lines), 'cursor': list(cursor) if cursor is not None else None,
                              'done': value.get('done', False)}
    if result['done']:
        if 'value' not in value or request['kind'] == 'resize':
            raise ValueError('Custom completion requires explicit input and value')
        result['value'] = answer(value['value'])
    return result
