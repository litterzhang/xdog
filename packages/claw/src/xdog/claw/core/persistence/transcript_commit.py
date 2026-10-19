"""One original-owner history+metadata transaction; no Agent execution or replay."""
from __future__ import annotations

import copy
import datetime
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xdog.agent.messages import dicts_to_messages, messages_to_dicts
from xdog.claw.core.persistence.navigation_journal import (
    JOURNAL,
    NavigationJournal,
    NavigationRecoveryError,
    load_manifest,
    ownership,
    recover_locked,
)
from xdog.claw.core.persistence.transcript_convert import entry_text, live_transcript_snapshot, transcript_to_messages
from xdog.claw.core.types import SessionMeta

if TYPE_CHECKING:
    from xdog.claw.core.persistence.transcript_store import TranscriptStore


@dataclass(frozen=True)
class HistoryCommit:
    owner: SessionMeta
    store_root: Path
    branch: str
    messages_json: str
    cursor: int
    channels: tuple[tuple[int, str], ...]
    channel: str = ''
    external: bool = False
    usage: tuple[tuple[str, int], ...] = ()
    increment_turn: bool = False


def _wire(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in row.items() if key not in {'usage', 'timestamp'}} for row in rows]


def commit(store: TranscriptStore, batch: HistoryCommit) -> SessionMeta:
    with store._lock:
        store._require_navigation_ready()
        current = store.get_active_session(batch.owner.group_id)
        if (batch.store_root != store._sessions_dir or current is None
                or current.session_id != batch.owner.session_id
                or store.active_branch(current.session_id) != batch.branch):
            raise ValueError('History commit owner/store/context changed')
        with ownership(store._sessions_dir):
            if store._load_index() != store._index:
                raise ValueError('History commit current index changed outside its owner')
            messages = tuple(dicts_to_messages(json.loads(batch.messages_json)))
            stored = store.load_transcript(current.session_id)
            old = _wire(messages_to_dicts(transcript_to_messages(stored)))
            expected = _wire(messages_to_dicts(list(messages[:batch.cursor])))
            if batch.cursor < 0 or len(messages) <= batch.cursor or old != expected:
                raise ValueError('History commit prefix changed; no length-only overwrite or duplicate append')
            rows = live_transcript_snapshot(messages, stored)
            if batch.channel:
                for row in rows[batch.cursor:]:
                    if row.get('role') == 'user' or batch.external:
                        row['channel'] = batch.channel
                        break
            if batch.usage:
                for row in reversed(rows[batch.cursor:]):
                    if row.get('role') == 'assistant':
                        row['usage'] = dict(batch.usage)
                        break
            for offset, channel in batch.channels:
                if batch.cursor <= offset < len(rows) and channel:
                    rows[offset]['channel'] = channel
            meta = (replace(current, turn_count=current.turn_count + 1,
                            last_active=datetime.datetime.now(datetime.timezone.utc).isoformat())
                    if batch.increment_turn else current)
            catalog, index = copy.deepcopy(store._catalog), copy.deepcopy(store._index)
            index[meta.group_id] = {**index[meta.group_id], 'turn_count': meta.turn_count,
                                   'last_active': meta.last_active}
            record = {**catalog['sessions'].get(meta.session_id, {}), **index[meta.group_id],
                      'message_count': len(rows)}
            for row in reversed(rows):
                text = entry_text(row)
                if text:
                    record['summary'] = text[:160]
                    break
            catalog['sessions'][meta.session_id] = record
            path = store._path(meta.session_id)
            pending = load_manifest(store._sessions_dir)
            if pending is not None:
                if pending['phase'] != 'committed':
                    store._navigation_recovery_required = True
                    raise NavigationRecoveryError('Uncertain prior history transaction; reopen original store')
                recover_locked(store._sessions_dir)  # Known committed housekeeping only; never undo/replay execution.
            try:
                journal = NavigationJournal(store._sessions_dir, {path, store._catalog_file, store._index_file})
            except BaseException:
                store._navigation_recovery_required = (store._sessions_dir / JOURNAL).exists()
                raise
            try:
                store._atomic(path, rows, jsonl=True)
                store._atomic(store._catalog_file, catalog)
                store._atomic(store._index_file, index)
                journal.commit()
            except BaseException:
                try:
                    journal.rollback()
                except BaseException:
                    store._navigation_recovery_required = True
                    raise
                raise
            store._catalog, store._index = catalog, index
            return meta
