from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.providers import ExtensionProviderStream, model_from_registration, registration
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.supervision_config import ChildConfig
from xdog.agent.supervision_model import resolve_child_model
from xdog.ai.types import AssistantMessage, Context, ImageContent, StreamOptions, UserMessage
from xdog.ai.utils.event_stream import EventStream

ALIAS = 'ext-providercaps-local/gpt-5.1/code:preview'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
ROW = {'id': 'gpt-5.1/code:preview', 'context_window': 48000, 'max_prompt_tokens': 30000, 'max_tokens': 2048,
       'reasoning': True, 'supported_efforts': ['low', 'high'], 'input': ['text', 'image'],
       'vision_limits': {'max_prompt_images': 1, 'max_prompt_image_size': 1024,
                         'supported_media_types': ['image/png']}}


def test_full_provider_metadata_uses_real_model_identity_and_independent_prompt_effort_vision_limits():
    declaration = registration('local', [ROW])
    model = model_from_registration('providercaps', declaration, declaration['models'][0])
    assert model.id == ALIAS and model.context_window == 48000 and model.max_prompt_tokens == 30000
    assert model.supported_efforts == ('low', 'high') and model.vision_limits.max_prompt_images == 1
    assert model.vision_limits.max_prompt_image_size == 1024
    assert model.vision_limits.supported_media_types == ('image/png',)


@pytest.mark.parametrize('change', [
    {'id': ''}, {'id': 'model id'}, {'id': 'x' * 129}, {'id': 'model\0name'},
    {'max_prompt_tokens': True}, {'max_prompt_tokens': -1}, {'max_prompt_tokens': 48001},
    {'supported_efforts': ['high', 'high']}, {'supported_efforts': ['unknown']}, {'supported_efforts': 'high'},
    {'reasoning': False, 'supported_efforts': ['high']}, {'vision_limits': {'max_prompt_images': True}},
    {'vision_limits': {'max_prompt_image_size': -1}}, {'vision_limits': {'supported_media_types': ['text/plain']}},
    {'vision_limits': {'supported_media_types': ['image/png', 'image/png']}}, {'vision_limits': {'unexpected': 1}},
    {'input': ['text'], 'vision_limits': {}},
])
def test_malformed_capabilities_are_not_coerced_or_partially_admitted(change):
    with pytest.raises(ValueError):
        registration('local', [{**ROW, **change}])


async def test_real_worker_full_metadata_routes_exact_native_id_and_rejects_invalid_effort_images_before_invocation(
        tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['providercaps'], root, tmp_path)
    base_calls = []
    def base(*args):
        base_calls.append(args)
        return EventStream.empty(AssistantMessage())
    try:
        await runtime.initialize()
        adapter = ExtensionProviderStream(runtime, base)
        model = adapter.resolve_model(ALIAS)
        assert model.max_prompt_tokens == 30000 and model.supported_efforts == ('low', 'high')
        image = ImageContent(data=base64.b64encode(PNG).decode(), mime_type='image/png')
        valid = adapter(ALIAS, Context(messages=(UserMessage(content=(image,)),)), StreamOptions(thinking='low'))
        assert (await valid.collect())[-1].type == 'done'
        for options, images in [(StreamOptions(thinking='xhigh'), ()),
                                (StreamOptions(thinking='high'), (image, image)),
                                (StreamOptions(), (ImageContent(data='x' * 2000, mime_type='image/png'),)),
                                (StreamOptions(), (ImageContent(data='aGVsbG8=', mime_type='image/jpeg'),))]:
            rejected = adapter(ALIAS, Context(messages=(UserMessage(content=images),)), options)
            assert (await rejected.collect())[-1].type == 'error'
        assert await runtime.command('ext-providercaps-count', '') == 'CALLS=1' and not base_calls
        child = resolve_child_model(adapter, 'base/model', StreamOptions(),
                                    config=ChildConfig(model=ALIAS, thinking='high'), context_window=99999,
                                    supports_tool_calls=True)
        assert child.max_prompt_tokens == 30000 and child.options.thinking == 'high'
        with pytest.raises(ValueError, match='not advertised'):
            resolve_child_model(adapter, 'base/model', StreamOptions(),
                                config=ChildConfig(model=ALIAS, thinking='xhigh'), context_window=99999,
                                supports_tool_calls=True)
    finally:
        await runtime.aclose()
