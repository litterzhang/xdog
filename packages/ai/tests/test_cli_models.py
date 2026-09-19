"""Human-readable model rows must not confuse protocols with operation support."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from xdog.ai.cli import _cmd_models, _compact_token_limit
from xdog.ai.types import Model, ModelCost, ModelEndpoint


@pytest.fixture
def catalogue(monkeypatch):
    models = (
        Model(
            id="copilot/chat", provider="copilot", api="openai-completions",
            context_window=1050000, max_prompt_tokens=922000, max_tokens=128000,
            reasoning=True, input=("text", "image"), output=("text",), cost=ModelCost(input=1),
            supported_protocols=("openai-completions", "openai-responses"),
            supported_generation_protocols=("openai-completions", "openai-responses"),
        ),
        Model(
            id="copilot/embedding", provider="copilot", api="openai-completions", model_type="embeddings",
            supported_protocols=("openai-completions",), supported_generation_protocols=(),
            endpoints=(ModelEndpoint("openai-completions", "embed", "/v1/embeddings"),),
        ),
        Model(
            id="copilot/image", provider="copilot", api="openai-completions",
            preferred_protocol="openai-responses", output=("text", "image"),
            context_window=400000, max_prompt_tokens=272000, max_tokens=128000,
            cost=ModelCost(input=0.33),
        ),
    )
    monkeypatch.setattr("xdog.ai.cli.ai.provider", lambda _: SimpleNamespace(models=lambda: models))
    return models


@pytest.mark.asyncio
async def test_compact_rows_show_selected_protocol_tags_and_cost(catalogue, capsys):
    await _cmd_models("copilot", sync=False)
    output = capsys.readouterr().out
    rows = {
        line.split()[0]: line.split()[1:]
        for line in output.splitlines()
        if line.strip() and line.split()[0] in ("chat", "embedding", "image")
    }
    assert rows["chat"] == ["922k", "in", "128k", "out", "openai-completions", "reasoning", "1x"]
    assert rows["embedding"] == ["?", "in", "?", "out", "openai-completions", "embedding", "free"]
    assert rows["image"] == ["272k", "in", "128k", "out", "openai-responses", "image_generation", "0.33x"]
    assert "READ IMAGE" not in output and "PROTOCOLS" not in output
    assert "/v1/embeddings" not in output


@pytest.mark.asyncio
async def test_details_preserve_all_protocols_and_independent_image_capabilities(catalogue, capsys):
    await _cmd_models("copilot", sync=False, details=True)
    output = capsys.readouterr().out
    assert "Supported protocols: openai-completions, openai-responses" in output
    assert "Read image: yes; generate image: no" in output
    assert "Generation protocols: none" in output
    assert "Embeddings: POST /v1/embeddings [openai-completions]" in output
    assert "context: 1,050,000; max input: 922,000; max output: 128,000 tokens" in output


@pytest.mark.asyncio
async def test_json_does_not_claim_embedding_supports_generation(catalogue, capsys):
    await _cmd_models("copilot", sync=False, output_json=True)
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)}
    assert rows["copilot/embedding"]["supported_protocols"] == ["openai-completions"]
    assert rows["copilot/embedding"]["supported_generation_protocols"] == []
    assert rows["copilot/chat"]["supports_image_input"] is True
    assert rows["copilot/chat"]["supports_image_output"] is False
    assert rows["copilot/image"]["supports_image_output"] is True


@pytest.mark.parametrize("value,expected", [
    (0, "?"), (512, "512"), (1000, "1k"), (65535, "65k"), (1050000, "1050k"),
])
def test_compact_token_counts(value, expected):
    assert _compact_token_limit(value) == expected
