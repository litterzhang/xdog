"""RPC connection/session-owned chunk references; no files or implicit replay."""
from __future__ import annotations

from typing import Any

from xdog.ai.types import GeneratedImage, ImageContent
from xdog.ai.utils.image_uploads import ImageUploads
from xdog.coding.core.agent_session import AgentSession
from xdog.tui.attachments import attachment


def _validate(image: GeneratedImage) -> None:
    attachment(image.data, 'RPC upload')


class RpcImages:
    def __init__(self, session: AgentSession) -> None:
        self.session = session
        self.owner = session.session_id
        self.uploads = ImageUploads(_validate)
        self.retired: set[str] = set()

    def _owner(self, expected: Any) -> None:
        if self.owner != self.session.session_id:
            self.uploads.clear()
            self.owner = self.session.session_id
        if not isinstance(expected, str) or expected != self.owner:
            raise ValueError('Image upload session changed; no image reference fallback')

    def chunk(self, packet: dict[str, Any]) -> str:
        self._owner(packet.get('session_id'))
        if set(packet) - {'type', 'session_id', 'id', 'index', 'mime_type', 'data', 'final', 'sha256'}:
            raise ValueError('Unsupported image upload fields; no file or URI resolution')
        key = packet.get('id')
        if (not isinstance(key, str) or key in self.retired
                or not self.uploads.contains(key) and len(self.retired) + self.uploads.count >= 4096):
            raise ValueError('Image upload ID retired or connection capacity exhausted')
        return self.uploads.chunk(packet)

    def resolve(self, packet: dict[str, Any]) -> tuple[ImageContent, ...]:
        self._owner(packet.get('session_id'))
        keys = packet.get('image_refs')
        if not isinstance(keys, list):
            raise ValueError('image_refs must be an array of completed upload IDs')
        return self.uploads.resolve(keys)

    def consume(self, packet: dict[str, Any]) -> tuple[ImageContent, ...]:
        result = self.resolve(packet)
        keys = packet['image_refs']
        self.retired.update(keys)
        self.uploads.discard_refs(keys)
        return result

    def discard(self, packet: dict[str, Any]) -> None:
        self._owner(packet.get('session_id'))
        key = packet.get('id')
        if (not isinstance(key, str) or not key or len(key) > 96
                or key not in self.retired and not self.uploads.contains(key)
                and len(self.retired) + self.uploads.count >= 4096):
            raise ValueError('Invalid image discard identity/capacity')
        self.retired.add(key)
        self.uploads.discard(key)

    def close(self) -> None:
        self.uploads.clear()
