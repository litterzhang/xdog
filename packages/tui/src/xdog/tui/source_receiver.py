"""Disk-spooled full-source assembly outside the UI and socket event loops."""
from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any, BinaryIO

from xdog.tui.transcript_browser import TranscriptRecord


class SourceReceiver:
    def __init__(self, publish: Callable[[dict[str, Any]], None],
                 decode: Callable[[BinaryIO], Any] | None = None, *, max_bytes: int | None = None) -> None:
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
            raise ValueError('Invalid source receiver capacity')
        self._max_bytes = max_bytes
        self.publish = publish
        self._decode = decode
        self._lock = threading.Lock()
        self._owner: tuple[str, str, str] | None = None
        self._scope: tuple[str, str] = ('', '')
        self._file: BinaryIO | None = None
        self._index = self._received = self._size = 0
        self._checksum = ""
        self._revision = -1
        self._session_revision = ''
        self._child_scope = ('', '', '')
        self._format = 'display'
        self._last_progress = 0.0
        self._digest = hashlib.sha256()

    def _discard(self) -> None:
        if self._file is not None:
            self._file.close()
        self._file = None

    def expect(self, request_id: str, group: str, session: str, *,
               target_session_id: str = '', branch_id: str = '', session_revision: str = '',
               child_id: str = '', child_owner: str = '', child_epoch: str = '',
               format: str = 'display') -> None:
        if format not in {'display', 'canonical'} or format == 'canonical' and self._decode is None:
            raise ValueError('Canonical sources require an explicit typed decoder')
        with self._lock:
            self._discard()
            self._owner = request_id, group, session
            self._scope = target_session_id, branch_id
            self._session_revision = session_revision
            self._child_scope = child_id, child_owner, child_epoch
            self._format = format

    def close(self) -> None:
        with self._lock:
            self._discard()
            self._owner = None

    def feed(self, message: dict[str, Any]) -> None:
        with self._lock:
            owner = (message.get("request_id"), message.get("group_id"), message.get("session_id"))
            if self._owner is None or owner != self._owner:
                return
            scope = message.get('target_session_id', ''), message.get('branch_id', '')
            envelope = dict(request_id=owner[0], group_id=owner[1], session_id=owner[2])
            if self._scope != ('', ''):
                envelope.update(target_session_id=self._scope[0], branch_id=self._scope[1])
            if self._session_revision:
                envelope['session_revision'] = self._session_revision
            if self._child_scope != ('', '', ''):
                envelope.update(child_id=self._child_scope[0], child_owner=self._child_scope[1],
                                child_epoch=self._child_scope[2])
            try:
                if message.get('format', 'display') != self._format:
                    raise ValueError('Source format changed between frames')
                if self._child_scope != ('', '', '') and tuple(message.get(key, '') for key in
                        ('child_id', 'child_owner', 'child_epoch')) != self._child_scope:
                    raise ValueError('Child source owner/epoch changed between frames')
                if self._session_revision and message.get('session_revision') != self._session_revision:
                    raise ValueError('Source conversation revision changed between frames')
                if scope != self._scope:
                    raise ValueError('Saved source scope changed between frames')
                kind = message["type"]
                if kind == "source_error":
                    raise ValueError(str(message.get("error", "Source read failed")))
                if kind == "source_begin":
                    size, checksum = message.get("bytes"), message.get("sha256")
                    if (type(size) is not int or size < 0 or not isinstance(checksum, str) or len(checksum) != 64
                            or any(char not in "0123456789abcdef" for char in checksum)):
                        raise ValueError("Invalid full-source manifest")
                    if self._max_bytes is not None and size > self._max_bytes:
                        raise ValueError('Source manifest exceeds receiver capacity')
                    self._discard()
                    self._file = tempfile.TemporaryFile()
                    revision = message.get("revision")
                    if type(revision) is not int or revision < 0:
                        raise ValueError("Invalid source revision")
                    self._revision = revision
                    self._size, self._checksum = size, checksum
                    self._index = self._received = 0
                    self._digest = hashlib.sha256()
                    self.publish({"type": "source_loading", **envelope, "bytes": size})
                    return
                if type(message.get("revision")) is not int or message["revision"] != self._revision:
                    raise ValueError("Source context changed between frames")
                if self._file is None:
                    raise ValueError("Full source arrived without a manifest")
                if kind == "source_chunk":
                    data = message.get("data")
                    if type(message.get("index")) is not int or message["index"] != self._index:
                        raise ValueError("Full-source chunks arrived out of order")
                    if not isinstance(data, str) or len(data) > 87_384:
                        raise ValueError("Invalid/oversized source chunk")
                    raw = base64.b64decode(data, validate=True)
                    if len(raw) > 64 * 1024 or self._received + len(raw) > self._size:
                        raise ValueError("Source exceeds its declared size")
                    self._file.write(raw)
                    self._digest.update(raw)
                    self._received += len(raw)
                    self._index += 1
                    if time.monotonic() - self._last_progress >= 0.2:
                        self._last_progress = time.monotonic()
                        self.publish({"type": "source_progress", **envelope,
                                      "received": self._received, "bytes": self._size})
                    return
                if kind != "source_end":
                    raise ValueError("Invalid source packet")
                if (type(message.get("chunks")) is not int or message["chunks"] != self._index
                        or self._received != self._size or self._digest.hexdigest() != self._checksum):
                    raise ValueError("Incomplete source or checksum mismatch")
                self._file.seek(0)
                if self._decode is not None:
                    result = self._decode(self._file)
                    self.publish({"type": "source_ready", **envelope, "records": result})
                    self._discard()
                    self._owner = None
                    return
                records = []
                ids = set()
                for line in self._file:
                    value = json.loads(line)
                    if not isinstance(value, dict) or any(not isinstance(value.get(key), str)
                                                          for key in ("id", "role", "title", "body")):
                        raise ValueError("Invalid source record")
                    if value["role"] not in {"user", "assistant", "tool", "reasoning", "system"} or value["id"] in ids:
                        raise ValueError("Invalid/duplicate source identity")
                    ids.add(value["id"])
                    records.append(TranscriptRecord(value["id"], value["role"], value["title"], value["body"]))
                self.publish({"type": "source_ready", **envelope, "records": tuple(records)})
                self._discard()
                self._owner = None
            except (ValueError, OSError, UnicodeError, TypeError) as exc:
                self._discard()
                self._owner = None
                self.publish({"type": "source_failed", **envelope, "error": str(exc)})
