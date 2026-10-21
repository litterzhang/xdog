"""Child image input and retained-byte budgets, separate from text/transcript limits."""
from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from xdog.agent.core import AgentMessage
from xdog.agent.messages import messages_to_dicts
from xdog.ai.types import ImageContent, ToolResultMessage, UserMessage
from xdog.ai.utils.image_files import MAX_REFERENCE_BYTES, decode_image

MAX_CHILD_IMAGE_BYTES = 40 * 1024 * 1024
MAX_CHILD_ENCODED_IMAGES = ((MAX_CHILD_IMAGE_BYTES + 2) // 3) * 4


def validate_images(images: tuple[ImageContent, ...]) -> None:
    if not isinstance(images, tuple) or len(images) > 4 or any(not isinstance(part, ImageContent) for part in images):
        raise ValueError('Child input requires at most four typed images')
    for part in images:
        if not isinstance(part.data, str) or not isinstance(part.mime_type, str):
            raise ValueError('Invalid typed child image')
        decode_image(part.data, part.mime_type, max_bytes=MAX_REFERENCE_BYTES)
    check_retained(images)


def image_parts(messages: Iterable[AgentMessage]) -> tuple[ImageContent, ...]:
    return tuple(part for message in messages if isinstance(message, (UserMessage, ToolResultMessage))
                 and not isinstance(message.content, str)
                 for part in message.content if isinstance(part, ImageContent))


def check_retained(images: Iterable[ImageContent]) -> None:
    # A selected queue row can also appear in canonical history. Count identical
    # immutable payloads once; no decoding or large JSON copies on the owner loop.
    _check_bytes(set(images))


def check_collection(images: Iterable[ImageContent]) -> None:
    """Bound serialized occurrences too, including repeated identical queue inputs."""
    _check_bytes(images)


def _check_bytes(images: Iterable[ImageContent]) -> None:
    if sum((len(part.data) * 3 // 4 - (2 if part.data.endswith('==') else 1 if part.data.endswith('=') else 0))
           for part in images) > MAX_CHILD_IMAGE_BYTES:
        raise ValueError('Child retained image budget exceeds 40 MiB; no image truncation')


def text_size(messages: tuple[AgentMessage, ...]) -> int:
    stripped = [replace(message, content=tuple(replace(part, data='') if isinstance(part, ImageContent) else part
                  for part in message.content)) if isinstance(message, (UserMessage, ToolResultMessage))
                and not isinstance(message.content, str) else message for message in messages]
    return len(json.dumps(messages_to_dicts(stripped)).encode('utf-8'))


def images_to_dicts(images: tuple[ImageContent, ...]) -> list[dict[str, str]]:
    return [{'data': part.data, 'mime_type': part.mime_type} for part in images]


def images_from_dicts(value: Any) -> tuple[ImageContent, ...]:
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError('Invalid saved child task images')
    result = []
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'data', 'mime_type'}
                or not isinstance(row['data'], str) or not isinstance(row['mime_type'], str)):
            raise ValueError('Invalid saved child task image')
        result.append(ImageContent(data=row['data'], mime_type=row['mime_type']))
    images = tuple(result)
    validate_images(images)
    return images


def input_receipt(text: str, images: tuple[ImageContent, ...]) -> dict[str, Any]:
    return {'content': text, 'image_sha256': [hashlib.sha256(base64.b64decode(part.data, validate=True)).hexdigest()
                                            for part in images],
            'image_mime_types': [part.mime_type for part in images]}
