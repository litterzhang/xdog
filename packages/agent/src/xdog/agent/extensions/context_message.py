"""Reviewed, non-triggering extension context for the next explicit user prompt.

Canonical messages stay custom. Only validated content is projected to the LLM;
opaque extension details are never interpreted as user/model instructions.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from typing import Any

from xdog.agent.core import AgentMessage, CustomAgentMessage
from xdog.agent.extensions.invocation import current_invocation
from xdog.agent.extensions.queued_input import MAX_PROPOSAL_BYTES, _images
from xdog.agent.mcp.schema import json_value
from xdog.ai.types import ImageContent, TextContent, UserMessage

TYPE = 'xdog.extension_context.v1'
METHODS = frozenset({'xdog/command', 'tools/call', 'xdog/event', 'xdog/pre', 'xdog/post', 'xdog/input'})
_COMMON = {'id', 'kind', 'operation', 'title', 'message', 'timeout', 'extension', 'parent_id'}
_FIELDS = {'custom_type', 'content', 'display', 'details', 'delivery', 'image_summaries', 'trigger_turn'}


def content(value: Any, *, projected: bool = False) -> list[dict[str, Any]]:
    if isinstance(value, str):
        value = [{'type': 'text', 'text': value}]
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError('Custom context requires text or a bounded typed content array')
    result, total, images = [], 0, []
    for part in value:
        if not isinstance(part, dict):
            raise ValueError('Invalid custom context part')
        if part.get('type') == 'text' and set(part) == {'type', 'text'}:
            text = part['text']
            if not isinstance(text, str) or '\x00' in text:
                raise ValueError('Invalid custom context text')
            total += len(text.encode())
            result.append(dict(part))
        elif part.get('type') == 'image' and not projected:
            images.append(part)
            result.append(dict(part))
        else:
            raise ValueError('Only text and inline typed images are custom context content')
    if total > 16384 or not any(part.get('text', '').strip() or part['type'] == 'image' for part in result):
        raise ValueError('Custom context text must be nonempty and at most 16 KiB')
    _images(images)
    return result


def proposal(custom_type: str, value: str | tuple[TextContent | ImageContent, ...], *,
             display: bool = True, details: Any = None, delivery: str = 'next_turn',
             trigger_turn: bool = False) -> dict[str, Any]:
    if type(trigger_turn) is not bool:
        raise ValueError('Custom trigger_turn must be an explicit boolean')
    if not isinstance(value, str):
        if not isinstance(value, tuple) or any(type(part) not in (TextContent, ImageContent) for part in value):
            raise ValueError('Custom context content must contain typed text/image values')
        value = [({'type': 'text', 'text': part.text} if isinstance(part, TextContent) else asdict(part))
                 for part in value]  # type: ignore[assignment]
    result = {'custom_type': custom_type, 'content': value, 'display': display, 'details': details,
              'delivery': delivery, 'trigger_turn': trigger_turn if delivery != 'next_turn' else False}
    return validate(result)


def validate(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() - (_COMMON | _FIELDS):
        raise ValueError('Custom context cannot select another session, branch, child or invocation')
    kind = value.get('custom_type')
    if not isinstance(kind, str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', kind) is None:
        raise ValueError('Invalid custom context type')
    if (type(value.get('display')) is not bool or value.get('delivery') not in ('next_turn', 'steer', 'follow_up')
            or type(value.get('trigger_turn', False)) is not bool):
        raise ValueError('Invalid custom context delivery/trigger policy')
    details = json.loads(json_value(value.get('details'), maximum=32768))
    projected = 'image_summaries' in value
    body = content(value.get('content'), projected=projected)
    if projected:
        from xdog.agent.extensions.queued_input import _summaries
        _summaries(value['image_summaries'])
    result = {**value, 'content': body, 'details': details}
    if len(json.dumps(result, ensure_ascii=True).encode()) > MAX_PROPOSAL_BYTES:
        raise ValueError('Custom context exceeds frame capacity')
    return result


def record(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) - {'trigger_turn'} != {
            'id', 'extension', 'session_id', 'branch_id', 'invocation', 'method',
            'custom_type', 'content', 'display', 'details', 'delivery'}):
        raise ValueError('Invalid canonical custom context record')
    for key in ('id', 'invocation'):
        if not isinstance(value[key], str) or re.fullmatch('[a-f0-9]{32}', value[key]) is None:
            raise ValueError('Invalid custom context provenance')
    if (not isinstance(value['extension'], str)
            or re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,31}', value['extension']) is None
            or value['method'] not in METHODS):
        raise ValueError('Invalid custom context origin')
    for key in ('session_id', 'branch_id'):
        if (not isinstance(value[key], str) or len(value[key]) > 256
                or any(ord(char) < 32 for char in value[key]) or key == 'session_id' and not value[key]):
            raise ValueError('Invalid custom context owner')
    clean = validate({key: value[key] for key in _FIELDS - {'image_summaries'} if key in value})
    return {**value, **clean}


def llm_message(message: AgentMessage) -> UserMessage | None:
    if not isinstance(message, CustomAgentMessage) or message.type != TYPE:
        return None
    data = record(message.data)
    parts: list[TextContent | ImageContent] = [TextContent(text=(
        f'[User-reviewed extension context: {data["extension"]} / {data["custom_type"]}; '
        'not a user-authored message or a tool permission]\n'))]
    for part in data['content']:
        parts.append(TextContent(text=part['text']) if part['type'] == 'text'
                     else ImageContent(data=part['data'], mime_type=part['mime_type']))
    return UserMessage(content=tuple(parts))


def images(messages: tuple[AgentMessage, ...]) -> tuple[ImageContent, ...]:
    return tuple(ImageContent(data=part['data'], mime_type=part['mime_type'])
                 for message in messages if isinstance(message, CustomAgentMessage) and message.type == TYPE
                 for part in record(message.data)['content'] if part['type'] == 'image')


def summary_text(message: AgentMessage) -> str:
    """Content/provenance only, independent of UI display; never raw metadata/image bytes."""
    projected = llm_message(message)
    if projected is None:
        return ''
    parts = projected.content
    assert not isinstance(parts, str)
    text = '\n'.join(part.text for part in parts if isinstance(part, TextContent))
    count = sum(isinstance(part, ImageContent) for part in parts)
    return text + (f'\n[{count} canonical image(s); original bytes retained in archived source]' if count else '')


def display_text(data: dict[str, Any]) -> str:
    data = record(data)
    if not data['display']:
        return ''
    text = '\n'.join(part['text'] for part in data['content'] if part['type'] == 'text')
    images = sum(part['type'] == 'image' for part in data['content'])
    return f'Extension context · {data["extension"]} / {data["custom_type"]}\n' + text + (
        f'\n[{images} retained image(s)]' if images else '')


async def reviewed(extension: str, dialog: dict[str, Any], *, ask: Any, enqueue: Any,
                   scope: tuple[str, str], alive: Any, schedule: Any = None) -> bool:
    lease = current_invocation()
    if (lease is None or lease.extension != extension or lease.method not in METHODS or lease.child_id
            or lease.session_id != scope[0] or not alive()
            or dialog.get('kind') != 'session' or dialog.get('operation') != 'context_message'):
        return False
    if 'image_summaries' in dialog:
        raise ValueError('Worker cannot forge custom context review projection')
    value = validate(dialog)
    images = [part for part in value['content'] if part['type'] == 'image']
    _, summaries = _images(images)
    body = [part for part in value['content'] if part['type'] == 'text']
    # A purely image proposal still has an explicit, inert text description.
    projection = {**value, 'content': body or [{'type': 'text', 'text': '[Inline image context]'}],
                  'image_summaries': summaries}
    approved = await ask(extension, projection)
    if approved is not True or not alive() or not lease.owner_alive():
        return False
    canonical = record({key: value[key] for key in _FIELDS - {'image_summaries'} if key in value} | {
        'id': uuid.uuid4().hex, 'extension': extension, 'session_id': scope[0], 'branch_id': scope[1],
        'invocation': lease.id, 'method': lease.method})
    committed = False
    async def commit() -> None:
        nonlocal committed
        if committed:
            return
        committed = True  # Consume before awaiting: no replay after canceled/uncertain persistence.
        if alive() and lease.owner_alive():
            await enqueue(canonical)
    async def after_success() -> None:
        if not alive() or not lease.owner_alive():
            return
        if schedule is None:
            await commit()
        else:
            await schedule(commit)
    return lease.defer(after_success)
