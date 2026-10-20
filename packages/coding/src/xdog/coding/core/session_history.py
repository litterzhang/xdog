"""Immutable original-owner history input to a finite disk commit, not a writer loop."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from xdog.coding.core.session_manager import SessionData, SessionManager


@dataclass(frozen=True)
class HistorySnapshot:
    payload: str

    @classmethod
    def capture(cls, data: SessionData) -> HistorySnapshot:
        return cls(json.dumps(data.to_dict(), ensure_ascii=True, allow_nan=False))

    def commit(self, manager: SessionManager, guard: Callable[[], None]) -> SessionData:
        detached = SessionData.from_dict(json.loads(self.payload))
        guard()
        manager.save_session(detached, before_replace=guard)
        return detached
