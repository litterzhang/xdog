"""Local child editor snapshots and exact accepted-input receipts, never parent drafts."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from xdog.tui.attachments import Attachment


@dataclass(frozen=True)
class ChildDraft:
    text: str = ''
    access: str = 'read-only'
    config: dict[str, Any] = field(default_factory=dict)
    images: tuple[Attachment, ...] = ()
    uncertain: bool = False
    cursor: int | None = None


def draft_receipt(text: str, images: tuple[Attachment, ...]) -> dict[str, Any]:
    return {'content': text, 'image_sha256': [hashlib.sha256(part.data).hexdigest() for part in images],
            'image_mime_types': [part.mime_type for part in images]}
