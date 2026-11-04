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
    from xdog.claw.core.runtime.group import _display_history_entries, _sum_transcript_usage
    # An absent committed pointer has no history to read. In particular, an
    # initial uncommitted context must not block capability/status handshakes.
    if store.get_active_session(group) is None:
        return StatusData(None, '[]', tuple(_sum_transcript_usage([]).items()))
    with store._lock:
        meta = store.get_active_session(group)
        sessions = sorted(store.list_sessions(group),
                          key=lambda row: (str(row.get('created_at', '')), str(row['session_id'])))
        history = []
        current_rows = []
        for session in sessions:
            identifier = session['session_id']
            rows = store.load_transcript(identifier)
            projected = _display_history_entries(rows, group)
            if projected:
                history.append({'role': 'system', 'type': 'session_boundary', 'session_id': identifier,
                                'content': f"Session · {session.get('created_at', '')}"})
            for entry in projected:
                entry['session_id'] = identifier
                history.append(entry)
            if meta is not None and identifier == meta.session_id:
                current_rows = rows
    # The timeline spans the group; context/token usage still belongs to the
    # active execution session. No archived Agent or selection is restored.
    return StatusData(meta, json.dumps(history), tuple(_sum_transcript_usage(current_rows).items()))
