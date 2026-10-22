"""Proactive targets are explicit host captures, never broadcast or replacement peers."""
from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from xdog.claw.channels.base import Channel
from xdog.claw.channels.tui.channel import NativeNoticeChannel, TuiChannel
from xdog.claw.channels.weixin.channel import WeixinChannel
from xdog.claw.channels.weixin.context_tokens import clear_context_tokens, set_context_token
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.outputs.delivery import DeliveryNotSent, OutputDelivery
from xdog.claw.core.outputs.store import OutputStore
from xdog.claw.core.outputs.targets import decode_reply, encode_reply
from xdog.claw.core.runtime.channel_adapter import ChannelReplies
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.types import UserInput


def test_default_never_infers_target_from_broadcast_or_stdout(capsys):
    channel = TuiChannel()
    assert channel.accepts_group("main")
    assert Channel.bound_reply(channel, "main") is None
    assert channel.bound_reply("main") is None
    assert capsys.readouterr().out == ""


@pytest.fixture
def weixin(tmp_path):
    account = "bound-target-account"
    clear_context_tokens(account)
    channel = WeixinChannel(tmp_path, account, "https://example.invalid", "auth-secret", group_id="work")
    channel._api_client.send_message = AsyncMock()
    yield channel
    clear_context_tokens(account)


def test_weixin_only_captures_configured_group_with_actual_cached_peer(weixin):
    assert weixin.bound_reply("work") is None
    weixin._user_id_map.update({"work": "alice", "other": "bob"})
    assert weixin.accepts_group("other")  # Legacy broadcast eligibility is not a proactive grant.
    assert weixin.bound_reply("other") is None
    assert weixin.bound_reply("alice") is None
    original = weixin.bound_reply("work")
    assert original is not None
    assert (original.group_id, original.channel, original.sender) == ("work", "weixin", "alice")
    assert original.metadata == {"account_id": weixin._account_id, "weixin_user_id": "alice", "context_token": ""}
    assert original.content == "" and original.images == ()
    weixin._user_id_map["work"] = ""
    assert weixin.bound_reply("work") is None


async def test_weixin_capture_freezes_account_peer_context_without_logging_token(weixin, tmp_path, caplog):
    registry = ChannelReplies()
    weixin._user_id_map["work"] = "alice"
    with caplog.at_level(logging.DEBUG):
        set_context_token(weixin._account_id, "alice", "private-original-context")
        target = weixin.bound_reply("work")
        assert target is not None
        reference = registry.capture(InputScope("work", tmp_path), weixin, target)
        set_context_token(weixin._account_id, "alice", "private-new-context")
        weixin._user_id_map["work"] = "bob"
        target.metadata.clear()
        restored = decode_reply(encode_reply(reference))
        await weixin.send_output(restored.original, "stable-output-id", "hello")
    sent = weixin._api_client.send_message.await_args.args[0].msg
    assert (sent.to_user_id, sent.context_token, sent.client_id) == (
        "alice", "private-original-context", "stable-output-id",
    )
    assert "private-original-context" not in caplog.text
    assert "private-new-context" not in caplog.text and "auth-secret" not in caplog.text
    assert "private-original-context" not in repr(reference)
    assert reference.original.metadata["account_id"] == weixin._account_id


@pytest.mark.parametrize("change", [
    {"group_id": "other"},
    {"sender": "bob"},
    {"metadata": {"account_id": "other-account", "weixin_user_id": "alice", "context_token": "ctx"}},
])
async def test_weixin_capture_does_not_relax_original_reply_validation(weixin, change):
    weixin._user_id_map["work"] = "alice"
    original = weixin.bound_reply("work")
    assert original is not None
    with pytest.raises(ValueError):
        await weixin.send_output(replace(original, **change), "output", "no")
    weixin._api_client.send_message.assert_not_awaited()


async def test_native_capture_requires_open_matching_binding_and_remains_nonbroadcast():
    publish = AsyncMock()
    channel = NativeNoticeChannel(publish)
    assert channel.bound_reply("main") is None
    channel.bind_group("main")
    original = channel.bound_reply("main")
    assert original is not None and original.channel == "tui"
    assert original.content == "" and original.images == ()
    assert channel.bound_reply("other") is None
    assert not channel.accepts_group("main")
    assert not channel.accepts_saved_reply(original)
    with pytest.raises(ValueError, match="explicit reply"):
        await channel.send_message("main", "not broadcast")
    channel.bind_group("main")  # Repeated pings/binds do not invalidate the current target.
    await channel.send_output(original, "stable-output", "hello")
    publish.assert_awaited_once_with({
        "type": "notification", "group_id": "main", "notification_id": "stable-output", "content": "hello",
    })
    await channel.disconnect()
    assert channel.bound_reply("main") is None
    channel.bind_group("main")
    await channel.connect()
    assert channel.bound_reply("main") is None
    with pytest.raises(DeliveryNotSent, match="closed"):
        await channel.send_output(original, "late-output", "no")
    assert publish.await_count == 1


@pytest.mark.parametrize("switch_back", [False, True])
async def test_native_saved_capture_cannot_follow_group_switch_even_back_to_original(tmp_path, switch_back):
    publish = AsyncMock()
    channel = NativeNoticeChannel(publish)
    registry = ChannelReplies()
    channel.bind_group("main")
    target = channel.bound_reply("main")
    assert target is not None
    reference = registry.capture(InputScope("main", tmp_path), channel, target)
    original = decode_reply(encode_reply(reference)).original
    assert original.metadata == target.metadata  # Host filtering must retain the proactive fence.
    channel.bind_group("other")
    if switch_back:
        channel.bind_group("main")
    with pytest.raises(DeliveryNotSent, match="binding"):
        await channel.send_output(original, "old-output", "no")
    publish.assert_not_awaited()
    current = channel.bound_reply("main" if switch_back else "other")
    assert current is not None
    await channel.send_output(current, "new-output", "yes")
    assert publish.await_count == 1


async def test_native_old_capture_cannot_move_to_new_connection(tmp_path):
    old_publish, new_publish = AsyncMock(), AsyncMock()
    old, new = NativeNoticeChannel(old_publish), NativeNoticeChannel(new_publish)
    registry = ChannelReplies()
    old.bind_group("main")
    target = old.bound_reply("main")
    assert target is not None
    reference = registry.capture(InputScope("main", tmp_path), old, target)
    registry.unregister(reference.binding)
    await old.disconnect()
    new.bind_group("main")
    registry.register(new)
    assert registry.recipient(reference) is None
    with pytest.raises(DeliveryNotSent, match="binding"):
        await new.send_output(reference.original, "old-output", "no")
    old_publish.assert_not_awaited()
    new_publish.assert_not_awaited()


async def test_native_observer_original_reply_keeps_connection_semantics_across_group_switch(tmp_path):
    publish = AsyncMock()
    channel = NativeNoticeChannel(publish)
    registry = ChannelReplies()
    original = UserInput(group_id="main", channel="tui", metadata={"not_routing": "ignore"})
    reference = registry.capture(InputScope("main", tmp_path), channel, original)
    assert reference.original.metadata == {}
    channel.bind_group("other")
    await channel.send_output(reference.original, "observer-output", "report")
    publish.assert_awaited_once_with({
        "type": "notification", "group_id": "main", "notification_id": "observer-output", "content": "report",
    })
    await channel.disconnect()
    with pytest.raises(DeliveryNotSent):
        await channel.send_output(reference.original, "late-observer-output", "no")
    assert publish.await_count == 1


@pytest.mark.parametrize("group", ["", "  ", "bad\x00group", None])
def test_native_invalid_bind_is_rejected_without_replacing_current(group):
    channel = NativeNoticeChannel(AsyncMock())
    channel.bind_group("main")
    original = channel.bound_reply("main")
    with pytest.raises(ValueError):
        channel.bind_group(group)
    assert channel.bound_reply("main") == original


async def test_native_retired_binding_is_receipted_as_pending_not_unknown(tmp_path):
    publish = AsyncMock()
    channel = NativeNoticeChannel(publish)
    registry = ChannelReplies()
    channel.bind_group("main")
    target = channel.bound_reply("main")
    assert target is not None
    reference = registry.capture(InputScope("main", tmp_path), channel, target)
    envelope = freeze_input(reference.original, tmp_path, message_id="explicit-send")
    store = await OutputStore.open(tmp_path / "outputs.sqlite3")
    delivery = OutputDelivery(store, registry.resolve)
    try:
        channel.bind_group("other")
        await delivery.emit(envelope, "send-attempt", reference.binding, ("no",))
        row, = await store.records()
        assert row.status == "pending" and "binding" in row.error
        channel.bind_group("main")
        await delivery.resume()
        row, = await store.records()
        assert row.status == "pending"
        publish.assert_not_awaited()
    finally:
        await delivery.close()
