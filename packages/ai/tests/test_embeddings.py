"""Tests for embedding support — protocol, embed(), model_sync, registration."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from xdog.ai.core import AuthResult
from xdog.ai.types import (
    EmbeddingObject,
    EmbeddingRequest,
    EmbeddingResponse,
    Model,
    ModelCost,
    Usage,
)
from xdog.ai.vendors.copilot._model_sync import _model_from_dict, _model_to_dict, _parse_api_model


def _make_embedding_api_model(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "text-embedding-3-small",
        "name": "Embedding V3 small",
        "vendor": "OpenAI",
        "preview": False,
        "supported_endpoints": ["/v1/embeddings"],
        "capabilities": {
            "family": "text-embedding-3-small",
            "type": "embeddings",
            "limits": {"max_context_window_tokens": 8191},
            "supports": {"dimensions": True},
        },
    }
    base.update(overrides)
    return base


def _make_embedding_model() -> Model:
    return Model(
        id="copilot/text-embedding-3-small",
        name="Embedding V3 small (Copilot)",
        api="openai-completions",
        provider="copilot",
        base_url="https://api.githubcopilot.com",
        model_type="embeddings",
        cost=ModelCost(),
    )


def _make_api_response(embeddings=None, prompt_tokens=8):
    if embeddings is None:
        embeddings = [[0.1, 0.2, 0.3]]
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": emb}
                 for i, emb in enumerate(embeddings)],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }


def test_embedding_model_parsed_correctly():
    """Core embedding model fields: type, protocol, no-reasoning, no-streaming."""
    model = _parse_api_model(_make_embedding_api_model())
    assert model is not None
    assert model.model_type == "embeddings"
    assert model.api == "openai-completions"
    assert model.supported_protocols == ("openai-completions",)
    assert model.supported_generation_protocols == ()
    assert model.reasoning is False
    assert model.supports_streaming is False


@pytest.mark.asyncio
async def test_embed_request_and_response():
    """Verify HTTP request is correct and response is parsed."""
    from xdog.ai.protocols.openai_completions import OpenAICompletionsProtocol

    captured: dict[str, Any] = {}

    async def mock_post(self, url, *, json, headers, **kwargs):
        captured.update(url=url, json=json, headers=headers)
        return httpx.Response(200, json=_make_api_response(),
                              request=httpx.Request("POST", url))

    proto = OpenAICompletionsProtocol()
    with patch.object(httpx.AsyncClient, "post", mock_post):
        m = _make_embedding_model()
        result = await proto.embed(
            m,
            EmbeddingRequest(input="Hello", dimensions=256),
            AuthResult(api_key="test-key"),
        )

    assert captured["json"]["input"] == ["Hello"]
    assert captured["json"]["dimensions"] == 256
    assert len(result.data) == 1
    assert result.usage.input == 8


@pytest.mark.asyncio
async def test_embed_usage_carries_cost():
    """Embedding usage goes through usage_with_cost like every other protocol."""
    from dataclasses import replace

    from xdog.ai.protocols.openai_completions import OpenAICompletionsProtocol

    async def mock_post(self, url, *, json, headers, **kwargs):
        return httpx.Response(200, json=_make_api_response(),
                              request=httpx.Request("POST", url))

    proto = OpenAICompletionsProtocol()
    priced = replace(_make_embedding_model(), cost=ModelCost(input=0.33))

    with patch.object(httpx.AsyncClient, "post", mock_post):
        result = await proto.embed(
            priced,
            EmbeddingRequest(input="Hello"),
            AuthResult(api_key="test-key"),
        )

    assert result.usage.cost.total == 0.33


@pytest.mark.asyncio
async def test_embed_string_shorthand():
    """embed(provider, model, 'text') wraps string into EmbeddingRequest."""
    from xdog.ai.providers.copilot import CopilotProvider

    mock_embed = AsyncMock(return_value=EmbeddingResponse(
        data=(EmbeddingObject(embedding=(0.1,)),),
        usage=Usage(input=1, total_tokens=1),
    ))

    p = CopilotProvider()
    m = _make_embedding_model()
    p._model_cache[m.id] = m  # inject into provider's cache

    # Mock the protocol's embed method
    mock_proto = type("P", (), {"embed": mock_embed, "stream": None})()
    p._protocols[m.api] = mock_proto

    # Mock auth to passthrough
    async def _passthrough_auth(model, context=None):
        from xdog.ai.core import AuthResult
        return AuthResult(api_key="k")

    with patch.object(p._get_vendor(), "resolve_auth", _passthrough_auth):
        result = await p.embed(m.id.split("/", 1)[-1], "Hello")

    assert isinstance(result, EmbeddingResponse)
    req = mock_embed.call_args[0][1]
    assert isinstance(req, EmbeddingRequest)


def test_openai_completions_supports_embed():
    from xdog.ai.protocols.openai_completions import OpenAICompletionsProtocol

    proto = OpenAICompletionsProtocol()
    assert callable(proto.embed)


def test_dimensions_survives_cache_round_trip():
    model = Model(id="copilot/emb", api="openai-completions", provider="copilot",
                  model_type="embeddings", dimensions=1536, cost=ModelCost())
    restored = _model_from_dict(_model_to_dict(model))
    assert restored.dimensions == 1536
    assert restored.model_type == "embeddings"
