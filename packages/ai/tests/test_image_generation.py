"""First-class image generation and CLI output; no live generation/quota use."""
from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from xdog.ai.cli import main
from xdog.ai.protocols.google_images import decode_image_response, encode_image_request
from xdog.ai.providers.antigravity import AntigravityProvider
from xdog.ai.providers.copilot import CopilotProvider
from xdog.ai.providers.runtime import Runtime
from xdog.ai.types import (
    GeneratedImage,
    ImageContent,
    ImageGenerationRequest,
    ImageGenerationResponse,
    Model,
)
from xdog.ai.utils.image_files import decode_image, read_reference, save_images
from xdog.ai.vendors.antigravity import AntigravityVendor, _transport
from xdog.ai.vendors.antigravity._auth import Credentials

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV1kAAAAASUVORK5CYII="
)
MODEL = Model(
    id="antigravity/picture", provider="antigravity", api="google-generative-ai",
    input=("text", "image"), output=("image",),
)


def image_part():
    return {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}


def response(*parts):
    return {"candidates": [{"content": {"parts": list(parts or (image_part(),))}, "finishReason": "STOP"}]}


@pytest.fixture
def provider(monkeypatch):
    vendor = AntigravityVendor()
    monkeypatch.setattr(vendor, "models", lambda: (MODEL,))
    monkeypatch.setattr(vendor.tokens, "get", AsyncMock(return_value=Credentials("token", "refresh", 0, "project")))
    return AntigravityProvider(vendor)


@pytest.fixture
def http(monkeypatch):
    original = httpx.AsyncClient

    def install(handler):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=transport, **kw))

    return install


@pytest.mark.asyncio
async def test_provider_image_generation_uses_native_route_and_typed_result(provider, http):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/v1internal:generateContent"
        body = json.loads(request.content)
        assert body["model"] == "picture" and body["project"] == "project"
        assert body["requestType"] == "image_gen"
        assert body["request"]["generationConfig"] == {
            "responseModalities": ["TEXT", "IMAGE"], "candidateCount": 1,
        }
        return httpx.Response(200, json={"response": {
            **response({"text": "A mockup"}, image_part()),
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 30, "totalTokenCount": 42},
        }})

    http(handler)
    result = await provider.image_generation("picture", "Draw a mockup")
    assert result.images == (GeneratedImage(PNG, "image/png"),)
    assert result.text == "A mockup"
    assert result.provider == "antigravity" and result.model == "antigravity/picture"
    assert result.usage.total_tokens == 42
    assert len(requests) == 1


def test_reference_image_and_options_encode_correctly():
    request = ImageGenerationRequest(
        "Edit", reference_images=(ImageContent(data=base64.b64encode(PNG).decode()),),
        aspect_ratio="9:16", image_size="2K",
    )
    body = encode_image_request(request, MODEL)
    assert body["contents"][0]["parts"] == [{"text": "Edit"}, image_part()]
    assert body["generationConfig"]["imageConfig"] == {"aspectRatio": "9:16", "imageSize": "2K"}


@pytest.mark.asyncio
@pytest.mark.parametrize("output,input_types,references", [
    (("text",), ("text", "image"), ()),
    (("image",), ("text",), (ImageContent(data=base64.b64encode(PNG).decode()),)),
])
async def test_unsupported_capability_fails_before_auth(provider, monkeypatch, output, input_types, references):
    monkeypatch.setattr(provider, "_resolve", lambda _: replace(MODEL, output=output, input=input_types))
    with pytest.raises(NotImplementedError):
        await provider.image_generation("picture", ImageGenerationRequest("Draw", reference_images=references))
    provider._vendor.tokens.get.assert_not_awaited()


@pytest.mark.parametrize("image_request", [
    ImageGenerationRequest(" "),
    ImageGenerationRequest("Draw", aspect_ratio="wrong"),
    ImageGenerationRequest("Draw", aspect_ratio="1:0"),
    ImageGenerationRequest("Draw", image_size="invalid"),
    ImageGenerationRequest("Draw", reference_images=tuple(ImageContent() for _ in range(5))),
])
def test_invalid_image_requests_fail(image_request):
    with pytest.raises(ValueError):
        encode_image_request(image_request, MODEL)


def test_decode_skips_thinking_and_retains_multiple_images():
    result = decode_image_response(response(
        {"thought": True, "text": "private"}, {**image_part(), "thought": True},
        {"text": "Public text"}, image_part(), image_part(),
    ), MODEL)
    assert result.text == "Public text"
    assert result.images == (GeneratedImage(PNG), GeneratedImage(PNG))


@pytest.mark.parametrize("payload", [
    {}, response({"text": "No image"}), {"candidates": None},
    {"promptFeedback": {"blockReason": "SAFETY"}},
    {"error": {"message": "private"}},
    {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [image_part()]}}]},
    response({"inlineData": {"mimeType": "image/png", "data": "invalid-base64"}}),
    response({"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(PNG).decode()}}),
])
def test_bad_generation_responses_fail_instead_of_saving_text(payload):
    with pytest.raises(ValueError):
        decode_image_response(payload, MODEL)


@pytest.mark.asyncio
async def test_runtime_routes_image_generation_to_provider(provider, monkeypatch):
    runtime = Runtime()
    runtime._active["antigravity"] = provider
    result = ImageGenerationResponse(images=(GeneratedImage(PNG),))
    generate = AsyncMock(return_value=result)
    monkeypatch.setattr(provider, "image_generation", generate)
    assert await runtime.image_generation("antigravity/picture", "Draw") is result
    generate.assert_awaited_once_with("picture", "Draw")
    assert await runtime.image_generation("picture", "Again") is result


@pytest.mark.asyncio
async def test_unsupported_provider_has_no_chat_fallback():
    with pytest.raises(NotImplementedError, match="does not support image generation"):
        await CopilotProvider().image_generation("any-model", "Draw")


@pytest.mark.asyncio
async def test_image_generation_cancellation_closes_response(provider, http):
    started, closed = asyncio.Event(), asyncio.Event()

    class Blocked(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.set()

    http(lambda _: httpx.Response(200, stream=Blocked()))
    task = asyncio.create_task(provider.image_generation("picture", "Draw"))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.asyncio
async def test_native_image_response_size_is_bounded(provider, http, monkeypatch):
    monkeypatch.setattr(_transport, "_MAX_JSON_RESPONSE_BYTES", 10)
    http(lambda _: httpx.Response(200, json={"response": response()}))
    with pytest.raises(ValueError, match="exceeded"):
        await provider.image_generation("picture", "Draw")


def test_files_are_unique_and_match_mime_type(tmp_path):
    image = GeneratedImage(PNG)
    first = save_images((image,), tmp_path)
    second = save_images((image,), tmp_path)
    assert first != second
    assert first[0].read_bytes() == second[0].read_bytes() == PNG
    assert first[0].suffix == ".png"
    reference = read_reference(first[0])
    assert reference.mime_type == "image/png"
    assert decode_image(reference.data, reference.mime_type) == image


@pytest.mark.parametrize("command", ["image", "image_generation"])
def test_cli_saves_images_and_returns_json(monkeypatch, tmp_path, capsys, command):
    generation = AsyncMock(return_value=ImageGenerationResponse(
        images=(GeneratedImage(PNG),), text="Design", model="antigravity/picture", provider="antigravity",
    ))
    monkeypatch.setattr("xdog.ai.cli.ai.provider", lambda _: SimpleNamespace(image_generation=generation))
    monkeypatch.setattr("sys.argv", [
        "xdog-ai", command, "antigravity", "picture", "Draw a screen",
        "--output-dir", str(tmp_path), "--aspect-ratio", "9:16", "--image-size", "1K", "--json",
    ])
    main()
    assert json.loads(capsys.readouterr().out)["model"] == "antigravity/picture"
    files = list(tmp_path.glob("*.png"))
    assert len(files) == 1 and files[0].read_bytes() == PNG
    generation.assert_awaited_once_with(
        "picture", ImageGenerationRequest("Draw a screen", aspect_ratio="9:16", image_size="1K"),
    )


def test_cli_json_contains_paths_not_base64(monkeypatch, tmp_path, capsys):
    generation = AsyncMock(return_value=ImageGenerationResponse(images=(GeneratedImage(PNG),), text="Design"))
    monkeypatch.setattr("xdog.ai.cli.ai.provider", lambda _: SimpleNamespace(image_generation=generation))
    monkeypatch.setattr("sys.argv", [
        "xdog-ai", "image", "test", "image", "Draw", "--output-dir", str(tmp_path), "--json",
    ])
    main()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert len(result["paths"]) == 1 and result["text"] == "Design"
    assert base64.b64encode(PNG).decode() not in output


def test_cli_reads_references_and_prints_saved_paths(monkeypatch, tmp_path, capsys):
    reference = tmp_path / "reference.png"
    reference.write_bytes(PNG)
    generation = AsyncMock(return_value=ImageGenerationResponse(images=(GeneratedImage(PNG),)))
    monkeypatch.setattr("xdog.ai.cli.ai.provider", lambda _: SimpleNamespace(image_generation=generation))
    monkeypatch.setattr("sys.argv", [
        "xdog-ai", "image", "test", "image", "Edit", "--reference", str(reference),
        "--output-dir", str(tmp_path / "out"),
    ])
    main()
    assert "Saved:" in capsys.readouterr().out
    assert generation.await_args.args[1].reference_images == (read_reference(reference),)


def test_cli_rejects_existing_file_as_output_dir_before_generation(monkeypatch, tmp_path, capsys):
    destination = tmp_path / "keep.txt"
    destination.write_text("Keep")
    generation = AsyncMock()
    monkeypatch.setattr("xdog.ai.cli.ai.provider", lambda _: SimpleNamespace(image_generation=generation))
    monkeypatch.setattr("sys.argv", [
        "xdog-ai", "image", "test", "image", "Draw", "--output-dir", str(destination),
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert "existing file" in capsys.readouterr().err
    generation.assert_not_awaited()
    assert destination.read_text() == "Keep"


@pytest.mark.parametrize("failure", [ValueError("No image returned"), httpx.ConnectError("sensitive request")])
def test_cli_errors_are_nonzero_and_network_details_are_hidden(monkeypatch, tmp_path, capsys, failure):
    generation = AsyncMock(side_effect=failure)
    monkeypatch.setattr("xdog.ai.cli.ai.provider", lambda _: SimpleNamespace(image_generation=generation))
    monkeypatch.setattr("sys.argv", ["xdog-ai", "image", "test", "image", "Draw", "--output-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert "Error:" in output and "sensitive request" not in output
    assert not list(tmp_path.iterdir())
