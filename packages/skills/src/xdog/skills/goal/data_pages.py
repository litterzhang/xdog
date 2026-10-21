"""Exact bounded data frames; no journal, host routing, mutation or record quota."""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from xdog.skills.goal.codec import json_text

TOOL_BYTES = 256 * 1024
PAGE_BYTES = 64 * 1024


def snapshot_revision(value: object, selector: dict[str, object]) -> str:
    """Pin both the selected content and its query, not a Goal record version."""
    return hashlib.sha256(json_text({'selector': selector, 'value': value}, maximum=None).encode()).hexdigest()


@dataclass(frozen=True)
class Page:
    offset: int = 0
    limit: int = PAGE_BYTES
    explicit: bool = False

    @classmethod
    def read(cls, params: dict[str, Any], *, force: bool = False) -> Page:
        offset, limit = params.get('offset', 0), params.get('limit', PAGE_BYTES)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= PAGE_BYTES:
            raise ValueError('Data page offset/limit require strict nonnegative/bounded integers')
        revision = params.get('revision')
        if 'revision' in params and (not isinstance(revision, str) or len(revision) != 64
                                     or any(char not in '0123456789abcdef' for char in revision)):
            raise ValueError('Data page revision must be a SHA-256 snapshot hash')
        if offset and revision is None:
            raise ValueError('Continuation offset requires revision; restart reading from offset 0')
        return cls(offset, limit, force or 'offset' in params or 'limit' in params)


def response(build: Callable[[], object], selector: dict[str, object], continuation: dict[str, object],
             page: Page, *, committed: bool = False) -> str:
    """Run on an owned worker over immutable facts, never a mutable actor/runtime."""
    value = build()
    if not page.explicit:
        # Human-facing results use real Unicode, never blindly unescape JSON text.
        readable = json_text(value, maximum=None, ensure_ascii=False)
        if len(readable.encode('utf-8')) <= TOOL_BYTES:
            return readable
    # Keep the existing ASCII byte-page codec, offsets and hashes unchanged.
    payload = json_text(value, maximum=None)
    data = payload.encode('utf-8')
    if page.offset > len(data):
        raise ValueError('Data page offset exceeds the selected immutable snapshot')
    end = min(len(data), page.offset + page.limit)
    result: dict[str, object] = {'format': 'xdog.goal_data_page.v1', 'selector': selector,
        'sha256': hashlib.sha256(data).hexdigest(), 'offset': page.offset, 'total_bytes': len(data),
        'text': data[page.offset:end].decode('ascii'),
        'next_offset': end if end < len(data) else None,
        'next_query': {**continuation, 'offset': end, 'limit': page.limit} if end < len(data) else None}
    if committed:
        result['snapshot_committed'] = True
    return json_text(result, maximum=TOOL_BYTES)
