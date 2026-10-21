"""Opt-in, owner-pinned durable ACP intake; never starts an Agent or receiver.

A queued/duplicate receipt acknowledges storage, not execution. Transport failure
returns outcome-unknown unless the original ID can be verified by the receiver's
atomic same-ID/content duplicate check. No new message ID is ever manufactured.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from xdog.agent.session_input import SessionInput, input_data

_TIMEOUT = 10
_STATES = frozenset({'pending', 'claimed', 'pending_review', 'approved', 'running',
                     'succeeded', 'failed', 'cancelled', 'interrupted', 'preparation_failed'})


def validate_target(target: dict[str, Any]) -> dict[str, str]:
    """Copy data-only routing, requiring the exact trusted host-supplied binding."""
    result = {}
    for name in ('endpoint', 'session_id', 'cwd', 'inbox_id', 'branch_id'):
        value = target.get(name)
        if (not isinstance(value, str) or '\x00' in value or (name != 'branch_id' and not value.strip())
                or len(value.encode()) > (4096 if name in {'endpoint', 'cwd'} else 256)):
            raise ValueError('Invalid ACP target ' + name)
        result[name] = value
    for name in ('endpoint', 'cwd'):
        if not Path(result[name]).is_absolute():
            raise ValueError('ACP target paths must be absolute')
    if len(result['endpoint'].encode()) > 107:
        raise ValueError('ACP endpoint exceeds Unix socket capacity')
    return result


def _endpoint(path: Path) -> None:
    if path.resolve() != path:
        raise ValueError('ACP endpoint must not contain symlinks or navigation')
    directory, endpoint = path.parent.lstat(), path.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700 or not stat.S_ISSOCK(endpoint.st_mode)
            or endpoint.st_uid != os.getuid() or stat.S_IMODE(endpoint.st_mode) != 0o600):
        raise ValueError('ACP endpoint requires same-user private directory (0700) and socket (0600)')


async def _settle(task: asyncio.Task[None]) -> None:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        async with asyncio.timeout(5):
            await writer.wait_closed()
    except (OSError, TimeoutError):
        pass


class _Peer:
    async def request_permission(self, **kwargs: Any) -> Any:
        try:
            from acp.schema import DeniedOutcome, RequestPermissionResponse
        except ImportError as exc:
            raise ImportError('Durable ACP intake requires the optional agent-client-protocol SDK') from exc
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome='cancelled'))

    async def session_update(self, **kwargs: Any) -> None:
        pass  # Intake does not collect model output or grant permission.


@asynccontextmanager
async def _connection(target: dict[str, str]) -> AsyncIterator[Any]:
    # Optional SDK is loaded only on an actual caller-owned connection.
    try:
        from acp import PROTOCOL_VERSION, connect_to_agent
        from acp.schema import Implementation
    except ImportError as exc:
        raise ImportError('Durable ACP intake requires the optional agent-client-protocol SDK') from exc

    stack = AsyncExitStack()
    try:
        async with asyncio.timeout(_TIMEOUT):
            await asyncio.to_thread(_endpoint, Path(target['endpoint']))
            reader, writer = await asyncio.open_unix_connection(target['endpoint'], limit=1024 * 1024)
            stack.push_async_callback(_close_writer, writer)
            connection = connect_to_agent(_Peer(), writer, reader)  # type: ignore[arg-type]
            stack.push_async_callback(connection.close)
            response = await connection.initialize(
                protocol_version=PROTOCOL_VERSION, client_info=Implementation(name='xdog-durable-intake', version='1'))
            meta = response.field_meta or {}
            namespace = meta.get('xdog', {})
            extensions = namespace.get('extensions', {}) if isinstance(namespace, dict) else {}
            if (response.protocol_version != PROTOCOL_VERSION or not isinstance(extensions, dict)
                    or any(type(extensions.get(key)) is not int or extensions[key] != 1
                           for key in ('enqueue', 'inbox_status'))):
                raise ValueError('ACP target must negotiate enqueue/inbox_status v1')
        yield connection
    finally:
        await _settle(asyncio.create_task(stack.aclose()))


async def _status(connection: Any, target: dict[str, str], message_id: str | None = None) -> dict[str, Any]:
    params = {'session_id': target['session_id'], 'cwd': target['cwd']}
    if message_id is not None:
        params['message_id'] = message_id
    async with asyncio.timeout(_TIMEOUT):
        result = await connection.ext_method('xdog/inbox_status', params)
    if (not isinstance(result, dict) or any(result.get(key) != target[key]
            for key in ('session_id', 'cwd', 'inbox_id', 'branch_id'))
            or not isinstance(result.get('messages'), list)):
        raise ValueError('ACP inbox owner/namespace/branch/workspace mismatch')
    if message_id is not None and any(not isinstance(row, dict) or row.get('message_id') != message_id
                                     or row.get('branch_id') != target['branch_id'] for row in result['messages']):
        raise ValueError('Invalid selected ACP inbox message')
    return result


async def inbox_status(target: dict[str, Any], message_id: str | None = None) -> dict[str, Any]:
    """Read exact pinned scope without session loading, execution or mutation."""
    binding = validate_target(target)
    async with _connection(binding) as connection:
        return await _status(connection, binding, message_id)


async def _send(connection: Any, target: dict[str, str], params: dict[str, Any]) -> dict[str, Any]:
    async with asyncio.timeout(_TIMEOUT):
        result = await connection.ext_method('xdog/enqueue', params)
    if (not isinstance(result, dict) or result.get('session_id') != target['session_id']
            or result.get('branch_id') != target['branch_id'] or result.get('message_id') != params['message_id']
            or result.get('status') not in {'queued', 'duplicate'} or result.get('message_state') not in _STATES):
        raise ValueError('Invalid enqueue receipt; acceptance is unknown')
    return result


async def enqueue(target: dict[str, Any], message: SessionInput) -> dict[str, Any]:
    """Enqueue once; recover ACK loss only with original-ID/content verification.

    Returns status queued/duplicate, unavailable (nothing sent), or
    outcome-unknown (inspect original message). Cancellation joins connection
    cleanup but never withdraws accepted input. Wire origin is always ACP, never
    human authority, regardless of the supplied envelope's informational origin.
    """
    binding, data = validate_target(target), input_data(message)
    params = {**{key: binding[key] for key in ('session_id', 'cwd')}, 'message_id': data['id'],
              **{key: data[key] for key in ('source', 'instruction', 'evidence', 'provenance')}}
    if len(json.dumps(params, ensure_ascii=True, allow_nan=False).encode()) > 1024 * 1024 - 4096:
        raise ValueError('ACP enqueue exceeds transport capacity')
    attempted = False
    try:
        async with _connection(binding) as connection:
            await _status(connection, binding)
            attempted = True
            result = await _send(connection, binding, params)
            confirmed = await _status(connection, binding, message.id)
            if len(confirmed['messages']) != 1:
                raise ValueError('Enqueue receipt missing from the original pinned inbox')
            return result
    except Exception as exc:
        error = str(exc)
        if not attempted:
            return {'status': 'unavailable', 'message_id': message.id, 'error': error}
    try:
        async with _connection(binding) as connection:
            status = await _status(connection, binding, message.id)
            if len(status['messages']) == 1:
                # Status intentionally exposes no submitted body. The receiver's
                # atomic duplicate check verifies original content, not ID alone.
                result = await _send(connection, binding, params)
                confirmed = await _status(connection, binding, message.id)
                if result['status'] == 'duplicate' and len(confirmed['messages']) == 1:
                    return {**result, 'ack_recovered': True}
    except Exception as exc:
        error += '; original-ID verification: ' + str(exc)
    return {'status': 'outcome-unknown', 'message_id': message.id, 'error': error,
            'notice': 'Inspect/retry only the original ID and content; no automatic new-ID retry'}
