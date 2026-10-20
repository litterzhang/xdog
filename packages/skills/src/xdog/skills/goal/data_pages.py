"""Exact bounded data frames; no journal, host routing, mutation or record quota."""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from xdog.skills.goal.codec import json_text

TOOL_BYTES = 256 * 1024
PAGE_BYTES = 64 * 1024


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
        return cls(offset, limit, force or 'offset' in params or 'limit' in params)


def response(build: Callable[[], object], selector: dict[str, object], continuation: dict[str, object],
             page: Page, *, committed: bool = False) -> str:
    """Run on an owned worker over immutable facts, never a mutable actor/runtime."""
    payload = json_text(build(), maximum=None)
    data = payload.encode('utf-8')
    if not page.explicit and len(data) <= TOOL_BYTES:
        return payload
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
