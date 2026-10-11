"""Secret-free selected-model and last-response usage snapshots; no model calls."""
from __future__ import annotations

import json
import math
from typing import Any, cast

from xdog.agent.agent import Agent
from xdog.ai.types import AssistantMessage, Model

_EFFORTS = ('off', 'minimal', 'low', 'medium', 'high', 'xhigh')
_COMPONENTS = ('input', 'output', 'cache_read', 'cache_write')


def _count(value: Any, maximum: int = 2**53 - 1) -> bool:
    return type(value) is int and 0 <= value <= maximum


def validate(operation: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get('session_id'), str) or len(value['session_id']) > 256:
        raise ValueError('Invalid session inspection identity')
    if operation == 'model':
        fields = {'session_id', 'id', 'name', 'provider', 'context_window', 'max_prompt_tokens', 'max_tokens',
                  'reasoning', 'thinking', 'supports_tool_calls', 'input', 'supported_efforts', 'vision_limits'}
        if set(value) != fields:
            raise ValueError('Invalid model inspection fields')
        for key in ('id', 'name', 'provider'):
            if not isinstance(value[key], str) or len(value[key].encode()) > 512:
                raise ValueError('Invalid public model inspection string')
        for key in ('reasoning', 'supports_tool_calls'):
            if value[key] is not None and type(value[key]) is not bool:
                raise ValueError('Invalid model inspection capability')
        vision = value['vision_limits']
        if vision is not None:
            if (not isinstance(vision, dict) or set(vision) != {
                    'max_prompt_images', 'max_prompt_image_size', 'supported_media_types'}
                    or not _count(vision['max_prompt_images'], 1024)
                    or not _count(vision['max_prompt_image_size'], 1024 * 1024 * 1024)
                    or not isinstance(vision['supported_media_types'], list)
                    or len(vision['supported_media_types']) > 32
                    or any(not isinstance(kind, str) or len(kind.encode()) > 256
                           for kind in vision['supported_media_types'])):
                raise ValueError('Invalid inspected vision capabilities')
        if value['thinking'] not in _EFFORTS:
            raise ValueError('Invalid inspected thinking level')
        for key, allowed, maximum in (('input', ('text', 'image'), 2), ('supported_efforts', _EFFORTS[1:], 5)):
            rows = value[key]
            if rows is None:
                continue
            if (not isinstance(rows, list) or len(rows) > maximum
                    or any(not isinstance(item, str) or len(item) > 32
                           or allowed and item not in allowed for item in rows)
                    or len(rows) != len(set(rows))):
                raise ValueError('Invalid inspected model capability list')
    elif operation == 'usage':
        fields = {'session_id', 'model', 'context_window', 'max_prompt_tokens', 'context_limit', 'message_count',
                  'tokens', 'percent', 'source', 'messages_since_usage', 'components'}
        if set(value) != fields or not isinstance(value['model'], str) or len(value['model'].encode()) > 512:
            raise ValueError('Invalid context inspection fields')
        for key in ('message_count', 'context_limit'):
            if not _count(value[key]):
                raise ValueError('Invalid context inspection count')
        if value['source'] == 'unavailable':
            if any(value[key] is not None for key in ('tokens', 'percent', 'messages_since_usage', 'components')):
                raise ValueError('Unavailable context usage cannot claim measured tokens')
        elif value['source'] == 'last-response':
            counters = value['components']
            if (not isinstance(counters, dict) or set(counters) != set(_COMPONENTS)
                    or any(not _count(counter) for counter in counters.values())
                    or not _count(value['tokens']) or value['tokens'] != sum(counters.values())
                    or not _count(value['messages_since_usage'])
                    or value['messages_since_usage'] >= value['message_count']):
                raise ValueError('Invalid measured context usage')
            percent = value['percent']
            if value['context_limit']:
                expected = 100 * value['tokens'] / value['context_limit']
                if type(percent) not in (int, float) or percent != expected or not math.isfinite(percent):
                    raise ValueError('Invalid measured context percentage')
            elif percent is not None:
                raise ValueError('Unknown context limit cannot claim a percentage')
        else:
            raise ValueError('Invalid context inspection source')
    else:
        raise ValueError('Unsupported session inspection')
    for key in ('context_window', 'max_prompt_tokens', *(['max_tokens'] if operation == 'model' else [])):
        if not _count(value[key], 2**31):
            raise ValueError('Invalid effective model limit')
    if (value['max_prompt_tokens'] > value['context_window'] and value['context_window']
            or operation == 'usage' and value['context_limit'] != (
                min(value['context_window'], value['max_prompt_tokens']) if value['max_prompt_tokens']
                and value['context_window'] else value['max_prompt_tokens'] or value['context_window'])):
        raise ValueError('Inconsistent effective context limits')
    # Return a fresh finite snapshot, never a live provider/model dictionary.
    from xdog.agent.mcp.schema import json_value
    return dict(json.loads(json_value(value, maximum=16384)))


def snapshot(agent: Agent, session_id: str, operation: str, model: Model | None = None,
             *, context_window: int = 0, max_prompt_tokens: int = 0, usage_floor: int = 0) -> dict[str, Any]:
    state = agent.state
    if model is not None and model.id != state.model:
        model = None  # Stale caller metadata is not the selected model's capability.
    window = agent.context_window or context_window
    prompt = agent.max_prompt_tokens or max_prompt_tokens
    if operation == 'model':
        return validate(operation, {'session_id': session_id, 'id': state.model,
            'name': model.name if model else state.model, 'provider': model.provider if model else '',
            'context_window': window, 'max_prompt_tokens': prompt, 'max_tokens': model.max_tokens if model else 0,
            'reasoning': model.reasoning if model else None, 'thinking': agent.options.thinking or 'off',
            'supports_tool_calls': model.supports_tool_calls if model else None,
            'input': list(model.input) if model and model.input is not None else None,
            'supported_efforts': (list(model.supported_efforts)
                                 if model and model.supported_efforts is not None else None),
            'vision_limits': ({'max_prompt_images': model.vision_limits.max_prompt_images,
                'max_prompt_image_size': model.vision_limits.max_prompt_image_size,
                'supported_media_types': list(model.vision_limits.supported_media_types)}
                if model and model.vision_limits is not None else None)})
    limit = min(window, prompt) if window and prompt else window or prompt
    result: dict[str, Any] = {'session_id': session_id, 'model': state.model,
        'context_window': window, 'max_prompt_tokens': prompt, 'context_limit': limit,
        'message_count': len(state.messages), 'tokens': None, 'percent': None, 'source': 'unavailable',
        'messages_since_usage': None, 'components': None}
    if not _count(usage_floor) or usage_floor > len(state.messages):
        raise ValueError("Invalid context measurement ownership boundary")
    for offset, message in enumerate(reversed(state.messages[usage_floor:])):
        if not isinstance(message, AssistantMessage) or message.model != state.model:
            continue
        counters = {key: getattr(message.usage, key, None) for key in _COMPONENTS}
        if any(not _count(counter) for counter in counters.values()):
            continue
        measured = cast(dict[str, int], counters)
        tokens = sum(measured.values())
        if not 0 < tokens <= 2**53 - 1:
            continue
        result.update(tokens=tokens, percent=100 * tokens / limit if limit else None, source='last-response',
                      messages_since_usage=offset, components=measured)
        break
    return validate(operation, result)


def envelope(value: Any, *, session_id: str, owner_id: str) -> dict[str, Any] | None:
    """Validate an invocation snapshot under its actual host session/child identity."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'session_id', 'owner_id', 'model', 'usage'}
            or value['session_id'] != session_id or value['owner_id'] != owner_id):
        raise ValueError('Inspection invocation owner changed')
    model = validate('model', value['model'])
    usage = validate('usage', value['usage'])
    if (model['session_id'] != session_id or usage['session_id'] != session_id
            or model['id'] != usage['model'] or model['context_window'] != usage['context_window']
            or model['max_prompt_tokens'] != usage['max_prompt_tokens']):
        raise ValueError('Inspection invocation snapshot is inconsistent')
    return {'session_id': session_id, 'owner_id': owner_id, 'model': model, 'usage': usage}


def capture(agent: Agent, session_id: str, owner_id: str, model: Model | None = None,
            *, context_window: int = 0, max_prompt_tokens: int = 0, usage_floor: int = 0) -> dict[str, Any]:
    data = {'session_id': session_id, 'owner_id': owner_id,
        'model': snapshot(agent, session_id, 'model', model, context_window=context_window,
                          max_prompt_tokens=max_prompt_tokens),
        'usage': snapshot(agent, session_id, 'usage', model, context_window=context_window,
                          max_prompt_tokens=max_prompt_tokens, usage_floor=usage_floor)}
    result = envelope(data, session_id=session_id, owner_id=owner_id)
    assert result is not None
    return result
