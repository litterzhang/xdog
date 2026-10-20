"""Session persistence: save/load conversation sessions to disk."""

from __future__ import annotations

import json
import logging
import math
import os
import stat
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xdog.agent import AgentMessage
from xdog.agent.access import AccessState
from xdog.agent.extensions.active_tools import names as active_tool_names
from xdog.agent.extensions.compaction import saved_record
from xdog.agent.extensions.state import validate_entries
from xdog.agent.session_name import session_name
from xdog.coding.config import get_sessions_dir
from xdog.coding.core.defaults import SESSION_FILE_PREFIX, SESSION_FILE_SUFFIX
from xdog.coding.core.messages import dicts_to_messages, messages_to_dicts
from xdog.coding.core.sessions.ownership import identifier

logger = logging.getLogger(__name__)


class SessionUnavailable(ValueError):
    """Existing owner data cannot be treated as missing or an empty session."""


def _validate_snapshot(data: Any) -> None:
    if not isinstance(data, dict):
        raise ValueError('Invalid session snapshot object')
    selected = data.get('session_id')
    if not isinstance(selected, str):
        raise ValueError('Invalid session identity')
    identifier(selected)
    for key in ('created_at', 'updated_at'):
        value = data.get(key, 0.0)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Invalid session timestamp')
    for key in ('model', 'summary', 'working_dir', 'active_branch'):
        if not isinstance(data.get(key, ''), str):
            raise ValueError('Invalid session metadata')
    if not isinstance(data.get('settings', {}), dict):
        raise ValueError('Invalid session settings')
    for key in ('messages', 'branches'):
        rows = data.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Invalid session ' + key)



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
    name: str = ""


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
    active_branch: str = ""
    access: dict[str, Any] = field(default_factory=lambda: AccessState().to_dict())
    legacy_plan: dict[str, Any] | None = None
    extension_entries: dict[str, Any] = field(default_factory=dict)
    compaction: dict[str, Any] | None = None
    name: str = ""
    active_tools: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "summary": self.summary,
            "name": session_name(self.name),
            "model": self.model,
            "working_dir": self.working_dir,
            "messages": messages_to_dicts(self.messages),
            "settings": self.settings,
            "branches": self.branches,
            "active_branch": self.active_branch,
            "access": self.access,
            "legacy_plan": self.legacy_plan,
            "extension_entries": self.extension_entries,
            "compaction": self.compaction,
            "active_tools": list(self.active_tools) if self.active_tools is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionData:
        _validate_snapshot(data)
        validate_entries(data.get("extension_entries", {}))
        access = AccessState.saved(data)
        return cls(
            session_id=data["session_id"],
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            summary=data.get("summary", ""),
            name=session_name(data.get("name", "")),
            model=data.get("model", ""),
            messages=dicts_to_messages(data.get("messages", [])),
            settings=data.get("settings", {}),
            branches=data.get("branches", []),
            working_dir=data.get("working_dir", ""),
            active_branch=data.get("active_branch", ""),
            access=access.to_dict(),
            legacy_plan=data.get("legacy_plan") if data.get("legacy_plan") is not None else data.get("plan"),
            extension_entries=data.get("extension_entries", {}),
            compaction=saved_record(data.get("compaction")),
            active_tools=active_tool_names(data.get("active_tools")),
        )


class SessionManager:
    """Manages session persistence to the file system."""

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self._dir = sessions_dir or get_sessions_dir()
        self.listing_errors: tuple[str, ...] = ()
        self._write_lock = threading.RLock()

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        identifier(session_id)
        path = self._dir / f"{SESSION_FILE_PREFIX}{session_id}{SESSION_FILE_SUFFIX}"
        if path.is_symlink():
            raise ValueError("Session files must not be symbolic links")
        return path

    def input_history_path(self, session_id: str) -> Path:
        """Separate request ledger, excluded from the session_*.json listing."""
        self._session_path(session_id)  # Apply the same ID/path validation.
        return self._dir / f"session-{session_id}.inputs.json"

    # --- CRUD ---

    def create_session(self, *, model: str = "", summary: str = "", working_dir: Path | None = None,
                       session_id: str | None = None) -> SessionData:
        """Create a new empty session and persist it."""
        self._ensure_dir()
        now = time.time()
        session = SessionData(
            session_id=session_id or uuid.uuid4().hex,
            created_at=now,
            updated_at=now,
            summary=summary,
            model=model,
            working_dir=str(working_dir.resolve()) if working_dir is not None else "",
        )
        self._write(session)
        return session

    def save_session(self, session: SessionData, *, before_replace: Callable[[], None] | None = None) -> None:
        """Write *session* to disk, updating the timestamp."""
        with self._write_lock:
            self._ensure_dir()
            session.updated_at = time.time()
            if before_replace is None:
                self._write(session)
            else:
                self._write(session, before_replace=before_replace)

    def _read_snapshot(self, session_id: str) -> dict[str, Any] | None:
        path = self._session_path(session_id)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SessionUnavailable('Session snapshot unavailable: ' + session_id) from exc
        try:
            with os.fdopen(descriptor, 'r', encoding='utf-8', closefd=False) as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('Session snapshot must be a regular file')
                raw = json.load(stream)
            _validate_snapshot(raw)
            if raw['session_id'] != session_id:
                raise ValueError('Saved session identity does not match its requested filename')
            result: dict[str, Any] = raw
            return result
        except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
            raise SessionUnavailable('Session snapshot invalid/unavailable: ' + session_id) from exc
        finally:
            os.close(descriptor)

    def load_session(self, session_id: str) -> SessionData | None:
        """Only genuine absence returns None; invalid/unreadable owners fail closed."""
        raw = self._read_snapshot(session_id)
        if raw is None:
            return None
        try:
            return SessionData.from_dict(raw)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SessionUnavailable('Session snapshot invalid/unavailable: ' + session_id) from exc

    def delete_session(self, session_id: str) -> bool:
        """Delete a session file.  Returns ``True`` if it existed."""
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            self.input_history_path(session_id).unlink(missing_ok=True)
            return True
        return False

    def list_sessions(
        self, *, limit: int | None = 50, working_dir: Path | None = None, include_unknown: bool = False,
    ) -> list[SessionMeta]:
        """List sessions ordered by most-recently-updated first."""
        self._ensure_dir()
        metas: list[SessionMeta] = []
        errors: list[str] = []
        for path in self._dir.glob(f"{SESSION_FILE_PREFIX}*{SESSION_FILE_SUFFIX}"):
            selected = path.name.removeprefix(SESSION_FILE_PREFIX).removesuffix(SESSION_FILE_SUFFIX)
            try:
                raw = self._read_snapshot(selected)
                if raw is None:
                    continue
                directory = raw.get('working_dir', '')
                if working_dir is not None:
                    if directory:
                        if Path(directory).resolve() != working_dir.resolve():
                            continue
                    elif not include_unknown:
                        continue
                metas.append(SessionMeta(
                    session_id=selected, created_at=raw.get('created_at', 0.0),
                    updated_at=raw.get('updated_at', 0.0), summary=raw.get('summary', ''),
                    name=session_name(raw.get('name', '')), model=raw.get('model', ''),
                    message_count=len(raw.get('messages', [])), working_dir=directory))
            except (ValueError, TypeError, OSError, OverflowError) as exc:
                diagnostic = f'{selected!r}: {exc}'
                errors.append(diagnostic)
                logger.warning('Saved session excluded from listing: %s', diagnostic)
        self.listing_errors = tuple(errors)
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas[:limit]

    def get_most_recent(self) -> SessionData | None:
        """Load the most recently updated session."""
        listing = self.list_sessions(limit=1)
        if not listing:
            return None
        return self.load_session(listing[0].session_id)

    # --- internal ---

    def _write(self, session: SessionData, *, before_replace: Callable[[], None] | None = None) -> None:
        path = self._session_path(session.session_id)
        fd, name = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=self._dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(session.to_dict(), stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            if before_replace is not None:
                before_replace()
            os.replace(name, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)
