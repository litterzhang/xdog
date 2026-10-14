from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.providers import ExtensionProviderStream, registration, wire_event
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.ai.types import AssistantMessage, Context, ImageContent, StreamOptions, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream


async def runtime(tmp_path, settings=None):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    result = ExtensionRuntime(['providermock'], root, tmp_path,
                              settings={'providermock': {'marker': str(tmp_path / 'provider-entered'),
                                                       **(settings or {})}})
    await result.initialize()
    return result


def test_registration_namespace_and_exact_capabilities_are_bounded_and_cannot_shadow_builtins():
    good = {'id': 'echo', 'context_window': 32000, 'max_tokens': 2048}
    assert registration('Local', [good])['name'] == 'local'
    for value in [{**good, 'context_window': True}, {**good, 'max_tokens': 0}, {**good, 'input': ['audio']},
                  {**good, 'supports_tool_calls': 'true'}, {**good, 'cost': {'input': float('nan')}}]:
        with pytest.raises(ValueError):
            registration('local', [value])
    with pytest.raises(ValueError):
        registration('copilot/model', [good])
    api = ExtensionAPI({})
    api.register_provider('local', [good], lambda *args: None)
    with pytest.raises(ValueError):
        api.register_provider('LOCAL', [good], lambda *args: None)
    for value in [{'type': 'tool_call', 'id': 'x', 'name': 'mutate', 'arguments': []},
                  {'type': 'usage', 'output': True}, {'type': 'done', 'stop_reason': 'unknown'}]:
        with pytest.raises(ValueError):
            wire_event(value)


async def test_real_provider_stream_context_transfer_signatures_usage_and_no_base_fallback(tmp_path):
    owner = await runtime(tmp_path)
    base_calls = []
    def base(*args):
        base_calls.append(args)
        return EventStream.empty(AssistantMessage())
    adapter = ExtensionProviderStream(owner, base)
    try:
        alias = 'ext-providermock-local/echo'
        assert len(owner.provider_models()) == 7 and adapter.resolve_model(alias).context_window == 32000
        # Large data must use the checksummed transfer, not truncate a single RPC.
        context = Context(system_prompt='s' * (2 * 1024 * 1024), messages=(UserMessage(content=(
            TextContent(text='user data'),
            ImageContent(data='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8'
                              '/x8AAwMCAO+j5ioAAAAASUVORK5CYII=', mime_type='image/png'))),))
        stream = adapter(alias, context, StreamOptions(thinking='low'))
        events = [event async for event in stream]
        message = await stream.result()
        assert message.model == alias and message.response_id == 'owned-response'
        assert message.usage.total_tokens == 10 and message.content[0].thinking_signature == 'thinking-continuity'
        assert message.content[1].text_signature == 'text-two' and message.content[1].text == 'EXT-PROVIDER-ANSWER'
        assert any(event.type == 'text_delta' for event in events) and events[-1].type == 'done'
        assert await owner.command('ext-providermock-count', '') == '1' and not base_calls
        assert await owner.command('ext-providermock-closed', '') == '1' and not owner._provider_lock.locked()
        await owner.manage('unload', 'providermock')
        unavailable = adapter(alias, Context(), StreamOptions())
        failed = [event async for event in unavailable]
        assert failed[-1].type == 'error' and not base_calls
    finally:
        await owner.aclose()


def test_provider_error_event_has_a_bounded_explicit_public_message() -> None:

    def _check_case(event):
        with pytest.raises(ValueError):
            wire_event(event)

    for event in [
        {"type": "error"},
        {"type": "error", "message": ""},
        {"type": "error", "message": 1},
        {"type": "error", "message": "x" * 8193},
        {"type": "error", "message": "\ud800"},
        {"type": "error", "message": "public", "stop_reason": "toolUse"},
        {"type": "error", "message": "public", "stop_reason": True},
        {"type": "error", "message": "public", "response_id": 1},
        {"type": "error", "message": "public", "private": "not a diagnostic field"},
    ]:
        _check_case(event)


async def test_provider_failure_with_tool_call_cannot_enter_hook_or_tool_execution(tmp_path):
    from xdog.agent import Agent, AgentConfig
    from xdog.agent.core import AgentTool, AgentToolResult

    owner = await runtime(tmp_path, {'failed_tool': True})
    hooks, mutations = [], []

    async def before(context):
        hooks.append(context)
        return None

    async def mutate(*args, **kwargs):
        mutations.append(args)
        return AgentToolResult(content=(TextContent(text='must not run'),))

    agent = Agent(ExtensionProviderStream(owner, lambda *args: EventStream.empty(AssistantMessage())),
        config=AgentConfig(model='ext-providermock-local/failure'), before_tool_call=before,
        tools=[AgentTool(name='mutate', description='mutation', parameters={'type': 'object'}, execute=mutate)])
    try:
        events = [event async for event in await agent.prompt('fail after proposing a tool')]
        assert agent.state.error == 'Provider unavailable' and not hooks and not mutations
        assert not any(event.type.startswith('tool_execution') for event in events)
        assert agent.messages[-1].stop_reason == 'error'
        assert await owner.command('ext-providermock-count', '') == '1'
        assert await owner.command('ext-providermock-closed', '') == '1'
    finally:
        await owner.aclose()
