"""Lightweight native request projections used only for vendor auth headers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from xdog.ai.types import AssistantMessage, Context, ImageContent, ToolResultMessage, UserMessage


def _image(*, data: str = "", mime_type: str = "image/png") -> ImageContent:
    return ImageContent(data=data, mime_type=mime_type)


def _content_images(content: Any, *, image_type: str) -> tuple[ImageContent, ...]:
    if not isinstance(content, list):
        return ()
    images: list[ImageContent] = []
    for part in content:
        if not isinstance(part, Mapping) or part.get("type") != image_type:
            continue
        if image_type == "image":
            source = part.get("source")
            if not isinstance(source, Mapping):
                continue
            data = source.get("data")
            media_type = source.get("media_type")
            if source.get("type") == "base64" and isinstance(data, str) and isinstance(media_type, str):
                images.append(_image(data=data, mime_type=media_type))
            continue
        images.append(_image(data=_image_reference(part)))
    return tuple(images)


def _image_reference(part: Mapping[str, Any]) -> str:
    for key in ("image_url", "file_id", "data", "image_base64"):
        value = part.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            url = value.get("url")
            if isinstance(url, str):
                return url
    return "native-image"


def _message(
    role: Any,
    content: Any,
    images: tuple[ImageContent, ...],
) -> UserMessage | AssistantMessage | ToolResultMessage:
    if role == "user":
        if images:
            return UserMessage(content=images)
        return UserMessage(content=content if isinstance(content, str) else "")
    if role in ("tool", "function", "toolResult"):
        return ToolResultMessage(content=images)
    return AssistantMessage()


def _messages_context(raw_messages: Any, *, image_type: str) -> Context:
    if not isinstance(raw_messages, list):
        return Context()
    messages = tuple(
        _message(raw.get("role"), raw.get("content"), _content_images(raw.get("content"), image_type=image_type))
        for raw in raw_messages
        if isinstance(raw, Mapping)
    )
    return Context(messages=messages)


def anthropic_native_auth_context(body: Mapping[str, Any]) -> Context:
    """Project Anthropic roles and base64 images for Copilot auth."""
    return _messages_context(body.get("messages"), image_type="image")


def responses_native_auth_context(body: Mapping[str, Any]) -> Context:
    """Project Responses input roles and image presence for Copilot auth."""
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        return Context(messages=(UserMessage(content=raw_input),))
    return _messages_context(raw_input, image_type="input_image")


def chat_native_auth_context(body: Mapping[str, Any]) -> Context:
    """Project Chat message roles and image presence for Copilot auth."""
    return _messages_context(body.get("messages"), image_type="image_url")
