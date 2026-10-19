"""Original channel bindings, kept outside the durable receipt schema."""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

from xdog.claw.channels.base import Channel
from xdog.claw.core.chunker import BlockChunker
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputRecord
from xdog.claw.core.outputs.delivery import OutputDelivery, Sender
from xdog.claw.core.outputs.store import OutputRecord
from xdog.claw.core.outputs.targets import ReplyReference
from xdog.claw.core.runtime.input_adapter import restore_input
from xdog.claw.core.types import GroupInput, UserInput


@dataclass(frozen=True)
class ChannelReply:
    binding: str
    channel: Channel
    original: GroupInput
    chunker: BlockChunker = field(default_factory=BlockChunker)

    async def __call__(self, text: str) -> None:
        for chunk in self.chunker.chunk(text) or [text]:
            if isinstance(self.original, UserInput):
                await self.channel.send_reply(self.original, chunk)
            else:
                await self.channel.send_message(self.original.group_id, chunk)


def durable_reply(delivery: OutputDelivery, record: InputRecord, binding: str
                  ) -> Callable[[str], Awaitable[None]]:
    ordinal = 0
    chunker = BlockChunker()
    async def send(text: str) -> None:
        nonlocal ordinal
        ordinal = await delivery.emit(record.envelope, record.attempt_id, binding,
                                       chunker.chunk(text) or [text], ordinal=ordinal)
    return send


class ChannelReplies:
    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def register(self, channel: Channel) -> str:
        existing = next((key for key, value in self._channels.items() if value is channel), None)
        if existing is not None:
            return existing
        binding = 'channel-' + uuid.uuid4().hex
        self._channels[binding] = channel
        return binding

    def unregister(self, binding: str) -> None:
        self._channels.pop(binding, None)

    def capture(self, scope: InputScope, channel: Channel, original: UserInput) -> ReplyReference:
        if original.channel != channel.name:
            raise ValueError('Original input belongs to another reply channel')
        routing = replace(original, content='', images=(), metadata=channel.reply_context(original))
        return ReplyReference(scope, self.register(channel), routing)

    def recipient(self, reference: ReplyReference) -> Channel | None:
        channel = self._channels.get(reference.binding)
        if channel is not None:
            if channel.name != reference.channel:
                raise ValueError('Original reply channel changed')
            return channel
        available = [item for item in self._channels.values() if item.accepts_saved_reply(reference.original)]
        if len(available) > 1:
            raise ValueError('Ambiguous original reply destination')
        return available[0] if available else None

    def reply(self, channel: Channel, original: GroupInput) -> ChannelReply:
        return ChannelReply(self.register(channel), channel, original)

    def resolve(self, row: OutputRecord) -> Sender | None:
        original = restore_input(row.envelope)
        channel = self._channels.get(row.binding)
        if channel is None and isinstance(original, UserInput):
            available = [item for item in self._channels.values() if item.accepts_saved_reply(original)]
            if len(available) > 1:
                raise ValueError('Ambiguous original channel destination')
            channel = available[0] if available else None
        if channel is None:
            return None
        return self._sender(channel, row.envelope)

    @staticmethod
    def _sender(channel: Channel, envelope: InputEnvelope) -> Sender:
        original = restore_input(envelope)
        async def send(row: OutputRecord) -> None:
            if isinstance(original, UserInput):
                await channel.send_output(original, row.id, row.text)
            else:
                await channel.send_message(original.group_id, row.text)
        return send
