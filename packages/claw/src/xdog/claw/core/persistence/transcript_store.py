"""Transcript store — pure JSONL persistence for session transcripts.

Handles session metadata (sessions.json index), transcript files
({session_id}.jsonl), and read-only historical/compaction archives. No knowledge of
AgentSession or GroupRuntime — those are lifecycle concerns handled
by GroupRuntime.
"""
from __future__ import annotations

import copy
import datetime
import json
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xdog.claw.core.types import SessionMeta


class SessionNameOwnerChanged(ValueError):
    """A read must not silently switch the requested current conversation."""


class TranscriptStore:
    """Pure JSONL persistence for session transcripts."""

    def __init__(self, sessions_dir: Path, *, owner_group: str | None = None):
        self._sessions_dir = Path(sessions_dir)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        from xdog.claw.core.persistence.navigation_journal import recover
        self._navigation_recovery_required = False
        recover(self._sessions_dir)  # Recover BEFORE reading catalogs, plans, entries, or exposing sessions.
        self._index_file = self._sessions_dir / "sessions.json"
        self._lock = threading.RLock()
        self._owner_group = owner_group
        self._index: dict[str, dict[str, Any]] = self._load_index()
        self._catalog_file = self._sessions_dir / "catalog.json"
        self._catalog = self._load_catalog()
        self._extension_entries: dict[str, Any] = {}
        for path in self._sessions_dir.glob("*.extensions.json"):
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
                    raise ValueError("Invalid extension entry sidecar")
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("Invalid extension entries")
                self._extension_entries[path.name] = value
            except (ValueError, OSError, UnicodeError):
                self._extension_entries[path.name] = {"invalid_saved_entries": None}
        self._access: dict[str, Any] = {}
        for path in [*self._sessions_dir.glob("*.plan.json"), *self._sessions_dir.glob("*.access.json")]:
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
                    raise ValueError("Invalid access sidecar")
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("Invalid access sidecar object")
                self._access[path.name] = value
            except (ValueError, OSError, UnicodeError):
                self._access[path.name] = {"invalid_saved_plan": True}

    def _require_navigation_ready(self) -> None:
        if self._navigation_recovery_required:
            from xdog.claw.core.persistence.navigation_journal import NavigationRecoveryError
            raise NavigationRecoveryError('Navigation recovery incomplete; reopen store after repairing storage')

    def get_active_skills(self, session_id: str) -> tuple[str, ...]:
        from xdog.claw.core.persistence.skill_selection import load
        with self._lock:
            return load(self._sessions_dir, session_id)

    def save_active_skills(self, session_id: str, active: tuple[str, ...]) -> None:
        from xdog.claw.core.persistence.skill_selection import save
        with self._lock:
            save(self._sessions_dir, session_id, active)

    def get_active_tools(self, session_id: str, branch_id: str = "") -> tuple[str, ...] | None:
        from xdog.agent.extensions.active_tools import names
        path = self._path(session_id, branch_id).with_suffix('.tools.json')
        if path.is_symlink():
            raise ValueError('Saved active-tool selection is not owned')
        if not path.exists():
            return None  # Older sessions/branches retain configured defaults.
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Saved active-tool selection is unavailable')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or set(value) != {'active'}:
            raise ValueError('Invalid saved active-tool selection')
        return names(value['active'])

    def save_active_tools(self, session_id: str, selected: tuple[str, ...] | None, *,
                          before_replace: Callable[[], None] | None = None) -> None:
        from xdog.agent.extensions.active_tools import names
        value = None if selected is None else list(selected)
        names(value)
        with self._lock:
            if session_id not in self._catalog['sessions']:
                raise ValueError('Unknown active-tool selection session')
            self._atomic(self._path(session_id).with_suffix('.tools.json'), {'active': value},
                         before_replace=before_replace)

    def get_model(self, session_id: str) -> str:
        self._require_navigation_ready()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
            raise ValueError("Invalid saved model session identity")
        path = self._sessions_dir / (session_id + ".model.json")
        if not path.exists():
            return ""
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
                raise ValueError("Invalid saved model selection")
            data = json.loads(path.read_text(encoding="utf-8"))
            model = data.get("model") if isinstance(data, dict) else None
            if (not isinstance(model, str) or len(model) > 256 or not model
                    or any(ord(char) < 33 or ord(char) == 127 for char in model)
                    or set(data) != {"model"}):
                raise ValueError("Invalid saved model selection")
            return model
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("Saved model is unavailable; no primary-provider fallback") from None

    def save_model(self, session_id: str, model: str,
                   admit: Callable[[], bool] = lambda: True) -> None:
        if (not isinstance(model, str) or not model or len(model) > 256
                or any(ord(char) < 33 or ord(char) == 127 for char in model)
                or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id)):
            raise ValueError("Invalid persisted model")
        path = self._sessions_dir / (session_id + ".model.json")
        with self._lock:
            if path.is_symlink() or not admit():
                raise ValueError("Model selection owner changed before persistence")
            fd, temporary = tempfile.mkstemp(prefix=".model-", dir=self._sessions_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump({"model": model}, output)
                    output.flush()
                    os.fsync(output.fileno())
                if not admit():
                    raise ValueError("Model selection owner changed before commit")
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    # Preserve public compatibility names; selections may now be native or extension aliases.
    get_extension_model = get_model
    save_extension_model = save_model

    def get_reasoning(self, session_id: str, model: str) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
            raise ValueError("Invalid reasoning session identity")
        path = self._sessions_dir / (session_id + ".thinking.json")
        if not path.exists():
            return None
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
                raise ValueError("Invalid saved reasoning selection")
            data = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.keys() != {"model", "thinking"}
                    or not isinstance(data["model"], str) or not 0 < len(data["model"]) <= 256
                    or data["thinking"] not in ("off", "minimal", "low", "medium", "high", "xhigh")):
                raise ValueError("Invalid saved reasoning selection")
            return str(data["thinking"]) if data["model"] == model else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("Saved reasoning selection could not be loaded") from None

    def save_reasoning(self, session_id: str, model: str, thinking: str,
                       admit: Callable[[], bool] = lambda: True) -> None:
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id) or not isinstance(model, str)
                or not 0 < len(model) <= 256 or thinking not in ("off", "minimal", "low", "medium", "high", "xhigh")):
            raise ValueError("Invalid reasoning selection")
        path = self._sessions_dir / (session_id + ".thinking.json")
        with self._lock:
            if path.is_symlink() or not admit():
                raise ValueError("Reasoning selection owner changed before persistence")
            fd, temporary = tempfile.mkstemp(prefix=".thinking-", dir=self._sessions_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump({"model": model, "thinking": thinking}, output)
                    output.flush()
                    os.fsync(output.fileno())
                if not admit():
                    raise ValueError("Reasoning selection owner changed before commit")
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if self._index_file.exists():
            try:
                with open(self._index_file, "r", encoding="utf-8") as f:
                    loaded: dict[str, dict[str, Any]] = json.load(f)
                    return loaded if isinstance(loaded, dict) else {}
            except (json.JSONDecodeError, OSError, TypeError):
                pass
        return {}

    def _atomic(self, path: Path, value: Any, *, jsonl: bool = False,
                before_replace: Callable[[], None] | None = None) -> None:
        self._require_navigation_ready()
        if path.is_symlink():
            raise ValueError("Persistence files must not be symbolic links")
        fd, name = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=self._sessions_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                if jsonl:
                    for row in value:
                        stream.write(json.dumps(row) + "\n")
                else:
                    json.dump(value, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            if before_replace is not None:
                before_replace()
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _path(self, session_id: str, branch_id: str = "") -> Path:
        self._require_navigation_ready()
        for value in (session_id, branch_id) if branch_id else (session_id,):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
                raise ValueError("Invalid session/branch ID")
        path = self._sessions_dir / (f"{session_id}_branch_{branch_id}.jsonl" if branch_id else f"{session_id}.jsonl")
        if path.is_symlink():
            raise ValueError("Transcript files must not be symbolic links")
        return path

    def _load_catalog(self) -> dict[str, Any]:
        result: dict[str, Any] = {"version": 1, "sessions": {}, "branches": {}, "active_branches": {}}
        try:
            loaded = json.loads(self._catalog_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and all(isinstance(loaded.get(key), dict)
                                              for key in ("sessions", "branches", "active_branches")):
                result.update(loaded)
        except (OSError, ValueError, TypeError):
            pass
        for group_id, data in self._index.items():
            if isinstance(data, dict) and isinstance(data.get("session_id"), str):
                result["sessions"].setdefault(data["session_id"], {**data, "group_id": group_id})
        # Older indexes lost archive metadata. Recover only from an explicitly
        # group-owned directory; shared stores must not guess ownership.
        if self._owner_group is not None:
            for path in self._sessions_dir.glob("*.jsonl"):
                if "_branch_" in path.stem or path.is_symlink():
                    continue
                created = datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.timezone.utc).isoformat()
                result["sessions"].setdefault(path.stem, {
                    "session_id": path.stem, "group_id": self._owner_group, "created_at": created,
                    "last_active": created, "turn_count": 0, "label": "Recovered archive",
                })
        return result

    def _save_catalog(self) -> None:
        self._atomic(self._catalog_file, self._catalog)

    def _save_index(self) -> None:
        for group_id, data in self._index.items():
            previous = self._catalog["sessions"].get(data["session_id"], {})
            self._catalog["sessions"][data["session_id"]] = {**previous, **data, "group_id": group_id}
        self._save_catalog()
        self._atomic(self._index_file, self._index)

    def list_sessions(self, group_id: str) -> list[dict[str, Any]]:
        self._require_navigation_ready()
        with self._lock:
            return sorted((dict(value) for value in self._catalog["sessions"].values()
                           if value.get("group_id") == group_id),
                          key=lambda value: str(value.get("last_active", "")), reverse=True)

    def read_session_name(self, group_id: str, expected_session: str) -> dict[str, str] | None:
        """Copy committed metadata only; None means retry lock acquisition, never an empty name."""
        from xdog.agent.session_name import session_name
        if not self._lock.acquire(blocking=False):
            return None
        try:
            self._require_navigation_ready()
            active = self.get_active_session(group_id)
            if active is None or active.session_id != expected_session:
                raise SessionNameOwnerChanged('Active session changed; refresh /sessions before reading its name')
            row = self._catalog['sessions'].get(expected_session)
            if not isinstance(row, dict) or row.get('group_id') != group_id:
                raise ValueError('Session name metadata is not owned by this group')
            name = session_name(active.label)
            if name != active.label:
                raise ValueError('Session name metadata is not canonical plain text')
            return {'session_id': active.session_id, 'session_name': name}
        finally:
            self._lock.release()

    def rename_session(self, group_id: str, session_id: str, name: str,
                       alive: Callable[[], bool] = lambda: True, *,
                       expected_name: str | None = None) -> SessionMeta:
        """Journal catalog/index metadata together; never rewrite transcript or sidecars."""
        from xdog.agent.session_name import session_name
        from xdog.claw.core.persistence.navigation_journal import JOURNAL, NavigationJournal, ownership
        selected = session_name(name)
        with self._lock:
            self._require_navigation_ready()
            active = self.get_active_session(group_id)
            row = self._catalog['sessions'].get(session_id)
            if (active is None or active.session_id != session_id or not isinstance(row, dict)
                    or row.get('group_id') != group_id):
                raise ValueError('Session name owner changed; refresh before retrying')
            if expected_name is not None and session_name(expected_name) != active.label:
                raise ValueError('Session name changed since review; inspect before retrying')
            cached = copy.deepcopy(self._catalog), copy.deepcopy(self._index)
            with ownership(self._sessions_dir):
                if not alive():
                    from asyncio import CancelledError
                    raise CancelledError
                try:
                    journal = NavigationJournal(self._sessions_dir, {self._catalog_file, self._index_file})
                except BaseException:
                    self._navigation_recovery_required = (self._sessions_dir / JOURNAL).exists()
                    raise
                try:
                    self._index[group_id] = {**self._index[group_id], 'label': selected}
                    self._save_index()
                    journal.commit()
                except BaseException:
                    try:
                        journal.rollback()
                    except BaseException:
                        self._navigation_recovery_required = True
                        raise
                    finally:
                        self._catalog, self._index = cached
                    raise
            result = self.get_active_session(group_id)
            assert result is not None
            return result

    def branch_metadata(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            for branch_id in self.list_branches(session_id):
                value = self._catalog["branches"].get(session_id, {}).get(branch_id, {})
                result.append({"branch_id": branch_id, **value})
            return result

    def active_branch(self, session_id: str) -> str:
        return str(self._catalog["active_branches"].get(session_id, ""))

    def create_session(self, group_id: str) -> SessionMeta:
        """Rotate context without weakening the group's current access scope.

        The inherited scope is durable in catalog/index before the active pointer
        can expose the new context. No access sidecar or history activation API
        is needed; failed commits recover through the shared metadata journal.
        """
        from xdog.agent.access import AccessState
        from xdog.claw.core.persistence.navigation_journal import JOURNAL, NavigationJournal, ownership
        with self._lock:
            self._require_navigation_ready()
            current = self.get_active_session(group_id)
            access = self.get_access(current.session_id) if current is not None else AccessState().to_dict()
            session_id = str(uuid.uuid4())
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            meta = SessionMeta(session_id=session_id, group_id=group_id, created_at=now,
                               last_active=now, turn_count=0, label="")
            row = {"session_id": meta.session_id, "group_id": meta.group_id,
                   "created_at": meta.created_at, "last_active": meta.last_active,
                   "turn_count": meta.turn_count, "label": meta.label, "access": access}
            catalog, index = copy.deepcopy(self._catalog), copy.deepcopy(self._index)
            catalog["sessions"][session_id] = copy.deepcopy(row)
            index[group_id] = copy.deepcopy(row)
            transcript = self._path(session_id)
            if transcript.exists():
                raise ValueError('New context transcript already exists; no history was overwritten')
            with ownership(self._sessions_dir):
                try:
                    journal = NavigationJournal(self._sessions_dir, {self._catalog_file, self._index_file, transcript})
                except BaseException:
                    self._navigation_recovery_required = (self._sessions_dir / JOURNAL).exists()
                    raise
                try:
                    self._atomic(transcript, [], jsonl=True)
                    self._atomic(self._catalog_file, catalog)
                    self._atomic(self._index_file, index)
                    journal.commit()
                except BaseException:
                    try:
                        journal.rollback()
                    except BaseException:
                        self._navigation_recovery_required = True
                        raise
                    raise
            # Publish only after all managed files and their commit marker settle.
            self._catalog, self._index = catalog, index
            return meta

    def get_active_session(self, group_id: str) -> SessionMeta | None:
        """Get the active session for a group ID."""
        self._require_navigation_ready()
        if group_id in self._index:
            data = self._index[group_id]
            return SessionMeta(
                session_id=data["session_id"],
                group_id=data["group_id"],
                created_at=data["created_at"],
                last_active=data["last_active"],
                turn_count=data.get("turn_count", 0),
                label=data.get("label", "")
            )
        return None

    def reset_session(self, group_id: str) -> SessionMeta:
        """Archive current session, start a new one."""
        return self.create_session(group_id)

    def append_turn(self, session_id: str, turn: dict[str, Any]) -> None:
        """Append a turn to the JSONL file."""
        transcript_file = self._path(session_id)
        with self._lock:
            with open(transcript_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(turn) + "\n")
            meta = self._catalog["sessions"].get(session_id)
            if isinstance(meta, dict):
                meta["message_count"] = int(meta.get("message_count", 0)) + 1
                content = turn.get("content", "")
                if isinstance(content, list):
                    content = " ".join(str(part.get("text", "")) for part in content
                                       if isinstance(part, dict) and part.get("type") == "text")
                if isinstance(content, str) and content:
                    meta["summary"] = content[:160]

    def load_transcript(self, session_id: str) -> list[dict[str, Any]]:
        """Load all turns from a session."""
        transcript_file = self._path(session_id)
        if not transcript_file.exists():
            return []

        turns = []
        with open(transcript_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    turns.append(json.loads(line))
        return turns

    def increment_turn(self, session_id: str) -> SessionMeta:
        """Bump turn count + last_active."""
        group_id = None
        for gid, data in self._index.items():
            if data["session_id"] == session_id:
                group_id = gid
                break

        if not group_id:
            raise ValueError(f"Session {session_id} not found in index")

        data = self._index[group_id]
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        meta = SessionMeta(
            session_id=data["session_id"],
            group_id=data["group_id"],
            created_at=data["created_at"],
            last_active=now,
            turn_count=data.get("turn_count", 0) + 1,
            label=data.get("label", "")
        )

        self._index[group_id] = {
            **data,
            "session_id": meta.session_id,
            "group_id": meta.group_id,
            "created_at": meta.created_at,
            "last_active": meta.last_active,
            "turn_count": meta.turn_count,
            "label": meta.label,
        }
        self._save_index()

        return meta

    def needs_daily_reset(self, group_id: str, reset_hour: int = 4) -> bool:
        """True if the session was created before today's reset hour."""
        meta = self.get_active_session(group_id)
        if meta is None or not meta.created_at:
            return False
        try:
            created = datetime.datetime.fromisoformat(self._index[group_id].get("resumed_at") or meta.created_at)
        except (ValueError, TypeError):
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        today_reset = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
        if now < today_reset:
            today_reset -= datetime.timedelta(days=1)
        return created < today_reset

    def needs_idle_reset(self, group_id: str, idle_seconds: int) -> bool:
        """True if the session has been idle longer than *idle_seconds*."""
        if idle_seconds <= 0:
            return False
        meta = self.get_active_session(group_id)
        if meta is None or not meta.last_active:
            return False
        try:
            last = datetime.datetime.fromisoformat(meta.last_active)
        except (ValueError, TypeError):
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        elapsed = (now - last).total_seconds()
        return elapsed >= idle_seconds

    def replace_transcript(self, session_id: str, turns: list[dict[str, Any]]) -> None:
        with self._lock:
            self._atomic(self._path(session_id), turns, jsonl=True)
            meta = self._catalog["sessions"].get(session_id)
            if meta is not None:
                meta["message_count"] = len(turns)
            self._save_catalog()

    # -- Current-context metadata and read-only historical archives ---------------

    def get_extension_entries(self, session_id: str, branch_id: str = "") -> Any:
        key = self._path(session_id, branch_id).with_suffix(".extensions.json").name
        return self._extension_entries.get(key, {})

    def save_extension_entries(self, session_id: str, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise ValueError("Extension entries must be an object")
        with self._lock:
            if session_id not in self._catalog["sessions"]:
                raise ValueError("Unknown extension entry session")
            path = self._path(session_id).with_suffix(".extensions.json")
            self._atomic(path, value)
            self._extension_entries[path.name] = value

    def _saved_access(self, session_id: str, branch_id: str = "") -> dict[str, Any]:
        from xdog.agent.access import AccessState
        path = self._path(session_id, branch_id)
        key = path.with_suffix(".access.json").name
        if key in self._access:
            return AccessState.from_dict(self._access[key]).to_dict()
        legacy_key = path.with_suffix(".plan.json").name
        if legacy_key in self._access:
            return AccessState.legacy(self._access[legacy_key]).to_dict()
        record = (self._catalog["branches"].get(session_id, {}).get(branch_id, {}) if branch_id else
                  self._catalog["sessions"].get(session_id, {}))
        return AccessState.saved(record).to_dict()

    def get_access(self, session_id: str) -> dict[str, Any]:
        return self._saved_access(session_id)

    def save_access(self, session_id: str, access: dict[str, Any]) -> None:
        from xdog.agent.access import AccessState
        access = AccessState.from_dict(access).to_dict()
        with self._lock:
            if session_id not in self._catalog["sessions"]:
                raise ValueError("Unknown access session")
            path = self._path(session_id).with_suffix(".access.json")
            self._atomic(path, access)
            self._access[path.name] = access

    def branch_access(self, session_id: str, branch_id: str) -> dict[str, Any]:
        return self._saved_access(session_id, branch_id)

    def _archive_checkpoint(self, session_id: str, branch_id: str, transcript: list[dict[str, Any]],
                    *, metadata: dict[str, Any] | None = None) -> None:
        with self._lock:
            from xdog.agent.access import AccessState
            access = (AccessState.saved(metadata).to_dict() if metadata is not None
                      and ("access" in metadata or "plan" in metadata) else self.get_access(session_id))
            if access is None:
                access = {"read_only": False, "revision": 0}
            from xdog.agent.extensions.active_tools import names
            selected = (names(metadata['active_tools']) if metadata is not None and 'active_tools' in metadata
                        else self.get_active_tools(session_id))
            self._atomic(self._path(session_id, branch_id).with_suffix('.tools.json'),
                         {'active': list(selected) if selected is not None else None})
            entries = (metadata or {}).get("extension_entries", self.get_extension_entries(session_id))
            entries_path = self._path(session_id, branch_id).with_suffix(".extensions.json")
            self._atomic(entries_path, entries)
            self._extension_entries[entries_path.name] = entries
            access_path = self._path(session_id, branch_id).with_suffix(".access.json")
            # Access scope must be durable before the branch becomes discoverable.
            self._atomic(access_path, access)
            self._access[access_path.name] = access
            self._atomic(self._path(session_id, branch_id), transcript, jsonl=True)
            self._catalog["branches"].setdefault(session_id, {})[branch_id] = {
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "message_count": len(transcript), "parent_branch": self.active_branch(session_id),
                **{key: value for key, value in (metadata or {}).items() if key not in {"plan", "access"}},
                **({"legacy_plan": metadata["plan"]} if metadata is not None and "plan" in metadata else {}),
                "access": access,
            }
            self._save_catalog()

    def save_compaction_checkpoint(self, session_id: str, branch_id: str, transcript: list[dict[str, Any]],
                                   *, metadata: dict[str, Any]) -> None:
        """Durably index the complete original BEFORE destructive context replacement."""
        with self._lock:
            destination = self._path(session_id, branch_id)
            if destination.exists() or branch_id in self._catalog['branches'].get(session_id, {}):
                raise ValueError('Compaction recovery checkpoint already exists; no overwrite')
            self._archive_checkpoint(session_id, branch_id, transcript, metadata=metadata)

    def load_branch(self, session_id: str, branch_id: str) -> list[dict[str, Any]] | None:
        """Load a conversation branch. Returns None if not found."""
        branch_file = self._path(session_id, branch_id)
        if not branch_file.exists():
            return None
        turns = []
        with open(branch_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    turns.append(json.loads(line))
        return turns

    def list_branches(self, session_id: str) -> list[str]:
        """List all branch IDs for a session."""
        self._path(session_id)
        prefix = f"{session_id}_branch_"
        branches = []
        for path in self._sessions_dir.glob(f"{prefix}*.jsonl"):
            branch_id = path.stem.removeprefix(prefix)
            branches.append(branch_id)
        return sorted(branches)


# Backward compatibility alias
SessionManager = TranscriptStore
