"""Existing checksum/chunk sender applied to immutable child-source snapshots."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from dataclasses import asdict

from xdog.claw.core.runtime.transcript_source import SourceCapture, SourceSpool
from xdog.tui.child_browser import child_records


def prepare_child_source(capture: SourceCapture, canceled: threading.Event) -> SourceSpool:
    file = tempfile.TemporaryFile()
    digest = hashlib.sha256()
    size = 0
    try:
        value = capture.load()[0]
        for record in child_records(value):
            if canceled.is_set():
                raise ValueError("Child source canceled")
            data = (json.dumps(asdict(record), ensure_ascii=False) + "\n").encode("utf-8")
            file.write(data)
            digest.update(data)
            size += len(data)
        if size > 2 * 1024 * 1024:
            raise ValueError("Child display source exceeds bounded retention")
        file.seek(0)
        return SourceSpool(file, size, digest.hexdigest())
    except BaseException:
        file.close()
        raise
