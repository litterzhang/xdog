"""Bounded idle extension-message proposals; image bytes remain host-private."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import asdict, dataclass, field
from typing import Any

from xdog.ai.types import ImageContent


def text(value: Any) -> str:
    # This original nonempty validator is also used by queue/context callers.
    if (not isinstance(value, str) or not value.strip() or '\x00' in value
            or len(value.encode('utf-8')) > 16384):
        raise ValueError('Requested message must be nonempty text, at most 16 KiB, without NUL')
    return value


def _message_text(value: Any, images: bool) -> str:
    if images and isinstance(value, str) and not value.strip() and '\x00' not in value and len(value.encode()) <= 16384:
        return value
    return text(value)


def host_message(extension: str, value: Any, *, image_only: bool = False) -> str:
    if not isinstance(extension, str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', extension) is None:
        raise ValueError('Requested message requires a registered extension identity')
    return f'[User-approved message requested by extension {extension}]\n\n' + _message_text(value, image_only)


def _digest(content: str, summaries: Any) -> str:
    return hashlib.sha256(json.dumps({'host_message': content, 'image_summaries': summaries},
                                    ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def request(value: dict[str, Any]) -> None:
    from xdog.agent.extensions.queued_input import MAX_PROPOSAL_BYTES, _images, _summaries
    allowed = {'id', 'kind', 'operation', 'title', 'message', 'timeout', 'extension', 'parent_id',
               'text', 'images', 'host_message', 'image_summaries', 'message_review', 'message_digest',
               'message_generation', 'message_images_generation'}
    if value.keys() - allowed:
        raise ValueError('Idle message contains unsupported fields or borrowed input scope')
    if any(key in value for key in ('target', 'session_id', 'branch_id', 'scope', 'index', 'revision',
                                    'delivery', 'image_refs', 'origin')):
        raise ValueError('Requested message cannot borrow another input scope, queue or attachments')
    images, _ = _images(value.get('images', []))
    projected = any(key in value for key in ('host_message', 'image_summaries', 'message_review', 'message_digest'))
    for key in ('message_generation', 'message_images_generation'):
        if key in value and (not projected or type(value[key]) is not int or not 0 <= value[key] < 2**53):
            raise ValueError('Message generation metadata requires a valid host-projected review')
    if projected:
        if (value.get('images') != [] or not isinstance(value.get('host_message'), str)
                or len(value['host_message'].encode()) > 17000
                or not isinstance(value.get('message_review'), str)
                or re.fullmatch('[a-f0-9]{32}', value['message_review']) is None):
            raise ValueError('Message review requires host-derived metadata, never image bodies')
        _summaries(value.get('image_summaries'))
        if value.get('message_digest') != _digest(value['host_message'], value['image_summaries']):
            raise ValueError('Message review metadata digest mismatch')
    _message_text(value.get('text'), bool(images) or projected and bool(value['image_summaries']))
    if len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) > MAX_PROPOSAL_BYTES:
        raise ValueError('Idle message proposal exceeds bounded worker frame budget')


def proposal(value: str, images: tuple[ImageContent, ...] = ()) -> dict[str, Any]:
    if not isinstance(images, tuple) or len(images) > 4 or any(type(image) is not ImageContent for image in images):
        raise ValueError('Idle message images must be an explicit tuple of typed ImageContent values')
    result = {'text': _message_text(value, bool(images)), 'images': [asdict(image) for image in images]}
    request(result)
    return result


@dataclass(frozen=True, slots=True)
class PreparedMessage:
    content: str
    images: tuple[ImageContent, ...] = field(repr=False)
    review: dict[str, Any] = field(repr=False)
    digest: str

    def matches(self, value: dict[str, Any]) -> bool:
        try:
            return (value.get('message_review') == self.review['message_review']
                    and value.get('images') == [] and value.get('host_message') == self.content
                    and value.get('message_digest') == self.digest
                    and _digest(value['host_message'], value.get('image_summaries')) == self.digest)
        except (ValueError, TypeError, KeyError):
            return False


def prepare(extension: str, value: dict[str, Any]) -> PreparedMessage:
    from xdog.agent.extensions.queued_input import _images
    if any(key in value for key in ('host_message', 'image_summaries', 'message_review', 'message_digest',
                                    'message_generation', 'message_images_generation')):
        raise ValueError('Worker cannot supply its own message review or attachment grants')
    request(value)
    images, summaries = _images(value.get('images', []))
    content = host_message(extension, value['text'], image_only=bool(images))
    digest = _digest(content, summaries)
    review = {**value, 'images': [], 'host_message': content, 'image_summaries': summaries,
              'message_review': secrets.token_hex(16), 'message_digest': digest}
    request(review)
    return PreparedMessage(content, images, review, digest)
