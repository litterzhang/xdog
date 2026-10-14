from __future__ import annotations

import base64

import pytest
from xdog.agent.extensions.providers import model_from_registration, registration

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


def test_malformed_capabilities_are_not_coerced_or_partially_admitted() -> None:

    def _check_case(change):
        with pytest.raises(ValueError):
            registration("local", [{**ROW, **change}])

    for change in [
        {"id": ""},
        {"id": "model id"},
        {"id": "x" * 129},
        {"id": "model\x00name"},
        {"max_prompt_tokens": True},
        {"max_prompt_tokens": -1},
        {"max_prompt_tokens": 48001},
        {"supported_efforts": ["high", "high"]},
        {"supported_efforts": ["unknown"]},
        {"supported_efforts": "high"},
        {"reasoning": False, "supported_efforts": ["high"]},
        {"vision_limits": {"max_prompt_images": True}},
        {"vision_limits": {"max_prompt_image_size": -1}},
        {"vision_limits": {"supported_media_types": ["text/plain"]}},
        {"vision_limits": {"supported_media_types": ["image/png", "image/png"]}},
        {"vision_limits": {"unexpected": 1}},
        {"input": ["text"], "vision_limits": {}},
    ]:
        _check_case(change)
