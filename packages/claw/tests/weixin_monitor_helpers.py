"""WeChat polling must not wait for the agent's message handler."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from xdog.claw.channels.weixin.api import WeixinApiClient
from xdog.claw.channels.weixin.monitor import (
    MonitorOpts,
)
from xdog.claw.channels.weixin.types import MessageType, WeixinMessage


class FakeApi(WeixinApiClient):
    def __init__(self):
        super().__init__("https://example.invalid", "")
        self.updates = asyncio.Queue()
        self.polls = asyncio.Queue()
        self.poll_cancelled = asyncio.Event()
        self.close = AsyncMock()

    async def get_updates(self, get_updates_buf="", timeout_ms=35000):
        self.polls.put_nowait((get_updates_buf, timeout_ms))
        try:
            response = await self.updates.get()
            if isinstance(response, Exception):
                raise response
            return response
        except asyncio.CancelledError:
            self.poll_cancelled.set()
            raise


def user_message(index):
    return WeixinMessage(message_id=index, from_user_id=f"user-{index}", message_type=MessageType.USER)


def options(tmp_path, api, on_message, **kwargs):
    return MonitorOpts(
        base_url="https://example.invalid",
        token="",
        account_id="test",
        state_dir=tmp_path,
        cancel_event=asyncio.Event(),
        on_message=on_message,
        api_client=api,
        **kwargs,
    )


async def wait(awaitable):
    return await asyncio.wait_for(awaitable, timeout=2)
