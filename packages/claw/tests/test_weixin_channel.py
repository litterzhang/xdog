"""Tests for WeChat channel."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from xdog.claw.channels.weixin.channel import (
    WeixinChannel,
    _body_from_item_list,
)
from xdog.claw.channels.weixin.context_tokens import (
    _token_store,
    get_context_token,
)
from xdog.claw.channels.weixin.types import (
    GetUpdatesResp,
    MessageItem,
    MessageItemType,
    MessageType,
    RefMessage,
    TextItem,
    WeixinMessage,
)
from xdog.claw.core.types import UserInput


def test_body_from_item_list_with_quoted_text():
    items = (
        MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="My reply"),
            ref_msg=RefMessage(
                title="Original message",
            ),
        ),
    )
    result = _body_from_item_list(items)
    assert "引用" in result
    assert "Original message" in result
    assert "My reply" in result

@pytest.mark.asyncio
async def test_send_message_calls_api(tmp_path):
    """send_message should call the API with correct params."""
    ch = WeixinChannel(
        state_dir=tmp_path,
        account_id="test-acct",
        base_url="https://test.example.com",
        token="test-token",
    )
    # Pre-populate user_id mapping
    ch._user_id_map["weixin:user1-im-wechat"] = "user1@im.wechat"

    # Mock the shared API client's send_message method
    ch._api_client.send_message = AsyncMock()

    await ch.send_message("weixin:user1-im-wechat", "Hello!")

    ch._api_client.send_message.assert_called_once()
    call_args = ch._api_client.send_message.call_args
    req = call_args[0][0]
    assert req.msg.to_user_id == "user1@im.wechat"
    assert len(req.msg.item_list) == 1
    assert req.msg.item_list[0].text_item.text == "Hello!"


@pytest.mark.asyncio
async def test_progress_replies_keep_typing_until_inbound_handler_finishes(tmp_path):
    ch = WeixinChannel(
        state_dir=tmp_path,
        account_id="test-acct",
        base_url="https://test.example.com",
        token="test-token",
    )
    ch._start_typing = AsyncMock()
    ch._stop_typing = AsyncMock()
    ch._api_client.send_message = AsyncMock()

    async def on_message(msg):
        await ch.send_message(msg.group_id, "Checking.")
        ch._stop_typing.assert_not_awaited()
        await ch.send_message(msg.group_id, "Done.")
        ch._stop_typing.assert_not_awaited()

    ch.set_on_message(on_message)
    try:
        await ch._on_inbound(_text_msg("user@im.wechat", "hello"))

        ch._start_typing.assert_awaited_once()
        ch._stop_typing.assert_awaited_once_with("main")
        assert ch._api_client.send_message.await_count == 2
    finally:
        await ch._api_client.close()


@pytest.mark.asyncio
async def test_inbound_message_conversion(tmp_path):
    """Inbound WeixinMessage should convert to UserInput correctly."""
    ch = WeixinChannel(
        state_dir=tmp_path,
        account_id="test-acct",
        base_url="https://test.example.com",
        token="test-token",
    )

    received: list[UserInput] = []

    async def on_msg(msg: UserInput) -> None:
        received.append(msg)

    ch.set_on_message(on_msg)

    msg = WeixinMessage(
        from_user_id="user1@im.wechat",
        message_type=MessageType.USER,
        item_list=(
            MessageItem(
                type=MessageItemType.TEXT,
                text_item=TextItem(text="Hi there!"),
            ),
        ),
        context_token="ctx-123",
        create_time_ms=1700000000000,
    )

    await ch._on_inbound(msg)

    assert len(received) == 1
    inbound = received[0]
    # The bound conversation, not one derived from the sender. Deriving it gave
    # every peer its own session, memory and persona — the agent introduced
    # itself by its routing key because that key was its IDENTITY.md.
    assert inbound.group_id == "main"
    # The sender is still carried, because a reply has to find its way back.
    assert inbound.sender == "user1@im.wechat"
    assert inbound.content == "Hi there!"
    assert inbound.channel == "weixin"
    assert inbound.metadata["weixin_user_id"] == "user1@im.wechat"
    assert inbound.metadata["context_token"] == "ctx-123"

@pytest.mark.asyncio
async def test_context_token_storage(tmp_path):
    """Inbound messages should store context tokens."""
    # Clear global store for test isolation
    _token_store.clear()

    ch = WeixinChannel(
        state_dir=tmp_path,
        account_id="test-acct",
        base_url="https://test.example.com",
        token="test-token",
    )

    async def noop(msg: UserInput) -> None:
        pass

    ch.set_on_message(noop)

    msg = WeixinMessage(
        from_user_id="user1@im.wechat",
        message_type=MessageType.USER,
        item_list=(
            MessageItem(
                type=MessageItemType.TEXT,
                text_item=TextItem(text="Hello"),
            ),
        ),
        context_token="ctx-token-abc",
    )

    await ch._on_inbound(msg)

    token = get_context_token("test-acct", "user1@im.wechat")
    assert token == "ctx-token-abc"

    _token_store.clear()

@pytest.mark.asyncio
async def test_connect_disconnect(tmp_path):
    """Test connect/disconnect lifecycle."""
    ch = WeixinChannel(
        state_dir=tmp_path,
        account_id="test-acct",
        base_url="https://test.example.com",
        token="test-token",
    )

    with patch("xdog.claw.channels.weixin.channel.run_monitor", new_callable=AsyncMock):
        await ch.connect()
        assert ch._monitor_task is not None

        await ch.disconnect()
        assert ch._cancel_event.is_set()


@pytest.mark.asyncio
async def test_queued_arrivals_do_not_change_active_reply_recipient(tmp_path):
    """Polling can receive Bob's message without redirecting Alice's reply."""
    ch = WeixinChannel(
        state_dir=tmp_path, account_id="queued-peers",
        base_url="https://example.invalid", token="test-token",
    )
    updates = asyncio.Queue()
    polls = asyncio.Queue()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    finished = asyncio.Event()
    received = []

    async def get_updates(**kwargs):
        polls.put_nowait(kwargs)
        return await updates.get()

    async def on_message(msg):
        received.append(msg.sender)
        if msg.sender == "alice@im.wechat":
            first_started.set()
            await release_first.wait()
        await ch.send_message(msg.group_id, f"Reply to {msg.content}")
        if msg.sender == "bob@im.wechat":
            finished.set()

    ch._api_client.get_updates = AsyncMock(side_effect=get_updates)
    ch._api_client.send_message = AsyncMock()
    ch._api_client.close = AsyncMock()
    ch._start_typing = AsyncMock()
    ch._stop_typing = AsyncMock()
    ch.set_on_message(on_message)
    updates.put_nowait(GetUpdatesResp(msgs=(
        replace(_text_msg("alice@im.wechat", "Alice"), context_token="alice-context"),
    )))
    await ch.connect()
    try:
        await asyncio.wait_for(polls.get(), timeout=2)
        await asyncio.wait_for(first_started.wait(), timeout=2)
        await asyncio.wait_for(polls.get(), timeout=2)
        updates.put_nowait(GetUpdatesResp(msgs=(
            replace(_text_msg("bob@im.wechat", "Bob"), context_token="bob-context"),
        )))
        await asyncio.wait_for(polls.get(), timeout=2)
        assert received == ["alice@im.wechat"]
        assert ch._user_id_map["main"] == "alice@im.wechat"
        release_first.set()
        await asyncio.wait_for(finished.wait(), timeout=2)
        sent = [call.args[0].msg for call in ch._api_client.send_message.await_args_list]
        assert [(msg.to_user_id, msg.context_token, msg.item_list[0].text_item.text) for msg in sent] == [
            ("alice@im.wechat", "alice-context", "Reply to Alice"),
            ("bob@im.wechat", "bob-context", "Reply to Bob"),
        ]
    finally:
        await ch.disconnect()
    assert ch._monitor_task is None
    ch._api_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_id_map_persistence(tmp_path):
    """user_id_map should persist to disk and survive reconnect."""
    ch = WeixinChannel(
        state_dir=tmp_path,
        account_id="test-acct",
        base_url="https://test.example.com",
        token="test-token",
    )

    async def noop(msg: UserInput) -> None:
        pass

    ch.set_on_message(noop)

    msg = WeixinMessage(
        from_user_id="user1@im.wechat",
        message_type=MessageType.USER,
        item_list=(
            MessageItem(
                type=MessageItemType.TEXT,
                text_item=TextItem(text="Hello"),
            ),
        ),
    )
    await ch._on_inbound(msg)

    # Verify persisted
    from xdog.claw.channels.weixin.channel import _load_user_id_map
    loaded = _load_user_id_map(tmp_path, "test-acct")
    assert loaded["main"] == "user1@im.wechat"

    # New channel instance should load the map
    ch2 = WeixinChannel(
        state_dir=tmp_path,
        account_id="test-acct",
        base_url="https://test.example.com",
        token="test-token",
    )
    with patch("xdog.claw.channels.weixin.channel.run_monitor", new_callable=AsyncMock):
        await ch2.connect()

    assert ch2._user_id_map["main"] == "user1@im.wechat"
    await ch2.disconnect()


def _text_msg(from_user_id: str, text: str) -> WeixinMessage:
    return WeixinMessage(
        from_user_id=from_user_id,
        message_type=MessageType.USER,
        item_list=(
            MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text)),
        ),
        create_time_ms=1700000000000,
    )


@pytest.mark.asyncio
async def test_all_peers_on_a_channel_share_one_conversation(tmp_path):
    """Two people messaging the same channel land in the same group.

    This is the whole point of the redesign. A channel is a way to REACH an
    agent, not an agent of its own — so who spoke must not decide which
    session, memory and persona they reach.
    """
    ch = WeixinChannel(
        state_dir=tmp_path,
        account_id="test-acct",
        base_url="https://test.example.com",
        token="test-token",
        group_id="work",
    )
    received: list[UserInput] = []

    async def on_msg(msg: UserInput) -> None:
        received.append(msg)

    ch.set_on_message(on_msg)

    await ch._on_inbound(_text_msg("alice@im.wechat", "from alice"))
    await ch._on_inbound(_text_msg("bob@im.wechat", "from bob"))

    assert [m.group_id for m in received] == ["work", "work"]
    # ...and the sender is still distinguishable, so a reply can be addressed.
    assert [m.sender for m in received] == ["alice@im.wechat", "bob@im.wechat"]
    # The reply address follows the most recent speaker rather than accumulating
    # one entry per peer; a stale address would send bob's answer to alice.
    assert ch._user_id_map["work"] == "bob@im.wechat"


@pytest.mark.asyncio
async def test_channel_defaults_to_main_group(tmp_path):
    ch = WeixinChannel(
        state_dir=tmp_path,
        account_id="test-acct",
        base_url="https://test.example.com",
        token="test-token",
    )
    assert ch._group_id == "main"
