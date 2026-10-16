"""Inspect automatic delivery or explicitly cancel/retry under the existing owner."""

from __future__ import annotations

import asyncio
import json
import shlex
from typing import TYPE_CHECKING, Any

from xdog.coding.core.sessions.inbox_models import label, record_data
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.inbox_views import summary

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.slash_commands import CommandResult

USAGE = 'Usage: /inbox [list [OFFSET]|show ID|cancel ID|retry ID] (quote opaque IDs; no implicit all)'


def session_inbox(session: AgentSession) -> InboxStore:
    runtime = session.runtime
    return runtime.inbox.store if runtime is not None else InboxStore(
        session.session_manager._dir, session.session_manager._dir.parent / 'inbox')


async def inbox_action(session: AgentSession, action: str, ids: tuple[str, ...] = (), *,
                       cursor: int = 0) -> dict[str, Any]:
    if action == 'approve':
        raise ValueError(USAGE + '; message approval is obsolete; delivery is automatic')
    if action not in {'list', 'show', 'cancel', 'retry'} or type(cursor) is not int or not 0 <= cursor <= 4096:
        raise ValueError(USAGE)
    if any(not isinstance(key, str) or key == '--all' for key in ids) or len(set(ids)) != len(ids):
        raise ValueError(USAGE)
    if ((action == 'list' and ids) or (action in {'show', 'cancel', 'retry'} and len(ids) != 1)
            or action != 'list' and cursor):
        raise ValueError(USAGE)
    for key in ids:
        label(key)
    sid, cwd = session.session_id, session.working_dir.resolve()
    branch = session.session_data.active_branch
    store = session_inbox(session)
    if action in {'list', 'show'}:
        rows = await asyncio.to_thread(store.records, sid, cwd)
        if (sid != session.session_id or cwd != session.working_dir.resolve()
                or branch != session.session_data.active_branch):
            raise RuntimeError('Inbox inspection context changed')
        if action == 'show':
            selected = next((row for row in rows if row.message.message_id == ids[0]), None)
            if selected is None:
                raise ValueError('Unknown inbox message')
            return {'session_id': sid, 'record': record_data(selected), 'untrusted': True,
                    'current_branch': branch,
                    'blocked_reason': summary(selected,current_branch=branch)['blocked_reason'],
                    'notice': 'Read-only evidence; delivery is automatic and tool permissions are unchanged'}
        page = rows[cursor:cursor + 64]
        return {'session_id': sid, 'current_branch': branch,
                'messages': [summary(row,current_branch=branch) for row in page],
                'next_cursor': cursor + len(page) if cursor + len(page) < len(rows) else None,
                'notice': 'Durable intake is not completion; pending messages are delivered automatically'}
    runtime = session.runtime
    if runtime is None:
        raise RuntimeError('Inbox control requires an active session runtime; open/resume the session explicitly')
    if action == 'cancel':
        cancelled = await runtime.inbox.cancel(ids[0], peer_id='inbox-command')
        rows = await runtime.inbox.snapshot()
        state = next((row.status for row in rows if row.message.message_id == ids[0]), 'unknown')
        return {'session_id': sid, 'message_id': ids[0],
                'status': 'cancelled' if state == 'cancelled' else 'cancel_requested' if cancelled else 'unchanged',
                'message_state': state, 'execution_continues': state == 'running',
                'notice': 'An already running shared batch cannot be withdrawn from model context'}
    request = await runtime.inbox.retry(ids[0], peer_id='inbox-command')
    return {'session_id': sid, 'status': 'accepted', 'request_ids': [request],
            'notice': 'Retry accepted; not completion; existing tool permissions still apply'}


async def inbox_command(session: AgentSession, args: str) -> CommandResult:
    from xdog.coding.core.slash_commands import CommandResult

    try:
        parts = shlex.split(args)
        action = parts[0] if parts else 'list'
        ids, cursor = tuple(parts[1:]), 0
        if action == 'list' and ids:
            if len(ids) != 1 or not ids[0].isdigit():
                return CommandResult(output=USAGE)
            cursor, ids = int(ids[0]), ()
        result = await inbox_action(session, action, ids, cursor=cursor)
        return CommandResult(output=json.dumps(result, ensure_ascii=True, allow_nan=False))
    except (ValueError, RuntimeError, OSError) as exc:
        message = str(exc)
        output = message if message.startswith('Usage: /inbox') else 'Inbox request refused: ' + message
        return CommandResult(output=output)
