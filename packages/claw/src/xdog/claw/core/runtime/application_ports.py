"""Schema-neutral application IO ports for installed application capabilities.

No feature types or schemas belong here. Snapshots are detached JSON; opaque
reply strings are interpreted only by this application, never by a capability.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from xdog.agent.leased_sqlite import LeasedSqlite
from xdog.agent.session_input import decode_input, input_data
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputRecord
from xdog.claw.core.outputs.routes import saved_route
from xdog.claw.core.outputs.store import OutputRecord
from xdog.claw.core.outputs.targets import ReplyReference, decode_reply, encode_reply

if TYPE_CHECKING:
    from xdog.claw.core.runtime.orchestrator import Orchestrator

Snapshot = dict[str, Any]


def envelope_snapshot(value: InputEnvelope) -> Snapshot:
    route = saved_route(value)
    return {'group_id': value.scope.group_id, 'workspace': str(value.scope.workspace),
            'message': input_data(value.message),
            'reply': encode_reply(route.reference) if route is not None and route.reference is not None else None}


def input_snapshot(value: InputRecord) -> Snapshot:
    return {'envelope': envelope_snapshot(value.envelope), 'status': value.status, 'attempt_id': value.attempt_id,
            'response': value.response, 'error': value.error, 'session_id': value.session_id}


def output_snapshot(value: OutputRecord) -> Snapshot:
    return {'id': value.id, 'envelope': envelope_snapshot(value.envelope), 'attempt_id': value.attempt_id,
            'ordinal': value.ordinal, 'status': value.status, 'error': value.error}


class ApplicationPorts:
    """Bridge on the actual application owner; it never owns a second consumer."""

    def __init__(self, application: Orchestrator, endpoint: Path | None) -> None:
        self._application = application
        self.data_dir = application.data_dir.absolute()
        self.endpoint = endpoint.absolute() if endpoint is not None else None

    def workspace(self, owner: str) -> Path | None:
        return self._application.get_group_workspace(owner)

    def _scope(self, owner: str, workspace: Path) -> InputScope:
        if (not isinstance(owner, str) or not isinstance(workspace, Path)
                or self.workspace(owner) != workspace):
            raise ValueError('Unknown current application owner/workspace scope')
        return InputScope(owner, workspace)

    def validate_message(self, value: object) -> Snapshot:
        return input_data(decode_input(value, transport_envelope=False))

    def validate_reply(self, owner: str, workspace: Path, value: str) -> str:
        reference = decode_reply(value)
        if reference.scope != InputScope(owner, workspace):
            raise ValueError('Reply reference does not match its original application scope')
        return encode_reply(reference)

    def tool_scope(self, context: dict[str, Any]) -> Snapshot:
        owner = context.get('group_id')
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError('Application tool requires an actual current owner')
        workspace = self.workspace(owner)
        if workspace is None:
            raise ValueError('Unknown current application owner')
        return {'group_id': owner, 'workspace': str(workspace)}

    def capture_reply(self, context: dict[str, Any]) -> str | None:
        owner = context.get('group_id')
        if not isinstance(owner, str) or (workspace := self.workspace(owner)) is None:
            raise ValueError('Unknown current application owner')
        reference = context.get('_reply_reference')
        if reference is not None and not isinstance(reference, ReplyReference):
            raise ValueError('Expected a host-created reply reference')
        return self.validate_reply(owner, workspace, encode_reply(reference)) if reference is not None else None

    async def open_database(self, path: Path, *, format: str, schema: str,
                            validate: Callable[[sqlite3.Connection], None],
                            recover: Callable[[sqlite3.Connection], None]) -> LeasedSqlite:
        # Lexical confinement deliberately does not resolve symlinks. The existing
        # lease IO rejects followed components/files; this is not an OS sandbox.
        path = path.absolute()
        if ('..' in path.parts or '\x00' in str(path) or path == self.data_dir
                or not path.is_relative_to(self.data_dir)):
            raise ValueError('Capability journal must stay within application data_dir')
        return await LeasedSqlite.open(path, format=format, schema=schema, validate=validate, recover=recover)

    async def input_namespace(self, owner: str, workspace: Path) -> str:
        self._scope(owner, workspace)
        store = (await self._application.input_service()).store
        return await store.namespace(self._scope(owner, workspace))

    async def input_get(self, owner: str, workspace: Path, identifier: str) -> Snapshot | None:
        self._scope(owner, workspace)
        store = (await self._application.input_service()).store
        value = await store.get(self._scope(owner, workspace), identifier)
        return input_snapshot(value) if value is not None else None

    async def input_attempts(self, owner: str, workspace: Path, identifier: str) -> tuple[Snapshot, ...]:
        self._scope(owner, workspace)
        store = (await self._application.input_service()).store
        values = await store.attempts(self._scope(owner, workspace), identifier)
        return tuple(json.loads(json.dumps(asdict(value), allow_nan=False)) for value in values)

    async def output_records(self, owner: str, workspace: Path) -> tuple[Snapshot, ...]:
        self._scope(owner, workspace)
        store = (await self._application.output_service()).store
        values = await store.records(self._scope(owner, workspace))
        return tuple(output_snapshot(value) for value in values)

    async def cancel_outputs(self, owner: str, workspace: Path, identifiers: tuple[str, ...]) -> None:
        self._scope(owner, workspace)
        service = await self._application.output_service()
        await service.cancel_pending(self._scope(owner, workspace), identifiers)

    async def subscribe_inputs(self, callback: Callable[[], None]) -> Callable[[], None]:
        return self._application.subscribe_inputs(lambda _scope: callback())

    async def subscribe_outputs(self, callback: Callable[[], None]) -> Callable[[], None]:
        return (await self._application.output_service()).subscribe(lambda _scope: callback())

    def register_output_source(self, name: str, resolve: Callable[[Snapshot], Awaitable[str | None]],
                               policy: Callable[[Snapshot], Awaitable[str]]) -> Callable[[], None]:
        async def resolve_reply(envelope: InputEnvelope) -> ReplyReference | None:
            self._scope(envelope.scope.group_id, envelope.scope.workspace)
            value = await resolve(envelope_snapshot(envelope))
            if value is None:
                return None
            return decode_reply(self.validate_reply(envelope.scope.group_id, envelope.scope.workspace, value))

        async def read_policy(output: OutputRecord) -> Literal['ready', 'pending', 'cancelled']:
            self._scope(output.envelope.scope.group_id, output.envelope.scope.workspace)
            value = await policy(output_snapshot(output))
            if value not in {'ready', 'pending', 'cancelled'}:
                raise ValueError('Invalid application capability output policy')
            return cast(Literal['ready', 'pending', 'cancelled'], value)

        return self._application.register_output_source(name, resolve_reply, read_policy)
