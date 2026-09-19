"""Typed Gemini image requests/responses, separate from OAuth and file storage."""
from __future__ import annotations

import re
from typing import Any

from xdog.ai.types import GeneratedImage, ImageGenerationRequest, ImageGenerationResponse, Model, Usage
from xdog.ai.utils.image_files import MAX_REFERENCE_BYTES, decode_image


def encode_image_request(request: ImageGenerationRequest, model: Model) -> dict[str, Any]:
    if not request.prompt.strip():
        raise ValueError("An image-generation prompt is required.")
    if model.supports_image_output is False:
        raise NotImplementedError(f"Model {model.id!r} does not support image generation.")
    if request.reference_images and model.supports_image_input is False:
        raise NotImplementedError(f"Model {model.id!r} does not support image input.")
    if len(request.reference_images) > 4:
        raise ValueError("At most four reference images are supported.")
    parts: list[dict[str, Any]] = [{"text": request.prompt}]
    for image in request.reference_images:
        decode_image(image.data, image.mime_type, max_bytes=MAX_REFERENCE_BYTES)
        parts.append({"inlineData": {"mimeType": image.mime_type, "data": image.data}})
    generation: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"], "candidateCount": 1}
    image_config: dict[str, str] = {}
    if request.aspect_ratio is not None:
        if re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", request.aspect_ratio) is None:
            raise ValueError("Aspect ratio must be positive integers separated by ':', e.g. 9:16.")
        image_config["aspectRatio"] = request.aspect_ratio
    if request.image_size is not None:
        if request.image_size not in ("1K", "2K", "4K"):
            raise ValueError("Image size must be 1K, 2K, or 4K.")
        image_config["imageSize"] = request.image_size
    if image_config:
        generation["imageConfig"] = image_config
    return {"contents": [{"role": "user", "parts": parts}], "generationConfig": generation}


def decode_image_response(payload: Any, model: Model) -> ImageGenerationResponse:
    if not isinstance(payload, dict):
        raise ValueError("Invalid Gemini image-generation response.")
    feedback = payload.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise ValueError("Image generation was blocked by the provider.")
    if payload.get("error"):
        raise ValueError("The provider returned an image-generation error.")
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("Invalid Gemini image candidates.")
    images: list[GeneratedImage] = []
    text: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("finishReason") not in (None, "STOP"):
            raise ValueError("Image generation stopped before successful completion.")
        content = candidate.get("content")
        if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
            continue
        for part in content["parts"]:
            if not isinstance(part, dict) or part.get("thought") is True:
                continue
            if isinstance(part.get("text"), str):
                text.append(part["text"])
            inline = part.get("inlineData", part.get("inline_data"))
            if not isinstance(inline, dict):
                continue
            data = inline.get("data")
            mime = inline.get("mimeType", inline.get("mime_type"))
            if not isinstance(data, str) or not isinstance(mime, str):
                raise ValueError("Image response is missing base64 data or MIME type.")
            images.append(decode_image(data, mime))
    if not images:
        raise ValueError("No image returned; the model may have refused or returned only text.")
    raw_usage = payload.get("usageMetadata", {})
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}

    def count(key: str) -> int:
        value = raw_usage.get(key)
        return value if type(value) is int and value >= 0 else 0

    cached = count("cachedContentTokenCount")
    return ImageGenerationResponse(
        images=tuple(images), text="\n".join(text), model=model.id, provider=model.provider,
        usage=Usage(
            input=max(0, count("promptTokenCount") - cached),
            output=count("candidatesTokenCount") + count("thoughtsTokenCount"),
            cache_read=cached, total_tokens=count("totalTokenCount"),
        ),
    )
