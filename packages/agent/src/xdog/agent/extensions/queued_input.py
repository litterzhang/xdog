"""Reviewed active-run input proposals; never a new-turn or composer capability."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, Literal, cast

from xdog.agent.extensions.invocation import current_invocation
from xdog.agent.extensions.message_request import host_message, text
from xdog.ai.types import ImageContent
from xdog.ai.utils.image_files import decode_image

Delivery = Literal['steer', 'follow_up']
METHODS = frozenset({'tools/call', 'xdog/pre', 'xdog/post', 'xdog/input', 'xdog/event'})
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_PROPOSAL_BYTES = 6 * 1024 * 1024  # Reserve 2 MiB for the ordinary 8-MiB worker frame envelope.
_COMMON = {'id', 'kind', 'operation', 'title', 'message', 'timeout', 'extension', 'parent_id'}
_FIELDS = _COMMON | {'text', 'delivery', 'images', 'host_message', 'image_summaries'}
_MEDIA = frozenset({'image/png', 'image/jpeg', 'image/webp'})


def delivery(value: Any) -> Delivery:
    if not isinstance(value, str) or value not in ('steer', 'follow_up'):
        raise ValueError('Queued input delivery must be steer or follow_up')
    return cast(Delivery, value)


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


def request(value: dict[str, Any]) -> None:
    """Validate either worker data or the host's binary-free broker projection.

    The host adapter rejects worker-supplied projection fields and recomputes them;
    accepting their shape here only allows the same broker to validate host review.
    """
    if not isinstance(value, dict) or value.keys() - _FIELDS:
        raise ValueError('Queued input cannot select another owner or borrow attachments')
    text(value.get('text'))
    delivery(value.get('delivery'))
    _images(value.get('images'))
    projected = 'host_message' in value or 'image_summaries' in value
    if projected:
        if (value.get('images') != [] or not isinstance(value.get('host_message'), str)
                or len(value['host_message'].encode()) > 17000):
            raise ValueError('Queued input review must not contain image bodies')
        _summaries(value.get('image_summaries'))
    try:
        size = len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode())
    except (TypeError, ValueError, UnicodeError):
        raise ValueError('Invalid queued input JSON') from None
    if size > MAX_PROPOSAL_BYTES:
        raise ValueError('Queued input exceeds the bounded protocol budget')


def proposal(value: str, mode: Delivery, images: tuple[ImageContent, ...]) -> dict[str, Any]:
    if not isinstance(images, tuple) or len(images) > 4 or any(type(image) is not ImageContent for image in images):
        raise ValueError('Queued input images must be a tuple of typed ImageContent values')
    result = {'text': text(value), 'delivery': delivery(mode), 'images': [asdict(image) for image in images]}
    request(result)
    return result


async def reviewed(extension: str, dialog: dict[str, Any], *,
                   ask: Callable[[str, dict[str, Any]], Awaitable[Any]],
                   apply: Callable[[str, str, Delivery, tuple[ImageContent, ...]], Awaitable[Any]],
                   alive: Callable[[], bool]) -> bool:
    """Review once, then defer queue admission until original callback success.

    ``alive`` must bind the host's original active/streaming run/session/profile,
    not merely a worker or UI connection. ``apply`` performs canonical source,
    model/profile, queue and image admission and publishes its own receipts.
    """
    lease = current_invocation()
    if (lease is None or lease.extension != extension or lease.method not in METHODS or not alive()
            or dialog.get('kind') != 'session' or dialog.get('operation') != 'queue_input'):
        return False
    if 'host_message' in dialog or 'image_summaries' in dialog:
        raise ValueError('Queued input cannot supply its own host review metadata')
    request(dialog)
    selected_text, selected_delivery = host_message(extension, dialog['text']), delivery(dialog['delivery'])
    selected_images, summaries = _images(dialog['images'])
    # Only scalars and independently rebuilt summaries cross to the renderer.
    projected = {**dialog, 'images': [], 'host_message': selected_text, 'image_summaries': summaries}
    identifier = 'extq-' + uuid.uuid4().hex
    if await ask(extension, projected) is not True or not alive() or current_invocation() is not lease:
        return False

    async def commit() -> None:
        if lease.owner_alive() and alive():
            await apply(identifier, selected_text, selected_delivery, selected_images)

    return lease.defer(commit)
