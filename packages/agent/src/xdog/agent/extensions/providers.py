"""Session-owned provider metadata and bounded typed stream events; no global registry mutation."""
from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from dataclasses import asdict, replace
from typing import Any, cast

from xdog.agent.core import CatalogStreamFn, StreamFn
from xdog.agent.mcp.schema import json_value
from xdog.agent.mcp.stdio import McpError
from xdog.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Model,
    ModelCost,
    StartEvent,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    TextDoneEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingDoneEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolCallDoneEvent,
    ToolCallStartEvent,
    Usage,
    UsageEvent,
    VisionLimits,
)
from xdog.ai.utils.event_stream import EventStream

_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,31}')
_MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}')
_EFFORTS = frozenset({'minimal', 'low', 'medium', 'high', 'xhigh'})
_MEDIA = re.compile(r'image/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,63}')


def registration(name: Any, models: Any, *, allow_empty: bool = False) -> dict[str, Any]:
    if (not isinstance(name, str) or _NAME.fullmatch(name) is None or not isinstance(models, list)
            or type(allow_empty) is not bool or not (0 if allow_empty else 1) <= len(models) <= 32):
        raise ValueError('Invalid provider registration/model capacity')
    rows, ids = [], set()
    for row in models:
        if (not isinstance(row, dict) or set(row) - {'id', 'name', 'context_window', 'max_tokens', 'reasoning',
                'input', 'supports_tool_calls', 'cost', 'max_prompt_tokens', 'supported_efforts', 'vision_limits'}
                or not isinstance(row.get('id'), str)
                or _MODEL_ID.fullmatch(row['id']) is None or row['id'] in ids
                or not isinstance(row.get('name', row['id']), str)
                or len(str(row.get('name', row['id'])).encode()) > 128):
            raise ValueError('Invalid/duplicate provider model identity')
        for key in ('context_window', 'max_tokens'):
            value = row.get(key, 0)
            if type(value) is not int or not 1 <= value <= 2**31:
                raise ValueError('Provider models require explicit bounded context/output limits')
        for key in ('reasoning', 'supports_tool_calls'):
            if type(row.get(key, False if key == 'reasoning' else True)) is not bool:
                raise ValueError('Invalid provider model capability')
        modalities = row.get('input', ['text'])
        if (not isinstance(modalities, list) or not 1 <= len(modalities) <= 2
                or any(not isinstance(item, str) or item not in ('text', 'image') for item in modalities)
                or len(set(modalities)) != len(modalities)):
            raise ValueError('Invalid provider input modalities')
        prompt = row.get('max_prompt_tokens', 0)
        if type(prompt) is not int or not 0 <= prompt <= row['context_window']:
            raise ValueError('Invalid provider prompt limit')
        efforts = row.get('supported_efforts')
        if efforts is not None and (not isinstance(efforts, list) or len(efforts) > 5
                or any(not isinstance(level, str) or level not in _EFFORTS for level in efforts)
                or len(set(efforts)) != len(efforts) or efforts and not row.get('reasoning', False)):
            raise ValueError('Invalid provider reasoning effort capabilities')
        vision = row.get('vision_limits')
        if vision is not None:
            if (not isinstance(vision, dict) or vision.keys() - {
                    'max_prompt_images', 'max_prompt_image_size', 'supported_media_types'}
                    or 'image' not in modalities):
                raise ValueError('Invalid provider vision capabilities')
            for key, maximum in (('max_prompt_images', 1024), ('max_prompt_image_size', 1024 * 1024 * 1024)):
                if type(vision.get(key, 0)) is not int or not 0 <= vision.get(key, 0) <= maximum:
                    raise ValueError('Invalid provider vision limit')
            media = vision.get('supported_media_types', [])
            if (not isinstance(media, list) or len(media) > 32 or any(
                    not isinstance(kind, str) or _MEDIA.fullmatch(kind) is None for kind in media)
                    or len(set(media)) != len(media)):
                raise ValueError('Invalid provider vision MIME capabilities')
            vision = {'max_prompt_images': vision.get('max_prompt_images', 0),
                      'max_prompt_image_size': vision.get('max_prompt_image_size', 0),
                      'supported_media_types': list(media)}
        cost = row.get('cost', {})
        if (not isinstance(cost, dict) or cost.keys() - {'input', 'output', 'cache_read', 'cache_write'}
                or any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e6
                       for value in cost.values())):
            raise ValueError('Invalid provider pricing metadata')
        ids.add(row['id'])
        rows.append({**row, 'name': row.get('name', row['id']), 'input': list(modalities), 'cost': dict(cost),
                     'max_prompt_tokens': prompt, 'supported_efforts': list(efforts) if efforts is not None else None,
                     'vision_limits': vision,
                     'reasoning': row.get('reasoning', False),
                     'supports_tool_calls': row.get('supports_tool_calls', True)})
    return {'name': name.lower(), 'models': rows}


def provider_catalog(value: Any, *, live: bool = False) -> list[dict[str, Any]]:
    """Validate one worker's bounded declarations, including optional legacy identities."""
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError('Invalid extension provider registration capacity')
    rows, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or item.keys() - {'name', 'models', 'registration_id', 'oauth'}:
            raise ValueError('Invalid extension provider declaration')
        row = registration(item.get('name'), item.get('models'), allow_empty='oauth' in item)
        if row['name'] in seen:
            raise ValueError('Duplicate extension provider declaration')
        if 'oauth' in item:
            from xdog.agent.extensions.provider_oauth import public_oauth
            row['oauth'] = public_oauth(item['oauth'])
        token = item.get('registration_id')
        if token is not None or live:
            if not isinstance(token, str) or re.fullmatch(r'[a-f0-9]{32}', token) is None:
                raise ValueError('Invalid extension provider registration identity')
            row['registration_id'] = token
        seen.add(row['name'])
        rows.append(row)
    return rows


def wire_event(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError('Provider stream event must be typed JSON data')
    try:
        value = json.loads(json_value(value, maximum=65536))
    except McpError:
        raise ValueError('Invalid provider event JSON') from None
    kind = value.get('type')
    fields = {'text': {'type', 'text', 'signature', 'new_block'},
              'thinking': {'type', 'text', 'signature', 'new_block'},
              'tool_call': {'type', 'id', 'name', 'arguments', 'signature'},
              'usage': {'type', 'input', 'output', 'cache_read', 'cache_write', 'total_tokens'},
              'done': {'type', 'stop_reason', 'response_id'},
              'error': {'type', 'message', 'stop_reason', 'response_id'}}
    if not isinstance(kind, str) or kind not in fields or value.keys() - fields[kind]:
        raise ValueError('Unknown provider event type/fields')
    if kind in ('text', 'thinking'):
        if (not isinstance(value.get('text'), str) or len(value['text'].encode()) > 32768
                or type(value.get('new_block', False)) is not bool):
            raise ValueError('Invalid provider text/thinking event')
    if kind in ('text', 'thinking', 'tool_call') and 'signature' in value:
        if not isinstance(value['signature'], str) or len(value['signature'].encode()) > 8192:
            raise ValueError('Invalid provider continuity signature')
    if kind == 'tool_call':
        if (not isinstance(value.get('id'), str) or not 0 < len(value['id']) <= 128
                or not isinstance(value.get('name'), str) or not 0 < len(value['name']) <= 128
                or not isinstance(value.get('arguments'), dict)):
            raise ValueError('Invalid provider tool-call event')
    if kind == 'usage':
        if any(type(value.get(key, 0)) is not int or not 0 <= value.get(key, 0) <= 2**53
               for key in fields[kind] - {'type'}):
            raise ValueError('Invalid provider usage counter')
    if kind == 'error':
        if (not isinstance(value.get('message'), str) or not value['message'].strip()
                or len(value['message'].encode()) > 8192
                or value.get('stop_reason', 'error') not in ('error', 'aborted')):
            raise ValueError('Invalid public provider error/cancellation event')
    if kind in ('done', 'error') and value.get('response_id') is not None:
        if not isinstance(value['response_id'], str) or len(value['response_id']) > 512:
            raise ValueError('Invalid provider response identity')
    if kind == 'done' and value.get('stop_reason', 'stop') not in ('stop', 'length', 'toolUse'):
        raise ValueError('Invalid provider completion')
    return cast(dict[str, Any], value)


class _ProviderCancelled(Exception):
    """An explicit caller cancel event, distinct from task cancellation/worker failure."""


async def _cancelable_events(source: AsyncGenerator[dict[str, Any], None],
                             cancel: asyncio.Event | None) -> AsyncGenerator[dict[str, Any], None]:
    async with aclosing(source):
        if cancel is None:
            async for event in source:
                yield event
            return
        waiter = asyncio.create_task(cancel.wait())
        pending: asyncio.Future[dict[str, Any]] | None = None
        try:
            while True:
                if cancel.is_set():
                    raise _ProviderCancelled
                pending = asyncio.ensure_future(anext(source))
                await asyncio.wait((pending, waiter), return_when=asyncio.FIRST_COMPLETED)
                if cancel.is_set():
                    raise _ProviderCancelled
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    return
                yield event
        finally:
            # Settle the interrupted RPC before closing its generator. The runtime
            # quarantines uncertain worker calls; no detached read or retry survives.
            tasks = [waiter] + ([pending] if pending is not None else [])
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


class ExtensionProviderStream:
    """Wrap one session's actual base adapter; explicit extension aliases never fall back."""
    def __init__(self, runtime: Any, base: StreamFn) -> None:
        self.runtime, self.base = runtime, base

    def models(self) -> tuple[Model, ...]:
        native = (self.base.models() if isinstance(self.base, CatalogStreamFn)
                  and callable(getattr(self.base, 'models', None)) else ())
        if (not isinstance(native, (list, tuple)) or len(native) > 512
                or any(not isinstance(model, Model) or model.id.startswith('ext-') for model in native)):
            raise ValueError('Invalid/oversized native catalog or reserved extension namespace')
        return (*native, *self.runtime.provider_models())

    def resolve_model(self, name: str) -> Model | None:
        if name.startswith('ext-'):
            return cast(Model | None, self.runtime.provider_model(name))
        resolver = getattr(self.base, 'resolve_model', None)
        return resolver(name) if callable(resolver) else None

    def __call__(self, model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
        if not model.startswith('ext-'):
            return self.base(model, context, options)
        stream: EventStream[AssistantMessage]
        async def events() -> AsyncIterator[AssistantMessageEvent]:
            message = AssistantMessage(model=model, provider=model.split('/')[0], api='extension-callback')
            yield StartEvent(partial=message)
            try:
                selected = self.runtime.provider_model(model)
                if selected is None:
                    raise ValueError('Extension provider model is no longer active')
                if (options.thinking is not None and selected.reasoning and selected.supported_efforts is not None
                        and options.thinking not in selected.supported_efforts):
                    raise ValueError('Selected extension model does not advertise the requested reasoning effort')
                if options.cancel is not None and options.cancel.is_set():
                    raise _ProviderCancelled
                if options.max_tokens is not None and (type(options.max_tokens) is not int
                                                       or options.max_tokens < 1):
                    raise ValueError('Provider output token limit must be a positive integer')
                from xdog.ai.utils.image_files import decode_image
                images = tuple(part for entry in context.messages
                               for part in (() if isinstance(entry.content, str) else entry.content)
                               if isinstance(part, ImageContent))
                if images and selected.supports_image_input is not True:
                    raise ValueError('Selected extension model does not support the actual image context')
                limits = selected.vision_limits
                if len(images) > (limits.max_prompt_images if limits and limits.max_prompt_images else 1024):
                    raise ValueError('Actual image context exceeds selected provider limits')
                for image in images:
                    if limits and limits.supported_media_types and image.mime_type not in limits.supported_media_types:
                        raise ValueError('Actual image context has an unsupported provider MIME type')
                    # Binary admission does not become optional when model metadata
                    # omits limits. This is base64/header validation, not a decoder.
                    await asyncio.to_thread(decode_image, image.data, image.mime_type,
                        max_bytes=min((limits.max_prompt_image_size if limits else 0) or 10 * 1024 * 1024,
                                      10 * 1024 * 1024))
                maximum = min(selected.max_tokens, options.max_tokens or selected.max_tokens)
                effective = replace(options, cancel=None, max_tokens=maximum,
                                    thinking=options.thinking if selected.reasoning else None)
                offered = context if selected.supports_tool_calls is not False else replace(context, tools=None)
                request = {'context': asdict(offered), 'options': asdict(effective)}
                count, size = 0, 0
                async with aclosing(_cancelable_events(self.runtime.provider_events(model, request),
                                                       options.cancel)) as source:
                    async for raw in source:
                        event = wire_event(raw)
                        count += 1
                        size += len(json.dumps(event).encode())
                        if count > 4096 or size > 8 * 1024 * 1024:
                            raise ValueError('Provider stream exceeds bounded event/content budget')
                        kind, index = event['type'], len(message.content)
                        if kind == 'text':
                            previous = message.content[-1] if message.content else None
                            continuing = isinstance(previous, TextContent) and not event.get('new_block', False)
                            if continuing and isinstance(previous, TextContent):
                                index -= 1
                                part = TextContent(text=previous.text + event['text'],
                                                   text_signature=event.get('signature', previous.text_signature))
                                message = replace(message, content=(*message.content[:-1], part))
                            else:
                                part = TextContent(text=event['text'], text_signature=event.get('signature'))
                                message = replace(message, content=(*message.content, part))
                                yield TextStartEvent(index=index, partial=message)
                            yield TextDeltaEvent(index=index, delta=event['text'], partial=message)
                            yield TextDoneEvent(index=index, text=part.text, text_signature=part.text_signature,
                                                partial=message)
                        elif kind == 'thinking':
                            previous = message.content[-1] if message.content else None
                            continuing = isinstance(previous, ThinkingContent) and not event.get('new_block', False)
                            if continuing and isinstance(previous, ThinkingContent):
                                index -= 1
                                thought = ThinkingContent(thinking=previous.thinking + event['text'],
                                    thinking_signature=event.get('signature', previous.thinking_signature))
                                message = replace(message, content=(*message.content[:-1], thought))
                            else:
                                thought = ThinkingContent(thinking=event['text'],
                                                      thinking_signature=event.get('signature'))
                                message = replace(message, content=(*message.content, thought))
                                yield ThinkingStartEvent(index=index, partial=message)
                            yield ThinkingDeltaEvent(index=index, delta=event['text'], partial=message)
                            yield ThinkingDoneEvent(index=index, thinking=thought.thinking,
                                                    thinking_signature=thought.thinking_signature, partial=message)
                        elif kind == 'tool_call':
                            if (offered.tools is None or not any(tool.name == event['name'] for tool in offered.tools)):
                                raise ValueError('Provider tool call was not offered by the actual host context')
                            if any(isinstance(part, ToolCall) and part.id == event['id'] for part in message.content):
                                raise ValueError('Duplicate provider tool-call ID')
                            tool = ToolCall(id=event['id'], name=event['name'], arguments=event['arguments'],
                                            thought_signature=event.get('signature'))
                            message = replace(message, content=(*message.content, tool))
                            yield ToolCallStartEvent(index=index, id=tool.id, name=tool.name, partial=message)
                            yield ToolCallDoneEvent(index=index, id=tool.id, name=tool.name, arguments=tool.arguments,
                                                    thought_signature=tool.thought_signature, partial=message)
                        elif kind == 'usage':
                            message = replace(message, usage=Usage(**{key: value for key, value in event.items()
                                                                      if key != 'type'}))
                            yield UsageEvent(usage=message.usage)
                        else:
                            failure = kind == 'error'
                            reason = cast(Any, event.get('stop_reason', 'error' if failure else 'stop'))
                            message = replace(message, stop_reason=reason, response_id=event.get('response_id'),
                                              error_message=event['message'] if failure else None)
                            await source.aclose()
                            stream.set_result(message)
                            if failure:
                                yield ErrorEvent(error=event['message'], stop_reason=reason, message=message)
                            else:
                                yield DoneEvent(stop_reason=reason, message=message)
                            return
                raise ValueError('Provider stream ended without explicit completion')
            except _ProviderCancelled:
                message = replace(message, stop_reason='aborted', error_message='Extension provider canceled')
                stream.set_result(message)
                yield ErrorEvent(error=message.error_message or '', stop_reason='aborted', message=message)
            except asyncio.CancelledError:
                message = replace(message, stop_reason='aborted', error_message='Extension provider canceled')
                stream.set_result(message)
                raise
            except Exception:
                message = replace(message, stop_reason='error',
                                  error_message='Extension provider failed; no fallback/replay')
                stream.set_result(message)
                yield ErrorEvent(error=message.error_message or '', message=message)
        stream = EventStream.from_async_generator(events())
        return stream


def model_from_registration(extension: str, provider: dict[str, Any], row: dict[str, Any]) -> Model:
    namespace = 'ext-' + extension + '-' + provider['name']
    return Model(id=namespace + '/' + row['id'], provider=namespace, api='extension-callback', name=row['name'],
        context_window=row['context_window'], max_tokens=row['max_tokens'], reasoning=row['reasoning'],
        input=tuple(row['input']), cost=ModelCost(**row['cost']), supports_tool_calls=row['supports_tool_calls'],
        max_prompt_tokens=row.get('max_prompt_tokens', 0),
        supported_efforts=tuple(row['supported_efforts']) if row.get('supported_efforts') is not None else None,
        vision_limits=(VisionLimits(max_prompt_images=row['vision_limits']['max_prompt_images'],
            max_prompt_image_size=row['vision_limits']['max_prompt_image_size'],
            supported_media_types=tuple(row['vision_limits']['supported_media_types']))
            if row.get('vision_limits') is not None else None))
