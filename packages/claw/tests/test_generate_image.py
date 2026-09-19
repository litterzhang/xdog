"""Claw image generation delegates to xdog.ai and confines its file operations."""
from __future__ import annotations

import asyncio
import base64
import json
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from click.testing import CliRunner
from xdog.ai.providers.antigravity import AntigravityProvider
from xdog.ai.providers.runtime import Runtime
from xdog.ai.types import GeneratedImage, ImageContent, ImageGenerationRequest, ImageGenerationResponse, Model
from xdog.ai.utils import image_files
from xdog.ai.vendors.antigravity import AntigravityVendor
from xdog.ai.vendors.antigravity._auth import Credentials
from xdog.claw.cli.cli import cli
from xdog.claw.config import ClawConfig, save_config
from xdog.claw.core.tools import create_tools, registered_names
from xdog.claw.core.tools import tool_generate_image as images

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV1kAAAAASUVORK5CYII="
)
IMAGE_MODEL = "antigravity/gemini-3.1-flash-image"
generate_images = partial(images.generate_images, model=IMAGE_MODEL)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    runtime = SimpleNamespace(
        active_providers=Mock(return_value=["antigravity"]),
        model=Mock(side_effect=lambda name: Model(id=name, output=("image",))),
        image_generation=AsyncMock(return_value=ImageGenerationResponse(
            images=(GeneratedImage(PNG),), text="Here is the design.",
        )),
    )
    monkeypatch.setattr(images.ai, "load", Mock(return_value=runtime))
    monkeypatch.setenv("CLAW_DIR", str(tmp_path))
    save_config(
        ClawConfig(model="copilot/chat", enabled_tools=("generate_image",), image_model=IMAGE_MODEL),
        tmp_path / "config.yaml",
    )
    return runtime


@pytest.mark.asyncio
async def test_delegates_to_xdog_ai_and_returns_paths(tmp_path, runtime):
    result = await generate_images("Coffee shop UI", workspace=tmp_path, output_dir="design")
    runtime.image_generation.assert_awaited_once_with(
        "antigravity/gemini-3.1-flash-image",
        ImageGenerationRequest("Coffee shop UI", aspect_ratio="9:16", image_size="1K"),
    )
    assert len(result.paths) == 1
    assert result.paths[0].parent == tmp_path / "design"
    assert result.paths[0].suffix == ".png"
    assert result.paths[0].read_bytes() == PNG
    assert result.text == "Here is the design."
    assert base64.b64encode(PNG).decode() not in result.to_json()


@pytest.mark.asyncio
async def test_reference_images_and_model_are_forwarded(tmp_path, runtime):
    (tmp_path / "reference.png").write_bytes(PNG)
    runtime.image_generation.return_value = ImageGenerationResponse(images=(GeneratedImage(PNG), GeneratedImage(PNG)))
    options = dict(
        workspace=tmp_path, references=("reference.png",),
        aspect_ratio="1:1", image_size="2K", model="antigravity/another-image",
    )
    first = await generate_images("Use this reference", **options)
    second = await generate_images("Another version", **options)
    first_call = runtime.image_generation.await_args_list[0]
    assert first_call.args == ("antigravity/another-image", ImageGenerationRequest(
        "Use this reference", reference_images=(ImageContent(data=base64.b64encode(PNG).decode()),),
        aspect_ratio="1:1", image_size="2K",
    ))
    assert len(first.paths) == len(second.paths) == 2
    assert set(first.paths).isdisjoint(second.paths)
    assert all(path.read_bytes() == PNG for path in (*first.paths, *second.paths))


@pytest.mark.asyncio
async def test_legacy_proxy_settings_are_not_used(tmp_path, runtime, monkeypatch):
    monkeypatch.setenv("CLIPROXY_API_BASE_URL", "https://must-not-be-contacted.invalid")
    monkeypatch.setenv("CLIPROXY_API_KEY", "obsolete-secret")
    monkeypatch.setenv("CLIPROXY_IMAGE_MODEL", "wrong/model")
    result = await generate_images("test", workspace=tmp_path)
    assert runtime.image_generation.await_args.args[0] == IMAGE_MODEL
    assert "obsolete-secret" not in result.to_json()
    assert not hasattr(images, "ImageProxyConfig")


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"output_dir": "../outside"},
    {"references": ("../outside.png",)},
    {"aspect_ratio": "invalid"},
    {"image_size": "invalid"},
    {"references": ("a", "b", "c", "d", "e")},
    {"model": "bare-model"},
    {"model": "/missing-provider"},
    {"model": "antigravity/"},
])
async def test_invalid_arguments_do_not_generate(tmp_path, runtime, kwargs):
    with pytest.raises(images.ImageGenerationError):
        await generate_images("test", workspace=tmp_path, **kwargs)
    runtime.image_generation.assert_not_awaited()
    assert not (tmp_path / "generated-images").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [False, True])
async def test_symlink_escape_is_rejected(tmp_path, runtime, reference):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "outside").symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / "reference.png").write_bytes(PNG)
    kwargs = {"references": ("outside/reference.png",)} if reference else {"output_dir": "outside"}
    with pytest.raises(images.ImageGenerationError, match="inside the workspace"):
        await generate_images("test", workspace=workspace, **kwargs)
    runtime.image_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_output_workspace_is_rechecked_after_generation(tmp_path, runtime):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    async def generate(*args):
        (workspace / "design").symlink_to(outside, target_is_directory=True)
        return ImageGenerationResponse(images=(GeneratedImage(PNG),))

    runtime.image_generation.side_effect = generate
    with pytest.raises(images.ImageGenerationError, match="inside the workspace"):
        await generate_images("test", workspace=workspace, output_dir="design")
    assert not list(outside.iterdir())


@pytest.mark.asyncio
async def test_reference_validation_reuses_ai_file_helpers(tmp_path, runtime, monkeypatch):
    (tmp_path / "reference.png").write_bytes(PNG)
    monkeypatch.setattr(image_files, "MAX_REFERENCE_BYTES", 8)
    with pytest.raises(images.ImageGenerationError, match="at most"):
        await generate_images("test", workspace=tmp_path, references=("reference.png",))
    (tmp_path / "reference.png").write_bytes(b"not-png")
    with pytest.raises(images.ImageGenerationError, match="Only PNG"):
        await generate_images("test", workspace=tmp_path, references=("reference.png",))
    runtime.image_generation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("generated", [
    (),
    (GeneratedImage(b"not-an-image"),),
    (GeneratedImage(PNG, "image/jpeg"),),
    tuple(GeneratedImage(PNG) for _ in range(5)),
])
async def test_invalid_outputs_create_no_files(tmp_path, runtime, generated):
    runtime.image_generation.return_value = ImageGenerationResponse(images=generated)
    with pytest.raises(images.ImageGenerationError):
        await generate_images("test", workspace=tmp_path)
    assert not (tmp_path / "generated-images").exists()


@pytest.mark.asyncio
async def test_output_size_limit(tmp_path, runtime, monkeypatch):
    monkeypatch.setattr(images, "_MAX_OUTPUT_BYTES", 8)
    with pytest.raises(images.ImageGenerationError, match="exceeded"):
        await generate_images("test", workspace=tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
async def test_network_errors_are_sanitized(tmp_path, runtime, error_type):
    runtime.image_generation.side_effect = error_type("secret-key")
    with pytest.raises(images.ImageGenerationError) as error:
        await generate_images("test", workspace=tmp_path)
    assert "secret-key" not in str(error.value)
    assert runtime.image_generation.await_count == 1


@pytest.mark.asyncio
async def test_provider_errors_are_actionable(tmp_path, runtime):
    runtime.image_generation.side_effect = NotImplementedError("Model does not support image generation.")
    with pytest.raises(images.ImageGenerationError, match="does not support image generation"):
        await generate_images("test", workspace=tmp_path)
    assert runtime.image_generation.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("external_cancel", [False, True])
async def test_inflight_cancellation_cancels_provider_call(tmp_path, runtime, external_cancel):
    started = asyncio.Event()
    stopped = asyncio.Event()
    cancel = asyncio.Event()

    async def blocked(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    runtime.image_generation.side_effect = blocked
    task = asyncio.create_task(generate_images("test", workspace=tmp_path, cancel=cancel))
    await asyncio.wait_for(started.wait(), timeout=2)
    if external_cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    else:
        cancel.set()
        with pytest.raises(images.ImageGenerationError, match="cancelled"):
            await asyncio.wait_for(task, timeout=2)
    assert stopped.is_set()
    assert not (tmp_path / "generated-images").exists()


@pytest.mark.asyncio
async def test_cancelled_call_does_not_start_generation(tmp_path, runtime):
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(images.ImageGenerationError, match="cancelled"):
        await generate_images("test", workspace=tmp_path, cancel=cancel)
    runtime.image_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_total_deadline(tmp_path, runtime, monkeypatch):
    async def blocked(*args):
        await asyncio.Event().wait()

    runtime.image_generation.side_effect = blocked
    monkeypatch.setattr(images, "_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(images.ImageGenerationError, match="timed out"):
        await generate_images("test", workspace=tmp_path)


@pytest.mark.asyncio
async def test_tool_is_registered_and_returns_paths(tmp_path, runtime):
    assert "generate_image" in registered_names()
    tool = create_tools(enabled=("generate_image",), image_model=IMAGE_MODEL)[0]
    result = await tool.execute(
        "id", {"prompt": "test", "model": "antigravity/another-image"}, ctx={"workspace_dir": str(tmp_path)},
    )
    payload = json.loads(result.content[0].text)
    assert len(payload["paths"]) == 1
    assert runtime.image_generation.await_args.args[0] == IMAGE_MODEL
    assert "model" not in tool.parameters["properties"]
    assert "CLIPROXY" not in tool.description
    assert "inlineData" not in result.content[0].text


@pytest.mark.asyncio
async def test_missing_login_is_actionable(tmp_path, runtime):
    runtime.active_providers.return_value = ["copilot"]
    tool = images.create_generate_image_tool(model=IMAGE_MODEL)
    result = await tool.execute("id", {"prompt": "test"}, ctx={"workspace_dir": str(tmp_path)})
    assert "xdog-ai login antigravity" in result.content[0].text
    runtime.image_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_requires_explicit_workspace(runtime):
    tool = images.create_generate_image_tool(model=IMAGE_MODEL)
    result = await tool.execute("id", {"prompt": "test"})
    assert "requires a workspace" in result.content[0].text
    runtime.image_generation.assert_not_awaited()


def test_cli_generates_image_and_returns_json(tmp_path, runtime):
    save_config(
        ClawConfig(enabled_tools=("generate_image",), image_model="antigravity/another-image"),
        tmp_path / "config.yaml",
    )
    result = CliRunner().invoke(cli, [
        "generate-image", "Design a mini program",
        "--workspace", str(tmp_path), "--output-dir", "design",
        "--aspect-ratio", "1:1", "--image-size", "2K",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["paths"]) == 1
    assert str(tmp_path / "design") in payload["paths"][0]
    assert runtime.image_generation.await_args.args == (
        "antigravity/another-image",
        ImageGenerationRequest("Design a mini program", aspect_ratio="1:1", image_size="2K"),
    )


def test_cli_reports_missing_login(tmp_path, runtime):
    runtime.active_providers.return_value = []
    result = CliRunner().invoke(cli, ["generate-image", "test", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert "xdog-ai login antigravity" in result.output


@pytest.mark.asyncio
async def test_helper_and_factory_have_no_default_model(tmp_path):
    with pytest.raises(TypeError):
        await images.generate_images("test", workspace=tmp_path)
    with pytest.raises(ValueError, match="image_model configuration"):
        images.create_generate_image_tool(model="")
    assert not hasattr(images, "DEFAULT_MODEL")


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [None, ("text",)])
async def test_unconfirmed_image_support_fails_without_generation(tmp_path, runtime, output):
    runtime.model.side_effect = lambda _: Model(id=IMAGE_MODEL, output=output)
    with pytest.raises(images.ImageGenerationError, match="confirmed image_generation"):
        await generate_images("test", workspace=tmp_path)
    runtime.image_generation.assert_not_awaited()


@pytest.mark.parametrize("enabled,image_model,error", [
    (None, "", "disabled"),
    ((), IMAGE_MODEL, "disabled"),
    (("generate_image",), "", "No image model configured"),
])
def test_cli_requires_tool_enablement_and_model(tmp_path, runtime, enabled, image_model, error):
    save_config(ClawConfig(enabled_tools=enabled, image_model=image_model), tmp_path / "config.yaml")
    result = CliRunner().invoke(cli, ["generate-image", "test", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert error in result.output
    runtime.image_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_claw_to_native_antigravity_integration(tmp_path, monkeypatch):
    """Actual runtime/provider/codec, with only authentication and HTTP mocked."""
    vendor = AntigravityVendor()
    monkeypatch.setattr(vendor, "models", lambda: (Model(
        id=IMAGE_MODEL, provider="antigravity", api="google-generative-ai",
        input=("text", "image"), output=("image",),
    ),))
    monkeypatch.setattr(vendor.tokens, "get", AsyncMock(return_value=Credentials("native-token", "", 0, "project")))
    runtime = Runtime()
    runtime._active["antigravity"] = AntigravityProvider(vendor)
    monkeypatch.setattr(images.ai, "load", lambda: runtime)
    monkeypatch.setenv("CLIPROXY_API_BASE_URL", "https://wrong-proxy.invalid")

    def handler(request):
        assert request.url.host == "daily-cloudcode-pa.googleapis.com"
        assert request.url.path == "/v1internal:generateContent"
        assert request.headers["authorization"] == "Bearer native-token"
        body = json.loads(request.content)
        assert body["model"] == "gemini-3.1-flash-image"
        assert body["request"]["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]
        return httpx.Response(200, json={"response": {"candidates": [{"content": {"parts": [{
            "inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()},
        }]}}]}})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    result = await generate_images("test", workspace=tmp_path)
    assert result.paths[0].read_bytes() == PNG
