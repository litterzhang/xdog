"""Native read-only transcript sources, shared by Coding terminal and RPC readers."""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any

from xdog.agent import AgentMessage
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.coding.core.messages import messages_to_dicts
from xdog.coding.core.session_manager import SessionManager
from xdog.tui.child_browser import message_records
from xdog.tui.history_timeline import TimelineSource, timeline_records
from xdog.tui.transcript_browser import TranscriptRecord


def _wire_messages(messages: tuple[AgentMessage, ...]) -> list[dict[str, Any]]:
    # Canonical serialization's generic fallback is for persistence, not display:
    # never turn an unsupported binary assistant part into a printable repr.
    for message in messages:
        if isinstance(message, AssistantMessage):
            valid = all(isinstance(part, (TextContent, ThinkingContent, ToolCall)) for part in message.content)
        elif isinstance(message, (UserMessage, ToolResultMessage)):
            valid = (isinstance(message, UserMessage) and isinstance(message.content, str)
                     or all(isinstance(part, (TextContent, ImageContent)) for part in message.content))
        else:
            valid = True  # Custom records are serialized but never falsely rendered as model prose.
        if not valid:
            raise ValueError('Unsupported source content; no binary/signature repr fallback')
    return messages_to_dicts(list(messages))


def source_records(manager: SessionManager, session_id: str, branch_id: str = '', *,
                   live_messages: tuple[AgentMessage, ...] | None = None,
                   live_compaction: Any = None, live_branch: str = '',
                   partial: AgentMessage | None = None,
                   canceled: threading.Event | None = None) -> tuple[TranscriptRecord, ...]:
    """Return complete display records, never activate a session or reveal binary/signature bodies."""
    stopped = canceled.is_set if canceled is not None else lambda: False
    if stopped():
        raise ValueError('Transcript read canceled')
    if live_messages is not None and not branch_id:
        records = message_records(_wire_messages(live_messages),
                                  partial=_wire_messages((partial,))[0] if partial is not None else None)
        return tuple(replace(record, id=f'live:{session_id}:' + record.id) for record in records)
    data = manager.load_session(session_id)
    if data is None or data.session_id != session_id:
        raise ValueError('Saved session unavailable or identity changed')
    messages = _wire_messages(tuple(data.messages))
    if branch_id == '@timeline':
        if (not isinstance(data.branches, list) or len(data.branches) >= 256
                or any(not isinstance(row, dict) for row in data.branches)):
            raise ValueError('Timeline branch capacity/records invalid; inspect sources separately')
        try:
            sources = [TimelineSource(row['branch_id'], row.get('label') or row['branch_id'],
                tuple(row['messages']), row.get('parent_branch', ''), row.get('compaction')) for row in data.branches]
        except (KeyError, TypeError) as exc:
            raise ValueError('Invalid saved timeline source') from exc
        sources.append(TimelineSource('@head', 'Current context',
            tuple(_wire_messages(live_messages)) if live_messages is not None else tuple(messages),
            live_branch if live_messages is not None else data.active_branch,
            live_compaction if live_messages is not None else data.compaction))
        return timeline_records(tuple(sources), canceled=stopped)
    if branch_id:
        if not isinstance(data.branches, list) or any(not isinstance(row, dict) for row in data.branches):
            raise ValueError('Invalid saved branch records')
        branch = next((row for row in data.branches if row.get('branch_id') == branch_id), None)
        if branch is None or not isinstance(branch.get('messages'), list):
            raise ValueError('Saved branch unavailable or invalid')
        messages = branch['messages']
    if stopped():
        raise ValueError('Transcript read canceled')
    return tuple(replace(record, id=f'saved:{session_id}:{branch_id}:' + record.id)
                 for record in message_records(messages))
