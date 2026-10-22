"""One admitted tool operation's explicit, single-channel outbound messages.

This is application IO, not default reply routing or feature notification policy.
Recipients come from live host channel bindings, never model/event addresses.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any

from xdog.agent.cross_loop import settle_on
from xdog.agent.session_input import SessionInput
from xdog.claw.core.chunker import BlockChunker
from xdog.claw.core.inputs.models import InputEnvelope, ReplyTarget
from xdog.claw.core.inputs.store import InputRecord
from xdog.claw.core.outputs.delivery import OutputDelivery
from xdog.claw.core.outputs.store import OutputRecord, delivery_status
from xdog.claw.core.outputs.targets import ReplyReference
from xdog.claw.core.runtime.channel_adapter import ChannelReplies


class OutboundMessages:
    def __init__(self, record: InputRecord, channels: ChannelReplies, outputs: OutputDelivery,
                 valid: Callable[[], bool], *, sender: str = '', lock: asyncio.Lock | None = None) -> None:
        self.record, self._channels, self._outputs, self._valid = record, channels, outputs, valid
        self._sender = sender
        self._loop = asyncio.get_running_loop()
        self._lock = lock if lock is not None else asyncio.Lock()
        self._offered: dict[str, ReplyReference] = {}

    def for_child(self, identifier: str) -> OutboundMessages:
        return OutboundMessages(self.record, self._channels, self._outputs, self._valid,
                                sender=identifier, lock=self._lock)

    def _check(self, cancel: asyncio.Event | None = None) -> None:
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        if not self._valid():
            raise ValueError('Original message tool owner or permissions are unavailable')

    def _catalog(self) -> dict[str, ReplyReference]:
        self._check()
        return self._channels.bound(self.record.envelope.scope)

    async def channels(self) -> dict[str, Any]:
        async def read() -> dict[str, Any]:
            self._offered = self._catalog()
            return {'channels': [{'id': identifier, 'name': reference.channel}
                                 for identifier, reference in self._offered.items()],
                    'scope': 'current_group', 'selection': 'one channel per message'}
        return await settle_on(self._loop, read())

    async def send(self, call_id: str, channel: str, text: str,
                   cancel: asyncio.Event | None = None, *, reference: str | None = None) -> dict[str, Any]:
        # A child may execute on another loop; original application IO stays here.
        async def send_on_owner() -> dict[str, Any]:
            async with self._lock:
                self._check(cancel)
                if not call_id or len(call_id.encode()) > 16384 or not text.strip() or len(text.encode()) > 256 * 1024:
                    raise ValueError('Message requires bounded nonempty call identity and text')
                source = self.record.envelope
                key = [source.scope.group_id, str(source.scope.workspace), source.message.id,
                       self.record.attempt_id, self._sender, call_id]
                identifier = 'message-' + hashlib.sha256(json.dumps(key).encode()).hexdigest()
                existing = await self._outputs.store.records(source.scope, message_id=identifier)
                self._check(cancel)
                chunks = BlockChunker().chunk(text) or [text]
                if existing:
                    # A repeated tool call cannot choose a second channel or retry an unknown send.
                    if ({row.binding for row in existing} != {channel}
                            or [row.text for row in existing] != chunks
                            or existing[0].envelope.message.provenance.get('reference') != reference):
                        raise ValueError('Original message call already has another channel or content')
                    return self._result(identifier, channel, existing)
                if channel not in self._offered:
                    raise ValueError('List message.channels in this operation before choosing a destination')
                destination = self._catalog().get(channel)
                if destination is None:
                    raise ValueError('Channel is not currently bound to this group; list message.channels')
                previous = self._offered.get(channel)
                if previous is not None and previous != destination:
                    raise ValueError('Channel recipient changed after listing; refresh before sending')
                # Freeze the actual recipient/account/context before entering owned IO.
                metadata = destination.original.metadata
                envelope = InputEnvelope(source.scope,
                    SessionInput(identifier, 'automatic', 'message', 'Explicit outbound message', (),
                        {'input_id': source.message.id, 'attempt_id': self.record.attempt_id,
                         'tool_call_id': call_id, 'worker': self._sender, 'reference': reference}),
                    ReplyTarget(destination.channel, destination.recipient), context={
                        'kind': 'external', 'channel': destination.channel, 'sender': destination.recipient,
                        'timestamp': 0, 'metadata': metadata, 'outbound_tool': 'message'})
                self._check(cancel)
                await self._outputs.emit(envelope, self.record.attempt_id, channel, chunks)
                rows = await self._outputs.store.records(source.scope, message_id=identifier)
                return self._result(identifier, channel, rows)
        return await settle_on(self._loop, send_on_owner())

    @staticmethod
    def _result(identifier: str, channel: str, rows: Sequence[OutputRecord]) -> dict[str, Any]:
        return {'message_id': identifier, 'channel': channel, 'status': delivery_status(rows),
                'outputs': [{'id': row.id, 'status': row.status} for row in rows],
                'default_reply_route_changed': False}
