"""Bounded in-memory image chunk assembly; ownership/permissions belong to its caller."""
from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from typing import Any

from xdog.ai.types import GeneratedImage, ImageContent
from xdog.ai.utils.image_files import MAX_REFERENCE_BYTES, decode_image


class ImageUploads:
    def __init__(self, validate: Callable[[GeneratedImage], None] | None = None) -> None:
        self._pending: dict[str, tuple[str, bytearray, int]] = {}
        self._ready: dict[str, ImageContent] = {}
        self._bytes = 0
        self._validate = validate

    def chunk(self, request: dict[str, Any]) -> str:
        key, index, mime = request.get('id'), request.get('index'), request.get('mime_type')
        if not isinstance(key, str) or not key or len(key) > 96 or type(index) is not int or index < 0:
            raise ValueError('Invalid image upload identity/index')
        if (not isinstance(mime, str) or mime not in {'image/png', 'image/jpeg', 'image/webp'}
                or not isinstance(request.get('data'), str)
                or 'final' in request and type(request['final']) is not bool):
            raise ValueError('Unsupported image upload type/final flag')
        if len(request['data']) > 100000:
            raise ValueError('Image chunk too large')
        data = base64.b64decode(request['data'], validate=True)
        if key in self._ready:
            raise ValueError('Image ID already complete')
        if key not in self._pending:
            if index != 0 or len(self._pending) + len(self._ready) >= 16:
                raise ValueError('Image upload out of order or cache full')
            self._pending[key] = mime, bytearray(), 0
        actual_mime, buffer, expected = self._pending[key]
        if actual_mime != mime or expected != index:
            raise ValueError('Image upload is out of order')
        if len(buffer) + len(data) > MAX_REFERENCE_BYTES or self._bytes + len(data) > 40 * 1024 * 1024:
            self.discard(key)
            raise ValueError('Image upload exceeds size/cache limit')
        buffer.extend(data)
        self._bytes += len(data)
        self._pending[key] = mime, buffer, expected + 1
        if request.get('final') is True:
            if hashlib.sha256(buffer).hexdigest() != request.get('sha256'):
                self.discard(key)
                raise ValueError('Image upload checksum mismatch')
            encoded = base64.b64encode(buffer).decode('ascii')
            try:
                decoded = decode_image(encoded, mime, max_bytes=MAX_REFERENCE_BYTES)
                if self._validate is not None:
                    self._validate(decoded)
            except ValueError:
                self.discard(key)
                raise
            self._ready[key] = ImageContent(data=encoded, mime_type=mime)
            self._pending.pop(key)
            return 'ready'
        return 'partial'

    def contains(self, key: str) -> bool:
        return key in self._pending or key in self._ready

    @property
    def count(self) -> int:
        return len(self._pending) + len(self._ready)

    def resolve(self, keys: object) -> tuple[ImageContent, ...]:
        if keys is None:
            return ()
        if not isinstance(keys, list) or len(keys) > 4 or any(not isinstance(key, str) for key in keys):
            raise ValueError('Invalid image reference list')
        if len(set(keys)) != len(keys) or any(key not in self._ready for key in keys):
            raise ValueError('Images are missing, duplicated or not completely uploaded')
        return tuple(self._ready[key] for key in keys)

    def discard_refs(self, keys: object) -> None:
        if isinstance(keys, list):
            for key in keys:
                if isinstance(key, str):
                    self.discard(key)

    def discard(self, key: str) -> None:
        if key in self._pending:
            self._bytes -= len(self._pending.pop(key)[1])
        if key in self._ready:
            item = self._ready.pop(key)
            self._bytes -= len(base64.b64decode(item.data))

    def clear(self) -> None:
        self._pending.clear()
        self._ready.clear()
        self._bytes = 0
