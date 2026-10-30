"""Bind read-only XHS IO to the existing admitted input and application loop."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.cross_loop import settle_on
from xdog.claw.channels.xhs.channel import XhsChannel
from xdog.claw.core.runtime.channel_adapter import ChannelReplies
from xdog.claw.core.types import GroupInput, UserInput


def bind_reader(channels: ChannelReplies, original: GroupInput, valid: Callable[[], bool],
                ) -> Callable[[], Awaitable[dict[str, Any]]] | None:
    if not isinstance(original, UserInput) or original.channel != 'xhs':
        return None
    channel = channels.original_channel(original)
    if not isinstance(channel, XhsChannel):
        return None
    loop = asyncio.get_running_loop()

    async def read() -> dict[str, Any]:
        async def on_owner() -> dict[str, Any]:
            if not valid() or channels.original_channel(original) is not channel:
                raise ValueError('Original XHS post reader owner or permissions changed')
            result = await channel.read_post(original)
            if not valid() or channels.original_channel(original) is not channel:
                raise ValueError('Original XHS post reader owner or permissions changed')
            return result
        return await settle_on(loop, on_owner())
    return read
