"""Readonly committed status data; no Runtime/Session/Agent in the IO projection."""
from __future__ import annotations

import json
from dataclasses import dataclass

from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.types import SessionMeta


@dataclass(frozen=True)
class StatusData:
    meta: SessionMeta | None
    history_json: str
    usage: tuple[tuple[str, int], ...]


def read(store: TranscriptStore, group: str) -> StatusData:
    # Reuse the existing pure compact-view codecs, not a second status policy.
    from xdog.claw.core.runtime.group import _display_history_entry, _sum_transcript_usage
    # An absent committed pointer has no history to read. In particular, an
    # initial uncommitted context must not block capability/status handshakes.
    if store.get_active_session(group) is None:
        return StatusData(None, '[]', tuple(_sum_transcript_usage([]).items()))
    with store._lock:
        meta = store.get_active_session(group)
        rows = store.load_transcript(meta.session_id) if meta is not None else []
    history = [value for row in rows if (value := _display_history_entry(row)) is not None]
    return StatusData(meta, json.dumps(history), tuple(_sum_transcript_usage(rows).items()))
