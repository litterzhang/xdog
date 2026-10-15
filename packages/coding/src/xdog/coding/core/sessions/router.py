"""Route exact live session identities; never load history as an attach shortcut."""

from __future__ import annotations

from pathlib import Path

from xdog.coding.core.sessions.controller import SessionController
from xdog.coding.core.sessions.ownership import OwnerRecord


class SessionRouter:
    def __init__(self) -> None:
        self._live: dict[str, tuple[SessionController, OwnerRecord]] = {}
        self._retired: set[str] = set()

    def register(self, controller: SessionController, record: OwnerRecord) -> None:
        if record.session_id != controller.session.session_id or record.generation != controller.generation:
            raise RuntimeError("Session owner identity mismatch")
        if record.session_id in self._live:
            raise RuntimeError("Session already has a live owner")
        self._live[record.session_id] = controller, record
        self._retired.discard(record.session_id)

    def resolve(self, session_id: str, cwd: Path) -> SessionController:
        if session_id not in self._live:
            raise RuntimeError("Session is inactive" if session_id in self._retired else "Session is unknown")
        controller, record = self._live[session_id]
        if cwd.resolve() != record.cwd.resolve():
            raise RuntimeError("Session workspace mismatch")
        if controller.generation != record.generation or controller.session.session_id != session_id:
            raise RuntimeError("Session owner generation is stale")
        return controller

    def retire(self, session_id: str, generation: str) -> None:
        current = self._live.get(session_id)
        if current is not None and current[1].generation == generation:
            del self._live[session_id]
            self._retired.add(session_id)
