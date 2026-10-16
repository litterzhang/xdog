"""Bounded typed image/descriptor validation for retained historical ContextMessages.

No dialog requests, invocation grants, queue admission or transport-control state.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from xdog.ai.types import ImageContent
from xdog.ai.utils.image_files import decode_image

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_PROPOSAL_BYTES = 6 * 1024 * 1024
_MEDIA = frozenset({'image/png', 'image/jpeg', 'image/webp'})


def _images(value: Any) -> tuple[tuple[ImageContent, ...], list[dict[str, Any]]]:
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError('Queued input accepts at most four inline typed images')
    images, summaries = [], []
    size = 0
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'type', 'data', 'mime_type'} or row['type'] != 'image'
                or not isinstance(row['data'], str) or not isinstance(row['mime_type'], str)
                or row['mime_type'] not in _MEDIA):
            raise ValueError('Queued input images must be inline base64 data, never references')
        size += len(row['data'])
        if size > MAX_PROPOSAL_BYTES:
            raise ValueError('Queued image data exceeds the bounded protocol budget')
        decoded = decode_image(row['data'], row['mime_type'], max_bytes=MAX_IMAGE_BYTES)
        images.append(ImageContent(data=row['data'], mime_type=row['mime_type']))
        summaries.append({'mime_type': decoded.mime_type, 'bytes': len(decoded.data),
                          'sha256': hashlib.sha256(decoded.data).hexdigest()})
    return tuple(images), summaries


def _summaries(value: Any) -> None:
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError('Invalid queued image review summaries')
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'mime_type', 'bytes', 'sha256'}
                or not isinstance(row['mime_type'], str) or row['mime_type'] not in _MEDIA
                or type(row['bytes']) is not int
                or not 1 <= row['bytes'] <= MAX_IMAGE_BYTES or not isinstance(row['sha256'], str)
                or re.fullmatch('[a-f0-9]{64}', row['sha256']) is None):
            raise ValueError('Invalid queued image review summary')
