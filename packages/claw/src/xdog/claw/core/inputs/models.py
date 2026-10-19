"""Immutable submitted input and host routing, never authority inferred from labels."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from xdog.agent.session_input import MAX_BYTES, JsonValue, SessionInput, decode_input
from xdog.ai.types import ImageContent
from xdog.ai.utils.image_files import MAX_REFERENCE_BYTES, decode_image

FORMAT = 'xdog.claw.input.v2'
MAX_METADATA_BYTES = 64 * 1024
MAX_ENVELOPE_BYTES = MAX_BYTES + MAX_METADATA_BYTES + 4 * (((MAX_REFERENCE_BYTES + 2) // 3) * 4)


def _text(value: object, maximum: int = 256, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or '\x00' in value or len(value.encode()) > maximum
            or not empty and not value.strip()):
        raise ValueError('Invalid input identity or routing text')
    return value


def _object(value: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Invalid input envelope fields')
    return value



def _context(value: object, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError('Host input context nesting exceeds capacity')
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _context(item, depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _context(item, depth + 1)
        return
    raise ValueError('Host input context requires finite JSON')


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate input envelope field')
        result[key] = value
    return result


@dataclass(frozen=True)
class InputScope:
    group_id: str
    workspace: Path

    def __post_init__(self) -> None:
        _text(self.group_id)
        if not isinstance(self.workspace, Path):
            raise ValueError('Input scope requires a workspace Path')
        _text(str(self.workspace), 4096)
        if not self.workspace.is_absolute() or '..' in self.workspace.parts:
            raise ValueError('Input workspace must be absolute without parent traversal')


@dataclass(frozen=True)
class ReplyTarget:
    channel: str
    recipient: str

    def __post_init__(self) -> None:
        _text(self.channel)
        _text(self.recipient, 1024)


@dataclass(frozen=True, init=False)
class InputEnvelope:
    scope: InputScope
    _message_json: str
    _context_json: str
    reply: ReplyTarget | None
    images: tuple[ImageContent, ...]
    human_authorized: bool
    admission_key: str

    def __init__(self, scope: InputScope, message: SessionInput, reply: ReplyTarget | None = None,
                 images: tuple[ImageContent, ...] = (), human_authorized: bool = False,
                 admission_key: str = '', context: dict[str, JsonValue] | None = None) -> None:
        if not isinstance(scope, InputScope) or not isinstance(message, SessionInput):
            raise ValueError('Input requires a typed scope and canonical message')
        if reply is not None and not isinstance(reply, ReplyTarget):
            raise ValueError('Input requires a typed reply target')
        if type(human_authorized) is not bool or human_authorized and message.origin != 'human':
            raise ValueError('Only a host-identified human input may carry human authority')
        _text(admission_key, empty=True)
        if context is not None and not isinstance(context, dict):
            raise ValueError('Host input context requires an object')
        _context(context)
        context_json = json.dumps(context or {}, sort_keys=True, ensure_ascii=True, allow_nan=False)
        if len(context_json.encode()) > MAX_METADATA_BYTES:
            raise ValueError('Host input context exceeds capacity')
        if not isinstance(images, tuple) or len(images) > 4:
            raise ValueError('Input requires at most four typed images')
        for image in images:
            if (not isinstance(image, ImageContent) or not isinstance(image.data, str)
                    or not isinstance(image.mime_type, str)):
                raise ValueError('Input requires typed image data')
            decode_image(image.data, image.mime_type, max_bytes=MAX_REFERENCE_BYTES)
        # Sever all caller-owned JSON references. A provenance-shaped wrapper
        # cannot borrow the extra budget reserved for real transport bindings.
        checked = decode_input(asdict(message), transport_envelope=False)
        object.__setattr__(self, '_context_json', context_json)
        object.__setattr__(self, 'scope', scope)
        object.__setattr__(self, '_message_json', json.dumps(asdict(checked), sort_keys=True, ensure_ascii=True))
        object.__setattr__(self, 'reply', reply)
        object.__setattr__(self, 'images', images)
        object.__setattr__(self, 'human_authorized', human_authorized)
        object.__setattr__(self, 'admission_key', admission_key)

    @property
    def message(self) -> SessionInput:
        """Return an independent projection, not writable accepted JSON."""
        return decode_input(json.loads(self._message_json), transport_envelope=False)

    @property
    def context(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], json.loads(self._context_json))


def encode_envelope(value: InputEnvelope) -> str:
    metadata = {'format': FORMAT, 'scope': {'group_id': value.scope.group_id, 'workspace': str(value.scope.workspace)},
                'reply': asdict(value.reply) if value.reply is not None else None,
                'human_authorized': value.human_authorized, 'admission_key': value.admission_key,
                'context': value.context}
    if len(json.dumps(metadata, ensure_ascii=True).encode()) > MAX_METADATA_BYTES:
        raise ValueError('Input envelope metadata exceeds capacity')
    payload = json.dumps({**metadata, 'message': json.loads(value._message_json),
                          'images': [{'data': image.data, 'mime_type': image.mime_type} for image in value.images]},
                         sort_keys=True, ensure_ascii=True, allow_nan=False)
    if len(payload.encode()) > MAX_ENVELOPE_BYTES:
        raise ValueError('Input envelope exceeds capacity')
    return payload


def decode_envelope(payload: str) -> InputEnvelope:
    if not isinstance(payload, str) or len(payload.encode()) > MAX_ENVELOPE_BYTES:
        raise ValueError('Input envelope exceeds capacity')
    data = _object(json.loads(payload, object_pairs_hook=_unique),
                   {'format', 'scope', 'message', 'reply', 'images', 'human_authorized', 'admission_key', 'context'})
    if data['format'] != FORMAT:
        raise ValueError('Unsupported input envelope format')
    scope = _object(data['scope'], {'group_id', 'workspace'})
    reply = _object(data['reply'], {'channel', 'recipient'}) if data['reply'] is not None else None
    raw_images = data['images']
    if not isinstance(raw_images, list) or len(raw_images) > 4:
        raise ValueError('Invalid saved input images')
    images = [_object(row, {'data', 'mime_type'}) for row in raw_images]
    result = InputEnvelope(InputScope(_text(scope['group_id']), Path(_text(scope['workspace'], 4096))),
        decode_input(data['message'], transport_envelope=False),
        ReplyTarget(_text(reply['channel']), _text(reply['recipient'], 1024)) if reply is not None else None,
        tuple(ImageContent(data=row['data'], mime_type=row['mime_type']) for row in images),
        data['human_authorized'], data['admission_key'], data['context'])
    encode_envelope(result)  # Apply the same metadata and total budgets on both boundaries.
    return result
