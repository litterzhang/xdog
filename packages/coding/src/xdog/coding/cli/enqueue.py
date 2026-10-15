"""A one-shot durable data write; never imports ACP or constructs an agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xdog.coding.config import get_inbox_dir, get_sessions_dir
from xdog.coding.core.sessions.inbox_models import InboxMessage
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.inbox_views import receipt_data


def enqueue(session_id: str, message_id: str, instruction: str) -> dict[str, Any]:
    message = InboxMessage(session_id, Path.cwd().resolve(), message_id, instruction, (), 'coding CLI', {})
    return receipt_data(InboxStore(get_sessions_dir(), get_inbox_dir()).enqueue(message))
