"""Read-only typed transcript projection: binary retained, signatures/credentials withheld.

This is an export format, deliberately not a lossless session-resumption format.
No binary decoding, tool execution, resource resolution or provider call occurs.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import threading
from collections.abc import Iterable, Iterator
from typing import Any

from xdog.agent import AgentMessage
from xdog.agent.core import CustomAgentMessage
from xdog.agent.supervision import ChildCapture
from xdog.ai.types import AssistantMessage, ToolResultMessage, UserMessage
from xdog.coding.core.messages import messages_to_dicts
from xdog.coding.core.session_manager import SessionData, SessionManager
from xdog.coding.core.transcript_source import _wire_messages
from xdog.tui.source_spool import SourceSpool
from xdog.tui.utils import redact_sensitive_text

_SECRET = re.compile(r'password|passwd|secret|token|authorization|api[_-]?key|credential|signature|encrypted', re.I)


def safe_json(value: Any, *, depth: int = 0, key: str = '') -> Any:
    """Bounded structural redaction, without treating arbitrary metadata as image bytes."""
    if depth > 64:
        raise ValueError('Canonical metadata nesting exceeds capacity')
    if _SECRET.search(key):
        return '<redacted>'
    if isinstance(value, dict):
        if any(not isinstance(name, str) for name in value):
            raise ValueError('Canonical metadata keys must be strings')
        return {name: safe_json(item, depth=depth + 1, key=name) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if type(value) is float and not math.isfinite(value):
        raise ValueError('Nonfinite canonical metadata')
    if value is None or type(value) in (bool, int, float):
        return value
    raise ValueError('Unsupported canonical metadata; no repr fallback')


def project(messages: tuple[AgentMessage, ...]) -> list[dict[str, Any]]:
    _wire_messages(messages)  # Reject unsupported typed parts before serializer repr fallbacks.
    for message in messages:
        if not isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage, CustomAgentMessage)):
            raise ValueError('Unsupported canonical message')
        if isinstance(message, CustomAgentMessage):
            safe_json(message.data)  # Persistence helpers must not silently drop malformed metadata.
        if isinstance(message, ToolResultMessage) and message.details is not None:
            safe_json(message.details)
    rows = messages_to_dicts(list(messages))
    for row in rows:
        # Never expose signature-bearing blocks via a nested persistence dictionary.
        content = row.pop('content', None)
        usage = row.pop('usage', None)
        projected = safe_json(row)
        if usage is not None:
            # Typed numeric usage is not a credential merely because a field is total_tokens.
            json.dumps(usage, allow_nan=False)
            projected['usage'] = usage
        if content is not None:
            parts: list[dict[str, Any]] = []
            for part in content:
                if part['type'] == 'image':
                    # The only unredacted payload is an actual typed image part.
                    data, media = part['data'], part['mime_type']
                    if not isinstance(data, str) or not isinstance(media, str):
                        raise ValueError('Invalid canonical image payload')
                    parts.append({'type': 'image', 'data': data, 'mime_type': media})
                elif part['type'] == 'thinking' and part.get('redacted') is True:
                    parts.append({'type': 'thinking', 'thinking': '<redacted>', 'redacted': True})
                else:
                    parts.append(safe_json({name: item for name, item in part.items()
                                            if not _SECRET.search(name)}))
            projected['content'] = parts
        # Also omit provider response IDs: these can be opaque continuity handles.
        projected.pop('response_id', None)
        row.clear()
        row.update(projected)
    return rows


def canonical_rows(manager: SessionManager, target: str, branch: str, *,
                   messages: tuple[AgentMessage, ...] | None, partial: AgentMessage | None,
                   active_branch: str, compaction: Any,
                   canceled: threading.Event) -> Iterator[dict[str, Any]]:
    if canceled.is_set():
        raise ValueError('Canonical source canceled')
    if messages is not None and not branch:
        sources = [('@live', '', '', messages, compaction, partial)]
    else:
        raw = json.loads(manager._session_path(target).read_text(encoding='utf-8'))
        if not isinstance(raw, dict):
            raise ValueError('Canonical saved session unavailable')
        _branch_messages({'messages': raw.get('messages')})
        saved = SessionData.from_dict(raw)
        if saved.session_id != target:
            raise ValueError('Canonical saved session unavailable')
        branches = saved.branches
        if (not isinstance(branches, list) or len(branches) >= 256
                or any(not isinstance(row, dict) for row in branches)):
            raise ValueError('Invalid canonical branch catalog')
        if branch == '@timeline':
            # Preserve each source with recorded provenance; never dedup by text.
            sources = []
            identities = set()
            for row in branches:
                identity = row.get('branch_id')
                if (not isinstance(identity, str) or not identity or identity in identities
                        or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', identity) is None):
                    raise ValueError('Invalid/duplicate canonical branch identity')
                identities.add(identity)
                sources.append((identity, row.get('label', ''), row.get('parent_branch', ''),
                                _branch_messages(row), row.get('compaction'), None))
            sources.append(('@head', 'Current context', active_branch if messages is not None else saved.active_branch,
                            messages if messages is not None else tuple(saved.messages),
                            compaction if messages is not None else saved.compaction, None))
        elif branch:
            selected = next((row for row in branches if row.get('branch_id') == branch), None)
            if selected is None:
                raise ValueError('Canonical branch unavailable')
            sources = [(branch, selected.get('label', ''), selected.get('parent_branch', ''),
                        _branch_messages(selected), selected.get('compaction'), None)]
        else:
            sources = [('@head', 'Current context', saved.active_branch, tuple(saved.messages), saved.compaction, None)]
    if sum(len(source[3]) for source in sources) > 200000:
        raise ValueError('Canonical aggregate message capacity exceeded')
    yield {'kind': 'manifest', 'format': 'xdog.canonical-transcript.v1', 'target_session_id': target,
           'source_count': len(sources), 'binary': 'typed-image-base64',
           'redaction': 'credentials-signatures-controls', 'replayable': False}
    for identity, label, parent, history, metadata, stream in sources:
        if canceled.is_set() or len(history) > 20000:
            raise ValueError('Canonical source canceled/exceeds message capacity')
        yield {'kind': 'source', 'id': identity, 'label': safe_json(label), 'parent': safe_json(parent),
               'compaction': safe_json(metadata), 'messages': len(history), 'partial': stream is not None}
        for index, message in enumerate(history):
            if canceled.is_set():
                raise ValueError('Canonical source canceled')
            yield {'kind': 'message', 'source': identity, 'index': index, 'message': project((message,))[0]}
        if stream is not None:
            yield {'kind': 'partial', 'source': identity, 'message': project((stream,))[0]}


def _branch_messages(row: dict[str, Any]) -> tuple[AgentMessage, ...]:
    from xdog.coding.core.messages import dicts_to_messages
    raw = row.get('messages')
    if not isinstance(raw, list) or any(not isinstance(message, dict) for message in raw):
        raise ValueError('Invalid canonical branch messages')
    # Validate roles/part kinds before permissive persistence readers drop unknown parts.
    for message in raw:
        role = message.get('role')
        if role not in ('user', 'assistant', 'toolResult', 'custom'):
            raise ValueError('Unsupported persisted canonical message role')
        if role == 'custom':
            continue
        content = message.get('content', [])
        allowed = {'text', 'thinking', 'toolCall'} if role == 'assistant' else {'text', 'image'}
        if isinstance(content, str) and role == 'user':
            continue
        if not isinstance(content, list) or any(not isinstance(part, dict) or part.get('type') not in allowed
                                               for part in content):
            raise ValueError('Unsupported persisted canonical content')
    return tuple(dicts_to_messages(raw))


def spool_canonical(manager: SessionManager, target: str, branch: str, *,
                    messages: tuple[AgentMessage, ...] | None, partial: AgentMessage | None,
                    active_branch: str, compaction: Any, canceled: threading.Event,
                    max_bytes: int) -> SourceSpool:
    rows = canonical_rows(manager, target, branch, messages=messages, partial=partial,
                          active_branch=active_branch, compaction=compaction, canceled=canceled)
    return _spool(rows, canceled, max_bytes)


def spool_child_canonical(child: ChildCapture, target: str, canceled: threading.Event, *,
                          max_bytes: int) -> SourceSpool:
    def rows() -> Iterator[dict[str, Any]]:
        if len(child.messages) > 20000:
            raise ValueError('Canonical child message capacity exceeded')
        yield {'kind': 'manifest', 'format': 'xdog.canonical-transcript.v1', 'target_session_id': target,
               'source_count': 1, 'binary': 'typed-image-base64',
               'redaction': 'credentials-signatures-controls', 'replayable': False}
        yield {'kind': 'source', 'id': child.id, 'label': safe_json(child.config.label), 'parent': target,
               'messages': len(child.messages), 'partial': child.partial is not None,
               'child': safe_json({'task': child.task, 'access': child.access, 'status': child.status,
                                  'error': child.error, 'revision': child.revision, 'settling': child.settling,
                                  'config': child.config.to_dict(), 'effective': dict(child.effective)}),
               'calls': child.calls, 'tokens': child.tokens}
        for index, message in enumerate(child.messages):
            if canceled.is_set():
                raise ValueError('Canonical child source canceled')
            yield {'kind': 'message', 'source': child.id, 'index': index, 'message': project((message,))[0]}
        if child.partial is not None:
            yield {'kind': 'partial', 'source': child.id, 'message': project((child.partial,))[0]}
    return _spool(rows(), canceled, max_bytes)


def _spool(rows: Iterable[dict[str, Any]], canceled: threading.Event, max_bytes: int) -> SourceSpool:
    file = tempfile.TemporaryFile()
    digest, size = hashlib.sha256(), 0
    try:
        for row in rows:
            data = (json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode()
            if size + len(data) > max_bytes or canceled.is_set():
                raise ValueError('Canonical source capacity/cancellation; no partial snapshot')
            file.write(data)
            digest.update(data)
            size += len(data)
        if canceled.is_set():
            raise ValueError('Canonical source canceled before manifest')
        file.seek(0)
        return SourceSpool(file, size, digest.hexdigest())
    except BaseException:
        file.close()
        raise
