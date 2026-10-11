"""All rendered images—not just new user attachments—are admitted on the actual adapter."""
from __future__ import annotations

import asyncio
import base64
import threading
from dataclasses import replace

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.extensions.providers import ExtensionProviderStream
from xdog.agent.image_context import validate_image_context
from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ToolCall,
    UserMessage,
    VisionLimits,
)
from xdog.ai.utils.event_stream import EventStream

IMAGE = ImageContent(data=base64.b64encode(b'small opaque image bytes').decode(), mime_type='image/gif')


class Adapter:
    def __init__(self, *, count=1, size=1024, media=('image/gif',), vision=True):
        self.profile = Model(id='configured/vision', input=('text', 'image') if vision else ('text',),
                             vision_limits=VisionLimits(max_prompt_images=count, max_prompt_image_size=size,
                                                        supported_media_types=media))
        self.calls = []
    def resolve_model(self, name):
        return self.profile if name in ('configured/vision', 'short-alias') else None
    def __call__(self, model, context, options):
        self.calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='ADMITTED'),)))


@pytest.mark.parametrize('case', ['retained', 'transformed', 'text-only', 'byte-size', 'media', 'bad-base64'])
async def test_actual_agent_refuses_unavailable_complete_image_context_without_request_or_pruning(case):
    adapter = Adapter(vision=case != 'text-only', size=1 if case == 'byte-size' else 1024,
                      media=('image/png',) if case == 'media' else ('image/gif',))
    image = replace(IMAGE, data='not base64') if case == 'bad-base64' else IMAGE
    async def context(messages):
        return [*messages, UserMessage(content=(IMAGE,))] if case == 'transformed' else messages
    agent = Agent(adapter, config=AgentConfig(model='configured/vision'), transform_context=context)
    if case == 'retained':
        agent.append_message(UserMessage(content=(IMAGE,)))
    stream = await agent.prompt('New user image', images=[image])
    async for _ in stream:
        pass
    await stream.result()
    assert not adapter.calls and agent.state.error
    assert any(isinstance(part, ImageContent) for message in agent.messages
               for part in (() if isinstance(message.content, str) else message.content))
    if case in {'retained', 'transformed'}:
        assert 'image-count' in agent.state.error


async def test_tool_images_count_on_following_model_iteration_without_reexecuting_or_hiding_tool_result():
    executed = []
    class ToolAdapter(Adapter):
        def __call__(self, model, context, options):
            self.calls.append(context)
            return EventStream.empty(AssistantMessage(
                content=(ToolCall(id='picture', name='image_probe', arguments={}),), stop_reason='toolUse'))
    active = ToolAdapter(count=1)
    async def execute(*args, **kwargs):
        executed.append(True)
        return AgentToolResult(content=(TextContent(text='ACTUAL-TOOL-IMAGE'), IMAGE))
    tool = AgentTool(name='image_probe', description='Owned image return', parameters={}, execute=execute)
    agent = Agent(active, config=AgentConfig(model='configured/vision'), tools=[tool])
    stream = await agent.prompt('One image already supplied', images=[IMAGE])
    async for _ in stream:
        pass
    assert len(active.calls) == 1 and executed == [True]
    assert 'image-count' in agent.state.error and 'ACTUAL-TOOL-IMAGE' in str(agent.messages)


async def test_validation_counts_more_than_composer_limit_when_model_actually_advertises_it():
    adapter = Adapter(count=6)
    ctx = Context(messages=(UserMessage(content=(IMAGE,) * 5),))
    await validate_image_context(adapter, 'short-alias', ctx)
    assert not adapter.calls  # Validation is not execution, even when five declared images are accepted.
    agent = Agent(adapter, config=AgentConfig(model='configured/vision'))
    stream = await agent.prompt('Five allowed', images=[IMAGE] * 5)
    async for _ in stream:
        pass
    assert len(adapter.calls) == 1 and not agent.state.error
    assert sum(isinstance(part, ImageContent) for part in adapter.calls[0].messages[-1].content) == 5


async def test_global_profile_is_never_borrowed_for_opaque_or_transparent_unknown_adapter(monkeypatch):
    import xdog.ai as ai
    def unexpected(*args):
        pytest.fail('Image admission must not sync/login/load a global provider')
    monkeypatch.setattr(ai, 'provider', unexpected)
    monkeypatch.setattr(ai, 'load', unexpected)
    calls = []
    def opaque(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    class Runtime:
        def provider_model(self, name):
            return None
    ctx = Context(messages=(UserMessage(content=(IMAGE,)),))
    await validate_image_context(opaque, 'caller-owned', ctx)
    await validate_image_context(ExtensionProviderStream(Runtime(), opaque), 'caller-owned', ctx)
    assert not calls


async def test_profile_change_during_offloop_byte_validation_refuses_request_without_guessing_new_limits(monkeypatch):
    from xdog.agent import image_context
    adapter = Adapter()
    original = image_context._validate
    entered, release = threading.Event(), threading.Event()
    def held(images, model):
        entered.set()
        assert release.wait(3)
        original(images, model)
    monkeypatch.setattr(image_context, '_validate', held)
    task = asyncio.create_task(validate_image_context(adapter, 'configured/vision',
        Context(messages=(UserMessage(content=(IMAGE,)),))))
    try:
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.003)
        adapter.profile = replace(adapter.profile, input=('text',))
        release.set()
        with pytest.raises(ValueError, match='capabilities changed'):
            await task
        assert not adapter.calls
    finally:
        release.set()


async def test_malformed_metadata_is_refused_and_text_only_context_does_not_resolve_metadata():
    class Broken(Adapter):
        def resolve_model(self, name):
            return {'not': 'a model'}
    with pytest.raises(ValueError, match='model metadata'):
        await validate_image_context(Broken(), 'configured/vision', Context(messages=(UserMessage(content=(IMAGE,)),)))
    class NoLookup(Adapter):
        def resolve_model(self, name):
            pytest.fail('Text-only requests need no image metadata lookup')
    await validate_image_context(NoLookup(), 'configured/vision', Context(messages=(UserMessage(content='text'),)))


async def test_cooperative_abort_during_image_admission_prevents_stream_request_after_validation(monkeypatch):
    from xdog.agent import image_context

    adapter = Adapter()
    entered, release = threading.Event(), threading.Event()
    original = image_context._validate
    def held(images, model):
        entered.set()
        assert release.wait(3)
        original(images, model)
    monkeypatch.setattr(image_context, '_validate', held)
    agent = Agent(adapter, config=AgentConfig(model='configured/vision'))
    stream = await agent.prompt('Abort before adapter request', images=[IMAGE])
    try:
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.003)
        agent.abort()  # Cooperative abort, not a forced producer-task cancellation.
        release.set()
        async for _ in stream:
            pass
        await stream.result()
        assert not adapter.calls and not agent.is_streaming
    finally:
        release.set()
        await agent.cancel_and_wait()
