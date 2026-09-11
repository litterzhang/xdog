"""Session persistence: save/load conversation sessions to disk."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xdog.agent import AgentMessage
from xdog.coding.config import get_sessions_dir
from xdog.coding.core.defaults import SESSION_FILE_PREFIX, SESSION_FILE_SUFFIX
from xdog.coding.core.messages import dicts_to_messages, messages_to_dicts


@dataclass(frozen=True)
class SessionMeta:
    """Lightweight metadata for listing sessions without loading full history."""

    session_id: str
    created_at: float
    updated_at: float
    summary: str
    model: str
    message_count: int
    working_dir: str = ""


@dataclass
class SessionData:
    """Full session data including conversation history."""

    session_id: str
    created_at: float
    updated_at: float
    summary: str
    model: str
    messages: list[AgentMessage] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    branches: list[dict[str, Any]] = field(default_factory=list)
    working_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "summary": self.summary,
            "model": self.model,
            "working_dir": self.working_dir,
            "messages": messages_to_dicts(self.messages),
            "settings": self.settings,
            "branches": self.branches,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionData:
        return cls(
            session_id=data["session_id"],
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            summary=data.get("summary", ""),
            model=data.get("model", ""),
            messages=dicts_to_messages(data.get("messages", [])),
            settings=data.get("settings", {}),
            branches=data.get("branches", []),
            working_dir=data.get("working_dir", ""),
        )


class SessionManager:
    """Manages session persistence to the file system."""

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self._dir = sessions_dir or get_sessions_dir()

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self._dir / f"{SESSION_FILE_PREFIX}{session_id}{SESSION_FILE_SUFFIX}"

    # --- CRUD ---

    def create_session(self, *, model: str = "", summary: str = "", working_dir: Path | None = None) -> SessionData:
        """Create a new empty session and persist it."""
        self._ensure_dir()
        now = time.time()
        session = SessionData(
            session_id=uuid.uuid4().hex,
            created_at=now,
            updated_at=now,
            summary=summary,
            model=model,
            working_dir=str(working_dir.resolve()) if working_dir is not None else "",
        )
        self._write(session)
        return session

    def save_session(self, session: SessionData) -> None:
        """Write *session* to disk, updating the timestamp."""
        self._ensure_dir()
        session.updated_at = time.time()
        self._write(session)

    def load_session(self, session_id: str) -> SessionData | None:
        """Load a session by id.  Returns ``None`` if not found."""
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return SessionData.from_dict(raw)
        except (json.JSONDecodeError, KeyError):
            return None

    def delete_session(self, session_id: str) -> bool:
        """Delete a session file.  Returns ``True`` if it existed."""
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_sessions(
        self, *, limit: int | None = 50, working_dir: Path | None = None, include_unknown: bool = False,
    ) -> list[SessionMeta]:
        """List sessions ordered by most-recently-updated first."""
        self._ensure_dir()
        metas: list[SessionMeta] = []
        for path in self._dir.glob(f"{SESSION_FILE_PREFIX}*{SESSION_FILE_SUFFIX}"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                directory = raw.get("working_dir", "")
                if working_dir is not None:
                    if directory:
                        if Path(directory).resolve() != working_dir.resolve():
                            continue
                    elif not include_unknown:
                        continue
                metas.append(SessionMeta(
                    session_id=raw["session_id"],
                    created_at=raw.get("created_at", 0.0),
                    updated_at=raw.get("updated_at", 0.0),
                    summary=raw.get("summary", ""),
                    model=raw.get("model", ""),
                    message_count=len(raw.get("messages", [])),
                    working_dir=directory,
                ))
            except (json.JSONDecodeError, KeyError):
                continue
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas[:limit]

    def get_most_recent(self) -> SessionData | None:
        """Load the most recently updated session."""
        listing = self.list_sessions(limit=1)
        if not listing:
            return None
        return self.load_session(listing[0].session_id)

    # --- internal ---

    def _write(self, session: SessionData) -> None:
        path = self._session_path(session.session_id)
        path.write_text(
            json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
