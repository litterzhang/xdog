"""Private editor CAS for reviewed extension proposals; never returns draft contents."""
from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Snapshot = tuple[str, int, tuple[str, ...]]


@dataclass
class _Review:
    identity: dict[str, str]
    extension: str
    text: str
    revision: str
    captured: Snapshot
    snapshot: Callable[[], Snapshot]
    replace: Callable[[str], bool]
    alive: Callable[[], bool]
    expires: float
    approved: bool = False


class EditorWriteReviews:
    """Host-owned, bounded, single-use review receipts; no turn/tool authority."""
    def __init__(self) -> None:
        self._pending: dict[str, _Review] = {}

    def prepare(self, dialog: dict[str, Any], snapshot: Callable[[], Snapshot],
                replace: Callable[[str], bool], alive: Callable[[], bool]) -> bool:
        self.prune()
        identity = dialog.get('editor_write')
        if (not isinstance(identity, dict) or set(identity) != {'id', 'invocation', 'session_id', 'child_id', 'method'}
                or any(not isinstance(value, str) for value in identity.values())
                or not identity['id'] or not identity['invocation'] or not alive()
                or identity['id'] in self._pending or len(self._pending) >= 8):
            return False
        self._pending[identity['id']] = _Review(dict(identity), dialog['extension'], dialog['text'],
            secrets.token_hex(32), snapshot(), snapshot, replace, alive,
            time.monotonic() + min(600, float(dialog.get('timeout', 300))))
        return True

    def answer(self, dialog: dict[str, Any], approved: bool) -> dict[str, Any]:
        self.prune()
        identity = dialog.get('editor_write', {})
        item = self._pending.get(identity.get('id'))
        accepted = bool(approved is True and item is not None and item.identity == identity and not item.approved
                        and item.alive() and item.snapshot() == item.captured)
        if item is not None:
            item.approved = accepted
        return {'approved': accepted, 'revision': item.revision if accepted and item is not None else ''}

    def apply(self, event: dict[str, Any]) -> bool:
        self.prune()
        identity = event.get('editor_write', {})
        item = self._pending.pop(identity.get('id'), None)
        return bool(item is not None and item.approved and item.identity == identity
            and event.get('extension') == item.extension and event.get('text') == item.text
            and event.get('revision') == item.revision and item.alive() and item.snapshot() == item.captured
            and item.replace(item.text))

    def discard(self, event: dict[str, Any]) -> None:
        self._pending.pop(event.get('editor_write', {}).get('id'), None)

    def prune(self) -> None:
        for key, item in tuple(self._pending.items()):
            if time.monotonic() >= item.expires or not item.alive():
                self._pending.pop(key, None)

    def clear(self) -> None:
        self._pending.clear()
