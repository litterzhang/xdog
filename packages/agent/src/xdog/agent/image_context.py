"""Admission for the complete rendered image context on the configured model adapter."""
from __future__ import annotations

import asyncio
import base64
import binascii
import re
from typing import Any

from xdog.agent.core import ModelStreamFn, StreamFn
from xdog.ai.types import Context, ImageContent, Model, VisionLimits
from xdog.ai.utils.image_files import MAX_IMAGE_BYTES


def _profile(model: Model) -> tuple[Any, ...]:
    if (not isinstance(model.id, str) or not model.id or len(model.id.encode()) > 512
            or model.input is not None and (not isinstance(model.input, tuple)
                or any(kind not in ('text', 'image') for kind in model.input))):
        raise ValueError('Invalid selected image model identity/modalities')
    limits = model.vision_limits
    if limits is not None:
        if (not isinstance(limits, VisionLimits) or type(limits.max_prompt_images) is not int
                or limits.max_prompt_images < 0 or type(limits.max_prompt_image_size) is not int
                or limits.max_prompt_image_size < 0 or not isinstance(limits.supported_media_types, tuple)
                or any(not isinstance(kind, str) for kind in limits.supported_media_types)):
            raise ValueError('Invalid image capabilities on configured model adapter')
    return model.id, model.input, limits


def _validate(images: tuple[ImageContent, ...], model: Model) -> None:
    if model.supports_image_input is not True:
        raise ValueError('Selected model does not advertise the actual rendered image context')
    limits = model.vision_limits
    maximum = limits.max_prompt_images if limits is not None else 0
    if maximum and len(images) > maximum:
        raise ValueError('Actual rendered image context exceeds selected model image-count limits; '
                         'choose a compatible model or explicitly compact the session. No images were dropped')
    size = limits.max_prompt_image_size if limits is not None else 0
    bound = 'selected model image-byte limits' if size else 'the neutral image payload safety bound'
    size = size or MAX_IMAGE_BYTES  # Existing neutral payload bound, NOT the composer four-image count.
    media = limits.supported_media_types if limits is not None else ()
    for image in images:
        if (not isinstance(image.data, str) or not isinstance(image.mime_type, str)
                or re.fullmatch(r'image/[A-Za-z0-9][A-Za-z0-9.+-]{0,127}', image.mime_type) is None):
            raise ValueError('Invalid typed image in rendered model context')
        if media and image.mime_type not in media:
            raise ValueError('Actual rendered image context has an unsupported model MIME type')
        if len(image.data) > ((size + 2) // 3) * 4:
            raise ValueError('Actual rendered image context exceeds ' + bound)
        try:
            data = base64.b64decode(image.data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Invalid base64 image in rendered model context') from None
        if not data:
            raise ValueError('Invalid empty image in rendered model context')
        if len(data) > size:
            raise ValueError('Actual rendered image context exceeds ' + bound)


def validate_model_images(images: tuple[ImageContent, ...], model: Model) -> None:
    """Validate a full rendered image collection, not the per-draft composer limit."""
    _profile(model)
    _validate(images, model)


async def validate_image_context(stream: StreamFn, model: str, context: Context) -> None:
    images = tuple(part for message in context.messages
                   for part in (() if isinstance(message.content, str) else message.content)
                   if isinstance(part, ImageContent))
    if not images or not isinstance(stream, ModelStreamFn):
        return
    selected = stream.resolve_model(model)
    if selected is None:
        return  # Optional/opaque SDK metadata is unavailable, never borrowed from a global provider.
    if not isinstance(selected, Model):
        raise ValueError('Invalid model metadata for rendered image context')
    captured = _profile(selected)
    await asyncio.to_thread(validate_model_images, images, selected)
    current = stream.resolve_model(model)
    if not isinstance(current, Model) or _profile(current) != captured:
        raise ValueError('Selected model image capabilities changed during rendered-context admission; no request')
