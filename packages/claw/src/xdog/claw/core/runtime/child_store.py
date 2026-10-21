"""Atomic, per-file bounded child records and isolated extension entries, scoped by host session/branch."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from xdog.agent.core import AgentMessage
from xdog.agent.extensions.state import validate_entries
from xdog.agent.input_queue import QueuedInput
from xdog.agent.messages import dicts_to_messages
from xdog.agent.supervision import MAX_TRANSCRIPT_BYTES, ChildSpec
from xdog.agent.supervision_config import ChildConfig
from xdog.agent.supervision_images import (
    MAX_CHILD_ENCODED_IMAGES,
    check_collection,
    check_retained,
    image_parts,
    images_from_dicts,
    images_to_dicts,
    text_size,
)

MAX_RECORD_BYTES = 3 * MAX_CHILD_ENCODED_IMAGES + 1100 * 1024
_TERMINAL = frozenset({"completed", "failed", "canceled", "timed-out", "interrupted"})


class ChildStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()

    @classmethod
    def scoped(cls, root: Path, session_id: str, branch: str) -> ChildStore:
        for value in (session_id, branch):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
                raise ValueError("Invalid child session/branch scope")
        return cls(root / f"{session_id}.children" / f"branch-{branch}")

    def _path(self, identifier: str, suffix: str = "") -> Path:
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{12}", identifier):
            raise ValueError("Invalid child identifier")
        if self.root.is_symlink() or self.root.parent.is_symlink():
            raise ValueError("Child record directory must not be a symbolic link")
        path = self.root / f"{identifier}{suffix}.json"
        if path.is_symlink():
            raise ValueError("Child records must not be symbolic links")
        return path

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        # Atomic replacement is not durable until its directory entry is flushed.
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _ensure_directory(self) -> None:
        missing = []
        directory = self.root
        while not directory.exists():
            missing.append(directory)
            parent = directory.parent
            if parent == directory:
                raise ValueError('Child record directory has no existing ancestor')
            directory = parent
        # Flush newly created parent entries too, not just the final record rename.
        # Existing directories/permissions are not changed by this migration.
        for directory in reversed(missing):
            directory.mkdir(mode=0o700, exist_ok=True)
            self._sync_directory(directory.parent)

    def _write(self, path: Path, value: dict[str, Any]) -> None:
        # A record file remains bounded, but a task's complete paired history
        # has no lifetime cap. Immutable linked chunks are flushed before the
        # atomic record/manifest replacement. Failed writes leave harmless
        # unreferenced chunks; the prior checkpoint stays authoritative.
        messages = value.get("messages")
        if isinstance(messages, list) and (len(messages) > 2000 or
                len(json.dumps(messages, ensure_ascii=True).encode()) > MAX_TRANSCRIPT_BYTES):
            value = self._segment_history(value, messages)
        encoded = json.dumps(value, ensure_ascii=True).encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError("Child record exceeds persistence budget")
        self._ensure_directory()
        fd, temporary = tempfile.mkstemp(prefix=".child-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(encoded)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            # If this fails, replacement may already have committed. Propagate
            # failure without rollback/replay; callers keep admission uncertain.
            self._sync_directory(self.root)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def begin(self, spec: ChildSpec) -> None:
        with self._lock:
            self._write(self._path(spec.id), {
                "id": spec.id, "task": spec.task, "access": spec.access, "status": "interrupted",
                "error": "Owner interrupted; effects may have occurred. Work is never restarted automatically.",
                "updated": time.time(), "messages": [], "checkpoint": -1, "settling": False,
                "config": spec.config().to_dict(), "task_images": images_to_dicts(spec.images),
                'initial_state': 'uncertain',
            })

    def archive_compaction(self, spec: ChildSpec, generation: int, messages: tuple[AgentMessage, ...]) -> None:
        """Durable, immutable pre-compaction export; failed/cancelled attempts are retained too."""
        from xdog.agent.history import validate_checkpoint
        from xdog.agent.messages import messages_to_dicts
        if type(generation) is not int or generation < 1:
            raise ValueError("Invalid child compaction generation")
        validate_checkpoint(messages, len(messages) - 1)
        if any(text_size((message,)) > MAX_TRANSCRIPT_BYTES for message in messages):
            raise ValueError("Invalid/oversized child archive message")
        check_collection(image_parts(messages))
        record = {"id": spec.id, "generation": generation, "source": "builtin-coding",
                  "messages": messages_to_dicts(list(messages))}
        with self._lock:
            path = self._path(spec.id, f".compaction-{generation}")
            if path.exists():
                if self._read(path) != record:
                    raise ValueError("Child compaction archive identity mismatch")
                # A prior rename may have succeeded while directory fsync
                # failed. An exact retry must establish durability before ACK.
                self._sync_directory(self.root)
                return
            self._write(path, record)

    def compaction_archives(self, identifier: str) -> list[dict[str, Any]]:
        """Read archived full paired snapshots without replaying model/tool work."""
        self._path(identifier)  # Validate scope even when there are no archives.
        with self._lock:
            records = []
            for path in self.root.glob(f"{identifier}.compaction-*.json"):
                record = self._read(path)
                generation = record.get("generation")
                if (set(record) != {"id", "generation", "source", "messages"}
                        or record.get("id") != identifier or type(generation) is not int or generation < 1
                        or path != self._path(identifier, f".compaction-{generation}")
                        or record.get("source") != "builtin-coding" or not isinstance(record.get("messages"), list)):
                    raise ValueError("Invalid child compaction archive")
                from xdog.agent.history import validate_checkpoint
                messages = tuple(dicts_to_messages(record["messages"]))
                validate_checkpoint(messages, len(messages) - 1)
                if any(text_size((message,)) > MAX_TRANSCRIPT_BYTES for message in messages):
                    raise ValueError("Invalid/oversized child archive message")
                check_collection(image_parts(messages))
                records.append(record)
            return sorted(records, key=lambda record: record["generation"])

    def save_inputs(self, identifier: str, rows: tuple[QueuedInput, ...]) -> None:
        """Write-ahead snapshot before live acceptance; recovery is explicitly uncertain."""
        from xdog.agent.supervision_queue import recovery_from_dicts, recovery_to_dicts
        with self._lock:
            encoded = recovery_to_dicts(rows)
            recovery_from_dicts(encoded)  # Bound/validate before replacing the prior durable snapshot.
            self._write(self._path(identifier, '.inputs'), {
                'version': 1, 'child_id': identifier, 'inputs': encoded})

    def save_return(self, identifier: str, row: QueuedInput) -> None:
        """Keep an exact conservative backup before the live return can remove data."""
        from xdog.agent.supervision_queue import recovery_from_dicts, recovery_to_dicts
        with self._lock:
            path = self._path(identifier, '.returns')
            saved = self._read(path) if path.exists() else {'version': 1, 'child_id': identifier, 'inputs': []}
            if (set(saved) != {'version', 'child_id', 'inputs'} or type(saved['version']) is not int
                    or saved['version'] != 1 or saved['child_id'] != identifier):
                raise ValueError('Invalid durable return backups')
            previous = recovery_from_dicts(saved['inputs'])
            if any(item.id == row.id for item in previous):
                if next(item for item in previous if item.id == row.id) != row:
                    raise ValueError('Durable return backup identity mismatch')
                return
            encoded = recovery_to_dicts((*previous, row))
            recovery_from_dicts(encoded)
            self._write(path, {'version': 1, 'child_id': identifier, 'inputs': encoded})

    def save_admission(self, record: dict[str, Any]) -> None:
        """Atomic prospective history/feedback checkpoint before queue settlement.

        Keep the older queue journal until its independent settlement completes;
        crashes between files expose explicit uncertainty, never implicit replay.
        """
        with self._lock:
            if record.get('status') != 'interrupted' or record.get('settling') is not False:
                raise ValueError('Invalid child admission checkpoint')
            base = self._path(record['id'])
            self._read(base)  # Apply link/size validation before reading the base identity.
            digest = hashlib.sha256(self._read_bytes(base)).hexdigest()
            self._write(self._path(record['id'], '.admission'), {**record, '_admission_base_sha256': digest})

    def save(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._write(self._path(record["id"]), record)
            # Terminal record is authoritative before removing the older journal.
            # A crash between these operations must not revive admitted inputs.
            journal = self._path(record['id'], '.inputs')
            if journal.exists():
                journal.unlink()
                self._sync_directory(self.root)
            admission = self._path(record['id'], '.admission')
            if admission.exists():
                admission.unlink()
                self._sync_directory(self.root)
            # Backup bytes already included in the authoritative terminal record.
            returns = self._path(record['id'], '.returns')
            if returns.exists():
                returns.unlink()
                self._sync_directory(self.root)

    def forget(self, identifier: str) -> None:
        with self._lock:
            path = self._path(identifier)
            if path.exists():
                record = self._read(path)
                record["closed"] = True
                self._write(path, record)  # Retain results on disk, but release the live catalog slot.

    def _segment_history(self, record: dict[str, Any], messages: list[Any]) -> dict[str, Any]:
        tail: str | None = None
        batch: list[Any] = []
        size = 0

        def commit() -> None:
            nonlocal tail, batch, size
            chunk = {"previous": tail, "items": batch}
            encoded = json.dumps(chunk, ensure_ascii=True).encode("utf-8")
            digest = hashlib.sha256(encoded).hexdigest()
            path = self._path(record["id"], ".history-" + digest)
            if path.exists():
                # Never trust an existing content-addressed filename alone.
                if self._read_file(path) != chunk:
                    raise ValueError("Child history chunk identity mismatch")
            else:
                self._write(path, chunk)
            tail, batch, size = digest, [], 0

        for message in messages:
            message_size = len(json.dumps(message, ensure_ascii=True).encode("utf-8"))
            if batch and size + message_size > MAX_TRANSCRIPT_BYTES:
                commit()
            batch.append(message)
            size += message_size + 2
        if batch:
            commit()
        result = {key: item for key, item in record.items() if key != "messages"}
        result["history"] = {"version": 1, "tail": tail, "count": len(messages)}
        return result

    def _read_bytes(self, path: Path) -> bytes:
        # Validate the opened inode, not a prior stat: no final-component link
        # following, FIFO blocking or unbounded reads if a file grows mid-read.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
                raise ValueError("Invalid child record file")
            with os.fdopen(fd, "rb", closefd=False) as file:
                encoded = file.read(MAX_RECORD_BYTES + 1)
            if len(encoded) > MAX_RECORD_BYTES:
                raise ValueError("Invalid child record file")
            return encoded
        finally:
            os.close(fd)

    def _read_file(self, path: Path) -> dict[str, Any]:
        value = json.loads(self._read_bytes(path).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid child record")
        return value

    def _read(self, path: Path) -> dict[str, Any]:
        value = self._read_file(path)
        if "history" not in value:
            return value
        manifest = value["history"]
        identifier = value.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{12}", identifier):
            raise ValueError("Invalid child history identity")
        if ("messages" in value or not isinstance(manifest, dict)
                or set(manifest) != {"version", "tail", "count"}
                or type(manifest["version"]) is not int or manifest["version"] != 1
                or type(manifest["count"]) is not int or manifest["count"] < 1):
            raise ValueError("Invalid child history manifest")
        chunks: list[list[Any]] = []
        tail = manifest["tail"]
        seen: set[str] = set()
        count = 0
        while tail is not None:
            if (not isinstance(tail, str) or not re.fullmatch(r"[a-f0-9]{64}", tail) or tail in seen):
                raise ValueError("Invalid child history chunk reference")
            seen.add(tail)
            chunk = self._read_file(self._path(identifier, ".history-" + tail))
            if (hashlib.sha256(json.dumps(chunk, ensure_ascii=True).encode("utf-8")).hexdigest() != tail
                    or set(chunk) != {"previous", "items"} or not isinstance(chunk["items"], list)
                    or not chunk["items"]):
                raise ValueError("Invalid child history chunk")
            chunks.append(chunk["items"])
            count += len(chunk["items"])
            if count > manifest["count"]:
                raise ValueError("Invalid child history count")
            tail = chunk["previous"]
        if count != manifest["count"]:
            raise ValueError("Incomplete child history")
        return {**{key: item for key, item in value.items() if key != "history"},
                "messages": [message for chunk in reversed(chunks) for message in chunk]}

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            if self.root.is_symlink() or self.root.parent.is_symlink():
                raise ValueError("Invalid child record directory")
            result = []
            for path in self.root.glob("????????????.json"):
                record = self._read(self._path(path.stem))
                if record.get("closed") is True:
                    continue
                admission = self._path(path.stem, '.admission')
                if record.get('status') == 'interrupted' and admission.exists():
                    checkpoint = self._read(admission)
                    if (checkpoint.get('id') != path.stem or checkpoint.get('status') != 'interrupted'
                            or checkpoint.get('settling') is not False
                            or checkpoint.get('task') != record.get('task')
                            or checkpoint.get('access') != record.get('access')):
                        raise ValueError('Invalid child admission checkpoint identity')
                    digest = checkpoint.pop('_admission_base_sha256', None)
                    if (not isinstance(digest, str) or len(digest) != 64
                            or any(char not in '0123456789abcdef' for char in digest)):
                        raise ValueError('Invalid child admission base identity')
                    # A terminal record may itself be interrupted. Its new base
                    # identity still wins if cleanup crashed before sidecar unlink.
                    if hashlib.sha256(self._read_bytes(path)).hexdigest() == digest:
                        record = checkpoint
                journal = self._path(path.stem, '.inputs')
                if record.get('status') == 'interrupted' and journal.exists():
                    from xdog.agent.supervision_queue import recovery_from_dicts
                    saved = self._read(journal)
                    if (set(saved) != {'version', 'child_id', 'inputs'} or type(saved['version']) is not int
                            or saved['version'] != 1 or saved['child_id'] != path.stem):
                        raise ValueError('Invalid child write-ahead input snapshot')
                    recovery_from_dicts(saved['inputs'])
                    record['undelivered_inputs'] = saved['inputs']
                    record['queue_uncertain'] = True
                    record['error'] = ('Owner interrupted; recovered queue snapshot may include consumed inputs. '
                                       'Inspect history/effects before explicit copy; never automatically replayed.')
                returns = self._path(path.stem, '.returns')
                if record.get('status') == 'interrupted' and returns.exists():
                    saved = self._read(returns)
                    if (set(saved) != {'version', 'child_id', 'inputs'} or type(saved['version']) is not int
                            or saved['version'] != 1 or saved['child_id'] != path.stem):
                        raise ValueError('Invalid durable return backup snapshot')
                    from xdog.agent.supervision_queue import recovery_from_dicts
                    recovery_from_dicts(saved['inputs'])
                    record['returned_backups'] = saved['inputs']
                if 'queue_uncertain' in record and type(record['queue_uncertain']) is not bool:
                    raise ValueError('Invalid child queue uncertainty marker')
                if (record.get("id") != path.stem or record.get("status") not in _TERMINAL
                        or not isinstance(record.get("task"), str)
                        or record.get("access") not in {"read-only", "inherit"}
                        or type(record.get("settling", False)) is not bool or record.get("settling") is True):
                    raise ValueError("Invalid child catalog record")
                if ('initial_state' in record and (not isinstance(record['initial_state'], str)
                        or record['initial_state'] not in {
                        'pending', 'uncertain', 'admitted', 'handled', 'discarded'})):
                    raise ValueError('Invalid initial task admission state')
                record["config"] = ChildConfig.from_record(record.get("config", {})).to_dict()
                effective = record.get("effective", {})
                if not isinstance(effective, dict) or set(effective) - {
                        "model", "thinking", "context_window", "max_prompt_tokens", "max_output_tokens",
                        "supports_tool_calls"}:
                    raise ValueError("Invalid child effective model metadata")
                if effective:
                    if not isinstance(effective.get("model"), str) or len(effective["model"].encode()) > 256:
                        raise ValueError("Invalid child effective model")
                    if (not isinstance(effective.get("thinking"), str)
                            or effective["thinking"] not in {"off", "minimal", "low", "medium", "high", "xhigh"}):
                        raise ValueError("Invalid child effective thinking")
                    for key in ("context_window", "max_prompt_tokens"):
                        if type(effective.get(key)) is not int or effective[key] < 0:
                            raise ValueError("Invalid child effective context limits")
                    maximum = effective.get("max_output_tokens")
                    if maximum is not None and (type(maximum) is not int or maximum < 1):
                        raise ValueError("Invalid child effective output limit")
                    if (effective.get("supports_tool_calls") is not None
                            and type(effective["supports_tool_calls"]) is not bool):
                        raise ValueError("Invalid child tool capability")
                for key in ("result", "error"):
                    if not isinstance(record.get(key, ""), str):
                        raise ValueError("Invalid child result")
                for key in ("calls", "tokens"):
                    if type(record.get(key, 0)) is not int or record.get(key, 0) < 0:
                        raise ValueError("Invalid child usage")
                updated = record.get("updated", 0)
                if type(updated) not in {int, float} or not math.isfinite(updated):
                    raise ValueError("Invalid child update timestamp")
                messages = record.get("messages", [])
                if (not isinstance(messages, list)
                        or len(record["task"].encode("utf-8")) > 16000):
                    raise ValueError("Invalid/oversized child transcript/task")
                feedback = record.get("input_feedback", [])
                if (not isinstance(feedback, list) or len(feedback) > 64
                        or any(not isinstance(value, str) for value in feedback)
                        or sum(len(value.encode()) for value in feedback) > 65536):
                    raise ValueError("Invalid child input-handler feedback")
                from xdog.agent.supervision_queue import recovery_from_dicts
                recovery = recovery_from_dicts(record.get('undelivered_inputs', []))
                recovery_from_dicts(record.get('returned_backups', []))
                typed = tuple(dicts_to_messages(messages))
                if any(text_size((message,)) > MAX_TRANSCRIPT_BYTES for message in typed):
                    raise ValueError("Invalid/oversized child text message")
                initial = images_from_dicts(record.get('task_images', []))
                check_collection((*image_parts(typed), *initial))
                check_retained((*image_parts(typed), *initial, *(part for row in recovery for part in row.images)))
                result.append(record)
            return result

    def entries(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            path = self._path(identifier, ".entries")
            value = self._read(path) if path.exists() else {}
            validate_entries(value)
            return value

    def save_entries(self, identifier: str, value: dict[str, Any]) -> None:
        validate_entries(value)
        with self._lock:
            self._write(self._path(identifier, ".entries"), value)
