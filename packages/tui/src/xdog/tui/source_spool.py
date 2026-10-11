"""Private JSONL transcript spooling shared by terminal transports, without application policy."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import BinaryIO

from xdog.tui.transcript_browser import TranscriptRecord


@dataclass(frozen=True, slots=True)
class SourceSpool:
    file: BinaryIO
    size: int
    sha256: str


def spool_records(records: Iterable[TranscriptRecord], canceled: threading.Event, *,
                  max_bytes: int | None = None) -> SourceSpool:
    file = tempfile.TemporaryFile()
    digest = hashlib.sha256()
    size = 0
    try:
        for record in records:
            if canceled.is_set():
                raise ValueError('Source request canceled')
            data = (json.dumps(asdict(record), ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
            if max_bytes is not None and size + len(data) > max_bytes:
                raise ValueError('Transcript source exceeds transfer capacity; no truncated snapshot')
            file.write(data)
            digest.update(data)
            size += len(data)
        if canceled.is_set():
            raise ValueError('Source request canceled')
        file.seek(0)
        return SourceSpool(file, size, digest.hexdigest())
    except BaseException:
        file.close()
        raise
