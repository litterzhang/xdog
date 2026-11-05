"""Real application inbox owns channel acceptance, not a volatile reply worker."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from xdog.claw.channels.weixin.channel import WeixinChannel, _TypingTicketCache
from xdog.claw.channels.weixin.types import MessageItem, MessageItemType, TextItem
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group

from packages.claw.tests.weixin_monitor_helpers import FakeApi, user_message


class ChannelApi(FakeApi):
    """Only the external API is substituted; routing, storage and cursor are real."""

    def __init__(self):
        super().__init__()
        self.sent = asyncio.Queue()
        self.typing = []

    async def get_config(self, user_id, context_token=""):
        return {"ret": 0, "typing_ticket": f"ticket:{user_id}"}

    async def send_typing(self, user_id, ticket, status):
        self.typing.append((user_id, status))

    async def send_message(self, request):
        self.sent.put_nowait(request.msg)


def text(index, sender, body, *, client_id=""):
    return replace(
        user_message(index),
        from_user_id=sender,
        client_id=client_id,
        context_token=f"ctx:{sender}",
        item_list=(MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=body)),),
    )


async def application(tmp_path, model, *, defer_inputs=False, enabled_tools=()):
    orch = Orchestrator(
        ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model,
        defer_inputs=defer_inputs,
    )
    orch.register_group(Group(id="main", workspace=str(tmp_path), enabled_tools=enabled_tools))
    channel = WeixinChannel(tmp_path / "channel", "account", "https://example.invalid", "")
    await channel._api_client.close()
    api = ChannelApi()
    channel._api_client = api
    channel._typing_tickets = _TypingTicketCache(api)
    orch.add_channel(channel)
    return orch, channel, api


def sent_parts(message):
    return message.to_user_id, message.context_token, message.item_list[0].text_item.text
