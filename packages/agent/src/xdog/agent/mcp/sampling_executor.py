"""Independent sampling on the host's selected adapter, never a second tool loop.

Tool definitions are proposals only: generated tool calls are returned for output
review, never executed here. Additional host context requires an explicit scoped
resolver supplied by the host; no global provider or parent-history fallback.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.core import CatalogStreamFn, ModelStreamFn, StreamFn
from xdog.agent.image_context import validate_image_context
from xdog.agent.mcp.client_capabilities import task_augmented
from xdog.agent.mcp.sampling import request, result
from xdog.agent.mcp.schema import json_value
from xdog.agent.mcp.stdio import McpError
from xdog.agent.supervision_transfer import settled_image_work
from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    Model,
    StreamOptions,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolChoice,
    ToolResultMessage,
    UserMessage,
)

ContextResolver = Callable[[str], Awaitable[tuple[Message, ...]]]


def model_catalog(stream: StreamFn, current: str) -> tuple[tuple[str, ...], tuple[Model, ...]]:
    """Same-adapter public IDs plus private pinned metadata, without global lookup."""
    if not isinstance(current, str) or not current or len(current.encode()) > 256:
        raise McpError('Invalid sampling current model identity')
    if not isinstance(stream, CatalogStreamFn):
        return (current,), ()  # Existing opaque adapters expose only their explicit configured ID.
    values = stream.models()
    if (not isinstance(values, (tuple, list)) or len(values) > 576
            or any(not isinstance(value, Model) for value in values)
            or len({value.id for value in values}) != len(values)):
        raise McpError('Invalid/ambiguous sampling same-adapter model catalog')
    models = tuple(value for value in values if value.model_type == 'chat' and value.supports_streaming)
    # Session adapters wrap opaque native streams to expose extension aliases.
    # An absent base catalog must not fabricate one, but the explicitly configured
    # native ID remains usable on that same opaque stream. A real advertised base
    # catalog withdrawing the current ID still fails closed.
    from xdog.agent.extensions.providers import ExtensionProviderStream
    if (isinstance(stream, ExtensionProviderStream) and not current.startswith('ext-')
            and not isinstance(stream.base, CatalogStreamFn) and current not in {value.id for value in models}):
        selected = stream.resolve_model(current)
        if selected is not None:
            if not isinstance(selected, Model) or selected.id != current:
                raise McpError('Sampling opaque configured resolver identity mismatch')
            models = (*models, selected)
        else:
            names = tuple(value.id for value in models) + (current,)
            return names, copy.deepcopy(models)
    if (not models or any(not value.id or len(value.id.encode()) > 256
                         or any(ord(c) < 0x21 or ord(c) == 0x7f for c in value.id)
                         for value in models) or current not in {value.id for value in models}):
        raise McpError('Sampling current model/catalog unavailable; no fallback')
    for value in models:
        selected = stream.resolve_model(value.id)
        if selected is None or selected != value:
            raise McpError('Sampling model catalog/resolver identity mismatch')
    return tuple(value.id for value in models), copy.deepcopy(models)


def catalog_current(stream: StreamFn, current: str, names: tuple[str, ...], profiles: tuple[Model, ...]) -> bool:
    """Catalog withdrawal/errors revoke authority, never leak adapter diagnostics."""
    try:
        return model_catalog(stream, current) == (names, profiles)
    except (ValueError, RuntimeError, TypeError, UnicodeError):
        return False


def _parts(blocks: list[dict[str, Any]]) -> tuple[TextContent | ImageContent, ...]:
    parts: list[TextContent | ImageContent] = []
    for block in blocks:
        if block['type'] == 'text':
            parts.append(TextContent(text=block['text']))
        elif block['type'] == 'image':
            parts.append(ImageContent(data=block['data'], mime_type=block['mimeType']))
        else:
            raise McpError('Selected neutral model adapter does not support this sampling content; no conversion')
    return tuple(parts)


def _messages(rows: list[dict[str, Any]]) -> tuple[Message, ...]:
    messages: list[Message] = []
    calls: dict[str, str] = {}
    for row in rows:
        blocks = row['content'] if isinstance(row['content'], list) else [row['content']]
        if row['role'] == 'assistant':
            parts: list[TextContent | ThinkingContent | ToolCall] = []
            for block in blocks:
                if block['type'] == 'tool_use':
                    if block['id'] in calls:
                        raise McpError('Duplicate sampling tool-use identity')
                    calls[block['id']] = block['name']
                    parts.append(ToolCall(id=block['id'], name=block['name'], arguments=copy.deepcopy(block['input'])))
                elif block['type'] == 'text':
                    parts.append(TextContent(text=block['text']))
                else:
                    raise McpError('Selected neutral adapter cannot represent this assistant content')
            messages.append(AssistantMessage(content=tuple(parts)))
        else:
            plain: list[dict[str, Any]] = []
            for block in blocks:
                if block['type'] == 'tool_result':
                    if plain:
                        messages.append(UserMessage(content=_parts(plain)))
                        plain = []
                    name = calls.pop(block['toolUseId'], None)
                    if name is None:
                        raise McpError('Sampling tool result has no original pending call')
                    result_parts = _parts(block['content'])
                    if 'structuredContent' in block:
                        # Neutral model wire encoders intentionally omit host
                        # details. Preserve the explicitly reviewed JSON payload
                        # as labeled content, not an invisible details-only value.
                        structured = json_value(block['structuredContent'], maximum=256 * 1024)
                        result_parts = (*result_parts, TextContent(text='MCP structuredContent (JSON):\n' + structured))
                    messages.append(ToolResultMessage(tool_call_id=block['toolUseId'], tool_name=name,
                        content=result_parts, is_error=block.get('isError', False),
                        details=copy.deepcopy(block.get('structuredContent'))))
                else:
                    plain.append(block)
            if plain:
                messages.append(UserMessage(content=_parts(plain)))
    if calls:
        raise McpError('Sampling request has unsettled tool calls')
    return tuple(messages)


class SamplingExecutor:
    def __init__(self, stream: StreamFn, allowed_models: tuple[str, ...], alive: Callable[[], bool],
                 *, context: ContextResolver | None = None, timeout: float = 120) -> None:
        if (not isinstance(allowed_models, tuple) or not allowed_models or len(allowed_models) > 576
                or any(not isinstance(model, str) or not 0 < len(model.encode()) <= 256 for model in allowed_models)
                or len(set(allowed_models)) != len(allowed_models) or not 0.1 <= timeout <= 600):
            raise ValueError('Explicit sampling model catalog/deadline required')
        self.stream, self.allowed_models, self.alive = stream, allowed_models, alive
        self.context, self.timeout = context, timeout

    async def __call__(self, value: dict[str, Any], selected: dict[str, Any], cancel: asyncio.Event) -> dict[str, Any]:
        def live() -> None:
            if cancel.is_set() or not self.alive():
                raise McpError('Sampling adapter owner revoked; no model/output replay')
        live()
        if task_augmented(value):
            raise McpError('Client task capability not supported', code=-32602)
        validated = await settled_image_work(lambda: request(value))
        params = validated['params']
        if (not isinstance(selected, dict) or set(selected) != {'model', 'max_tokens', 'context', 'tools'}
                or not isinstance(selected['model'], str) or not isinstance(selected['context'], str)):
            raise McpError('Explicit sampling selection required')
        model = selected['model']
        if (model not in self.allowed_models or type(selected['max_tokens']) is not int
                or not 1 <= selected['max_tokens'] <= params['maxTokens']):
            raise McpError('Sampling model/token selection outside host catalog')
        provider_metadata = params.get('metadata')
        if provider_metadata is not None and any(not isinstance(value, str) for value in provider_metadata.values()):
            raise McpError('Selected neutral sampling adapter cannot represent non-string provider metadata')
        requested_context = params.get('includeContext', 'none')
        scope = selected['context']
        ranks = {'none': 0, 'thisServer': 1, 'allServers': 2}
        if scope not in ranks or ranks[scope] > ranks[requested_context]:
            raise McpError('Sampling context selection exceeds request')
        extra: tuple[Message, ...] = ()
        if scope != 'none':
            if self.context is None:
                raise McpError('Reviewed sampling context resolver unavailable; no parent-history fallback')
            extra = copy.deepcopy(await self.context(scope))
        definitions = {tool['name']: tool for tool in params.get('tools', [])}
        names = selected['tools']
        if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
                or len(set(names)) != len(names) or not set(names) <= definitions.keys()):
            raise McpError('Sampling tools outside reviewed request')
        tools = tuple(Tool(name=name, description=definitions[name].get('description', ''),
                           parameters=copy.deepcopy(definitions[name]['inputSchema'])) for name in names)
        metadata = self.stream.resolve_model(model) if isinstance(self.stream, ModelStreamFn) else None
        if tools and (metadata is None or metadata.supports_tool_calls is not True):
            raise McpError('Selected sampling adapter does not advertise tool calls')
        if metadata is not None and metadata.max_tokens and selected['max_tokens'] > metadata.max_tokens:
            raise McpError('Sampling output budget exceeds selected model limits')
        context = Context(messages=(*extra, *await settled_image_work(lambda: _messages(params['messages']))),
                          system_prompt=params.get('systemPrompt'), tools=tools or None)
        from xdog.agent.messages import messages_to_dicts
        # Bound the actual resolved context too, not merely the server request.
        await settled_image_work(lambda: json_value(messages_to_dicts(list(context.messages)), maximum=256 * 1024))
        await validate_image_context(self.stream, model, context)
        live()
        if isinstance(self.stream, ModelStreamFn) and self.stream.resolve_model(model) != metadata:
            raise McpError('Selected sampling model capabilities changed before admission')
        choice = params.get('toolChoice', {}).get('mode', 'auto')
        if choice == 'required' and not tools:
            raise McpError('Required proposed tools were not approved; no model call')
        options = StreamOptions(max_tokens=selected['max_tokens'], temperature=params.get('temperature'),
            stop_sequences=tuple(params['stopSequences']) if 'stopSequences' in params else None,
            tool_choice=ToolChoice(type='any' if choice == 'required' else choice) if tools else None,
            metadata=tuple(provider_metadata.items()) if provider_metadata is not None else None,
            cancel=cancel, web_search=False)
        response = None
        iterator = None
        consumption: asyncio.Task[AssistantMessage] | None = None
        try:
            async with asyncio.timeout(self.timeout):
                response = self.stream(model, context, options)
                if inspect.isawaitable(response):
                    response = await response
                iterator = response.__aiter__()
                async def consume() -> AssistantMessage:
                    assert iterator is not None and response is not None
                    async for _event in iterator:
                        live()  # No partial output goes to the peer.
                    return await response.result()
                consumption = asyncio.create_task(consume())
                while not consumption.done():
                    live()  # Cancel/host loss also interrupts stalled providers.
                    await asyncio.wait({consumption}, timeout=.025)
                message = consumption.result()
                live()
                if (not isinstance(message, AssistantMessage) or message.error_message
                        or message.stop_reason in ('error', 'aborted')):
                    raise McpError('Sampling provider failed/canceled; no partial output release')
                content: list[dict[str, Any]] = []
                for part in message.content:
                    if isinstance(part, TextContent):
                        content.append({'type': 'text', 'text': part.text})
                    elif isinstance(part, ToolCall):
                        content.append({'type': 'tool_use', 'id': part.id, 'name': part.name,
                                        'input': copy.deepcopy(part.arguments)})
                    elif not isinstance(part, ThinkingContent):
                        raise McpError('Unsupported sampling provider output')
                if not content:
                    raise McpError('Sampling has no releasable output; reasoning/signatures never disclosed')
                reason = {'stop': 'endTurn', 'length': 'maxTokens', 'toolUse': 'toolUse'}.get(
                    message.stop_reason, 'endTurn')
                output = {'model': model, 'role': 'assistant', 'stopReason': reason,
                          'content': content if tools else content[0]}
                if not tools and len(content) > 1:
                    if any(block['type'] != 'text' for block in content):
                        raise McpError('Unapproved sampling tool output')
                    output['content'] = {'type': 'text', 'text': ''.join(block['text'] for block in content)}
                effective = copy.deepcopy(validated)
                if 'tools' in effective['params']:
                    effective['params']['tools'] = [definitions[name] for name in names]
                return await settled_image_work(lambda: result(output, effective))
        finally:
            if consumption is not None and not consumption.done():
                consumption.cancel()
                while not consumption.done():
                    try:
                        await asyncio.shield(consumption)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
            if consumption is not None and not consumption.cancelled():
                consumption.exception()
            # EventStream wrappers do not automatically close their source when
            # the consumer is canceled. Close both owned iterator layers.
            for source in (iterator, getattr(response, '_iterator', None)):
                close = getattr(source, 'aclose', None)
                if callable(close):
                    await close()
