"""Policy-free draft CAS receipts; callers enforce command/UI/session authority."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any


class EditorActions:
    def __init__(
        self, snapshot: Callable[[], tuple[str, int, tuple[str, ...]]], replace: Callable[[str], bool]
    ) -> None:
        self.snapshot, self.replace = snapshot, replace
        self._receipt: tuple[str, str, tuple[str, int, tuple[str, ...]]] | None = None

    def handle(self, owner: str, request: dict[str, Any]) -> dict[str, Any]:
        if request["operation"] == "read":
            value = self.snapshot()
            if len(value[0].encode()) > 65536:
                raise ValueError("Current editor text exceeds snapshot limit")
            nonce = secrets.token_hex(32)
            self._receipt = owner, nonce, value
            return {"text": value[0], "cursor": value[1], "image_count": len(value[2]), "revision": nonce}
        receipt = self._receipt
        self._receipt = None  # Single-use, including conflict/rejection.
        if receipt is None or receipt[:2] != (owner, request["revision"]) or receipt[2] != self.snapshot():
            return {"applied": False, "reason": "Editor/attachments/owner changed; draft preserved"}
        if not self.replace(request["text"]):
            return {"applied": False, "reason": "Unsupported editor text; draft preserved"}
        return {"applied": True, "reason": "Captured draft text replaced; attachments preserved, not submitted"}

    def invalidate(self) -> None:
        self._receipt = None
