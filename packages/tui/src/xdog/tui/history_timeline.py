"""Read-only provenance graph of saved history, never content-only deduplication."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from xdog.tui.child_browser import message_records
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.utils import sanitize_terminal_text


@dataclass(frozen=True)
class TimelineSource:
    id: str
    label: str
    messages: tuple[dict[str, Any], ...]
    parent: str = ''
    compaction: Mapping[str, Any] | None = None


def fingerprint(message: Mapping[str, Any]) -> str:
    # Copies can acquire transport timestamps/fallback usage on persistence;
    # actual model/protocol/content/signatures/binary identity must still match.
    value = {key: value for key, value in message.items() if key not in {'timestamp', 'usage'}}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def timeline_records(sources: tuple[TimelineSource, ...], *,
                     format_source: Callable[[TimelineSource], tuple[TranscriptRecord, ...]] | None = None,
                     canceled: Callable[[], bool] = lambda: False) -> tuple[TranscriptRecord, ...]:
    if len(sources) > 256 or len({source.id for source in sources}) != len(sources):
        raise ValueError('Timeline source capacity/identity invalid')
    if sum(len(source.messages) for source in sources) > 200000:
        raise ValueError('Timeline raw history capacity exceeded; inspect sources separately')
    if any(not isinstance(source.id, str) or not 0 < len(source.id) <= 128
           or not isinstance(source.label, str) or not isinstance(source.parent, str)
           or source.compaction is not None and not isinstance(source.compaction, Mapping) for source in sources):
        raise ValueError('Invalid timeline provenance')
    by_id = {source.id: source for source in sources}
    ids: dict[str, tuple[str, ...]] = {}
    marks: dict[str, tuple[str, ...]] = {}
    visiting: set[str] = set()
    def materialize(source: TimelineSource) -> tuple[str, ...]:
        if source.id in ids:
            return ids[source.id]
        if canceled():
            raise ValueError('Timeline read canceled')
        if source.id in visiting:
            raise ValueError('Timeline lineage cycle; no guessed merge')
        if len(source.messages) > 20000:
            raise ValueError('Timeline source exceeds message capacity')
        visiting.add(source.id)
        hashes_list = []
        for message in source.messages:
            if canceled():
                raise ValueError('Timeline read canceled')
            hashes_list.append(fingerprint(message))
        hashes = tuple(hashes_list)
        marks[source.id] = hashes
        identity = [source.id + f':m{index}' for index in range(len(hashes))]
        if source.parent and source.parent in by_id:
            parent = by_id[source.parent]
            parent_ids = materialize(parent)
            for index, (old, new) in enumerate(zip(marks[parent.id], hashes, strict=False)):
                if old != new:
                    break
                identity[index] = parent_ids[index]
        metadata = source.compaction or {}
        recovery, cut, count = (metadata.get(key) for key in
                                ('recovery_branch_id', 'first_kept_index', 'original_count'))
        if (isinstance(recovery, str) and recovery in by_id and type(cut) is int and type(count) is int
                and 0 < cut < count and count == len(by_id[recovery].messages)):
            original = by_id[recovery]
            original_ids = materialize(original)
            tail = marks[original.id][cut:]
            if hashes[1:1 + len(tail)] == tail:
                identity[1:1 + len(tail)] = original_ids[cut:]
            else:
                raise ValueError('Timeline recovery link does not match retained history')
        visiting.remove(source.id)
        ids[source.id] = tuple(identity)
        return ids[source.id]
    for source in sources:
        materialize(source)
    merged: dict[str, TranscriptRecord] = {}
    owners: dict[str, list[str]] = {}
    order: list[str] = []
    for source in sources:
        if canceled():
            raise ValueError('Timeline read canceled')
        records = format_source(source) if format_source is not None else message_records(source.messages)
        for record in records:
            # Formatters combine call/results. A divergent result keeps its own
            # record even if the originating call is shared with another branch.
            first, _, suffix = record.id.partition(':')
            index = int(first[1:]) if first.startswith('m') and first[1:].isdigit() else -1
            origin = ids[source.id][index] if 0 <= index < len(ids[source.id]) else source.id + ':' + first
            digest = hashlib.sha256((record.role + '\0' + record.title + '\0' + record.body).encode()).hexdigest()
            key = 'timeline:' + origin + ':' + suffix + ':' + digest
            if key not in merged:
                order.append(key)
                merged[key] = replace(record, id=key)
                owners[key] = []
            provenance = sanitize_terminal_text(source.label) + ' [' + source.id + ']'
            if provenance not in owners[key]:
                owners[key].append(provenance)
            if len(merged) > 20000:
                raise ValueError('Timeline unique source capacity exceeded; no partial export')
    header = TranscriptRecord('timeline:index', 'system', 'Read-only session timeline',
        f'{len(sources)} sources · {len(merged)} unique source records\n'
        'Only recorded parent prefixes and verified compaction links share identities.\n'
        + '\n'.join(sanitize_terminal_text(source.label) + ' [' + source.id + ']' for source in sources))
    return (header, *(replace(merged[key], title='[' + ' | '.join(owners[key]) + '] ' + merged[key].title)
                      for key in order))
