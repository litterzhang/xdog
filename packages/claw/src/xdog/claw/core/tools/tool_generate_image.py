"""Workspace-scoped image generation using xdog.ai's authenticated providers."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import xdog.ai as ai
from xdog.agent import AgentTool, AgentToolResult
from xdog.ai.types import ImageGenerationRequest, TextContent
from xdog.ai.utils.image_files import read_reference, save_images

ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
IMAGE_SIZES = ("1K", "2K", "4K")
_MAX_REFERENCES = 4
_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_TIMEOUT_SECONDS = 180.0


class ImageGenerationError(ValueError):
    """An actionable error safe to return to the agent."""


@dataclass(frozen=True)
class GeneratedImages:
    paths: tuple[Path, ...]
    text: str = ""

    def to_json(self) -> str:
        return json.dumps({"paths": [str(path) for path in self.paths], "text": self.text}, ensure_ascii=False)


def _workspace_path(workspace: Path, value: str) -> Path:
    path = (workspace / value).resolve()
    if not path.is_relative_to(workspace):
        raise ImageGenerationError("Image paths must stay inside the workspace.")
    return path


async def _with_cancellation(
    operation: Coroutine[Any, Any, GeneratedImages],
    cancel: asyncio.Event | None,
) -> GeneratedImages:
    task = asyncio.create_task(operation)
    stop = asyncio.create_task(cancel.wait()) if cancel is not None else None
    try:
        if stop is not None:
            done, _ = await asyncio.wait((task, stop), return_when=asyncio.FIRST_COMPLETED)
            if stop in done:
                raise ImageGenerationError("Image generation cancelled.")
        return await task
    finally:
        tasks = [task] if stop is None else [task, stop]
        for pending in tasks:
            if not pending.done():
                pending.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def generate_images(
    prompt: str,
    *,
    workspace: Path,
    model: str,
    output_dir: str = "generated-images",
    references: tuple[str, ...] = (),
    aspect_ratio: str = "9:16",
    image_size: str = "1K",
    cancel: asyncio.Event | None = None,
) -> GeneratedImages:
    """Generate via xdog.ai; own only workspace confinement and file storage.

    Path checks prevent accidental access, not hostile code in a shared
    workspace. No automatic retries: another generation may consume quota.
    """
    if not prompt.strip():
        raise ImageGenerationError("An image prompt is required.")
    if aspect_ratio not in ASPECT_RATIOS or image_size not in IMAGE_SIZES:
        raise ImageGenerationError("Unsupported aspect ratio or image size.")
    if len(references) > _MAX_REFERENCES:
        raise ImageGenerationError("At most four reference images are supported.")
    provider_id, separator, short_name = model.partition("/")
    if not separator or not provider_id.strip() or not short_name.strip():
        raise ImageGenerationError("Image model must use provider/model format.")
    if cancel is not None and cancel.is_set():
        raise ImageGenerationError("Image generation cancelled.")
    workspace = workspace.resolve()
    output = _workspace_path(workspace, output_dir)
    if output.exists() and not output.is_dir():
        raise ImageGenerationError("Output directory is an existing file.")
    reference_paths = [_workspace_path(workspace, name) for name in references]

    async def run() -> GeneratedImages:
        runtime = ai.load()
        if provider_id not in runtime.active_providers():
            raise ImageGenerationError(
                f"Image provider {provider_id!r} is not logged in. Run `xdog-ai login {provider_id}` first."
            )
        info = runtime.model(model)
        if info is None or info.supports_image_output is not True:
            raise ImageGenerationError(
                f"Model {model!r} does not have confirmed image_generation support. "
                "Run `xdog-claw onboard` to select a supported image model."
            )
        reference_images = tuple([await asyncio.to_thread(read_reference, path) for path in reference_paths])
        result = await runtime.image_generation(model, ImageGenerationRequest(
            prompt=prompt, reference_images=reference_images, aspect_ratio=aspect_ratio, image_size=image_size,
        ))
        if len(result.images) > 4:
            raise ImageGenerationError("Provider returned more than four images; no files were saved.")
        if sum(len(image.data) for image in result.images) > _MAX_OUTPUT_BYTES:
            raise ImageGenerationError("Generated images exceeded 32 MiB; no files were saved.")
        if cancel is not None and cancel.is_set():
            raise ImageGenerationError("Image generation cancelled.")
        # Recheck after the remote operation in case the output path changed.
        output = _workspace_path(workspace, output_dir)
        paths = await asyncio.to_thread(save_images, result.images, output)
        return GeneratedImages(paths=paths, text=result.text[:2000])

    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            return await _with_cancellation(run(), cancel)
    except (TimeoutError, httpx.TimeoutException):
        raise ImageGenerationError("Image generation timed out; check provider status before retrying.") from None
    except httpx.HTTPError:
        raise ImageGenerationError("Image provider network request failed.") from None
    except OSError:
        raise ImageGenerationError(
            "Cannot read reference images or save output files; check paths and permissions."
        ) from None
    except (ValueError, RuntimeError, KeyError) as exc:
        raise ImageGenerationError(str(exc)) from None


def create_generate_image_tool(*, model: str) -> AgentTool:
    if not model:
        raise ValueError("generate_image requires image_model configuration. Run `xdog-claw onboard` first.")

    async def execute(
        tool_call_id: str,
        args: dict[str, Any],
        cancel: asyncio.Event | None = None,
        on_update: Any = None,
        *,
        ctx: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        context = ctx or {}
        workspace = context.get("workspace_dir") or context.get("fs_workspace")
        if not workspace:
            return AgentToolResult(content=(TextContent(text="Error: image generation requires a workspace."),))
        try:
            result = await generate_images(
                args.get("prompt", ""),
                workspace=Path(workspace),
                output_dir=args.get("output_dir", "generated-images"),
                references=tuple(args.get("reference_images", [])),
                aspect_ratio=args.get("aspect_ratio", "9:16"),
                image_size=args.get("image_size", "1K"),
                model=model,
                cancel=cancel,
            )
            return AgentToolResult(content=(TextContent(text=result.to_json()),))
        except ImageGenerationError as exc:
            return AgentToolResult(content=(TextContent(text=f"Error: {exc}"),))

    return AgentTool(
        name="generate_image",
        label="Generate image",
        description=(
            f"Generate or edit image assets/UI mockups using the configured xdog.ai model {model}. "
            "Saves PNG/JPEG/WebP files in the workspace and returns local paths, not image data. "
            "Optional reference_images are workspace files sent to the model. "
            "Log in with xdog-ai login <provider> first; no proxy configuration is required. "
            "Consumes image-model quota; use only when the user requests image generation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1, "description": "Describe the image or requested edits."},
                "output_dir": {"type": "string", "description": "Workspace directory; default generated-images."},
                "reference_images": {
                    "type": "array", "items": {"type": "string"}, "maxItems": _MAX_REFERENCES,
                    "description": "Up to four workspace PNG/JPEG/WebP reference files, each at most 10 MiB.",
                },
                "aspect_ratio": {"type": "string", "enum": list(ASPECT_RATIOS)},
                "image_size": {"type": "string", "enum": list(IMAGE_SIZES)},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
        execute=execute,
    )
