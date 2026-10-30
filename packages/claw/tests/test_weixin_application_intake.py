"""Real application inbox owns channel acceptance, not a volatile reply worker."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from xdog.claw.channels.weixin.channel import WeixinChannel, _TypingTicketCache
from xdog.claw.channels.weixin.context_tokens import set_context_token
from xdog.claw.channels.weixin.monitor import _load_get_updates_buf, _save_get_updates_buf
from xdog.claw.channels.weixin.types import GetUpdatesResp, MessageItem, MessageItemType, TextItem
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group

from packages.claw.tests.eyes_helpers import ScriptedModel
from packages.claw.tests.test_weixin_monitor import FakeApi, user_message, wait


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


async def test_poll_accepts_while_agent_is_busy_and_replies_use_original_peer(tmp_path):
    model = ScriptedModel()
    entered, release = asyncio.Event(), asyncio.Event()
    model.gates["Alice"] = entered, release
    orch, channel, api = await application(tmp_path, model)
    alice, bob = text(1, "alice", "Alice"), text(2, "bob", "Bob")
    api.updates.put_nowait(GetUpdatesResp(msgs=(alice,), get_updates_buf="alice"))
    await channel.connect()
    try:
        await wait(entered.wait())
        assert (await wait(api.polls.get()))[0] == ""
        assert (await wait(api.polls.get()))[0] == "alice"
        api.updates.put_nowait(GetUpdatesResp(msgs=(bob,), get_updates_buf="bob"))
        assert (await wait(api.polls.get()))[0] == "bob"
        service = await orch.input_service()
        scope = InputScope("main", tmp_path)
        records = await service.store.records(scope)
        assert [r.status for r in records] == ["running", "pending"]
        assert [r.envelope.reply.recipient for r in records] == ["alice", "bob"]
        assert records[0].envelope.context["timestamp"] == 0
        # Neither later arrivals nor mutable caches may redirect accepted output.
        channel._proactive_recipient = lambda: "wrong-peer"
        set_context_token("account", "alice", "newer-not-the-original-context")
        release.set()
        assert sent_parts(await wait(api.sent.get())) == ("alice", "ctx:alice", "Scheduled report")
        assert sent_parts(await wait(api.sent.get())) == ("bob", "ctx:bob", "Scheduled report")
        for record in records:
            assert (await wait(service.wait(scope, record.envelope.message.id))).status == "succeeded"
        assert model.calls == ["Alice", "Bob"] and model.peak == 1
        # A repeated upstream delivery is the same immutable submission.
        api.updates.put_nowait(GetUpdatesResp(msgs=(alice, bob), get_updates_buf="duplicate"))
        assert (await wait(api.polls.get()))[0] == "duplicate"
        assert len(await service.store.records(scope)) == 2
        assert model.calls == ["Alice", "Bob"] and api.sent.empty()
    finally:
        release.set()
        await orch.stop()


async def test_accepted_messages_survive_receiver_disappearance_and_application_restart(tmp_path):
    # Host readiness pauses execution, not acceptance. It is an existing real
    # application startup barrier; no second test-only consumer is installed.
    model = ScriptedModel()
    orch, channel, api = await application(tmp_path, model, defer_inputs=True)
    api.updates.put_nowait(GetUpdatesResp(msgs=(text(7, "original", "recover"),), get_updates_buf="accepted"))
    await channel.connect()
    try:
        assert (await wait(api.polls.get()))[0] == ""
        assert (await wait(api.polls.get()))[0] == "accepted"
        service = await orch.input_service()
        records = await service.store.records(InputScope("main", tmp_path))
        assert len(records) == 1 and records[0].status == "pending"
        identifier = records[0].envelope.message.id
        assert model.calls == []
    finally:
        await orch.stop()
    recovered = ScriptedModel()
    orch, channel, api = await application(tmp_path, recovered)
    channel._proactive_recipient = lambda: "wrong-peer"
    try:
        service = await orch.input_service()
        result = await wait(service.wait(InputScope("main", tmp_path), identifier))
        assert result.status == "succeeded" and recovered.calls == ["recover"]
        assert sent_parts(await wait(api.sent.get())) == ("original", "ctx:original", "Scheduled report")
    finally:
        await orch.stop()


@pytest.mark.parametrize("change", ["missing_identity", "changed_body", "changed_context"])
async def test_unsafe_intake_cannot_advance_upstream_cursor(tmp_path, monkeypatch, change):
    import xdog.claw.channels.weixin.monitor as monitor

    monkeypatch.setattr(monitor, "RETRY_DELAY_S", 0.001)
    model = ScriptedModel()
    orch, channel, api = await application(tmp_path, model, defer_inputs=True)
    original = text(10, "alice", "original")
    if change == "missing_identity":
        offered = replace(original, message_id=0)
    else:
        # Exercise the channel's actual lightweight pre-ACK callback directly,
        # without a monitor or dispatch worker owning accepted execution.
        await channel._accept_inbound(original)
        offered = (
            replace(original, item_list=text(10, "alice", "changed").item_list)
            if change == "changed_body"
            else replace(original, context_token="changed-route")
        )
    _save_get_updates_buf(channel._state_dir, "account", "previous")
    api.updates.put_nowait(GetUpdatesResp(msgs=(offered,), get_updates_buf="unsafe"))
    await channel.connect()
    try:
        assert (await wait(api.polls.get()))[0] == "previous"
        assert (await wait(api.polls.get()))[0] == "previous"
        assert _load_get_updates_buf(channel._state_dir, "account") == "previous"
        records = await (await orch.input_service()).store.records(InputScope("main", tmp_path))
        assert len(records) == (0 if change == "missing_identity" else 1)
        assert model.calls == []
    finally:
        await orch.stop()


async def test_client_identity_is_stable_and_namespaced_by_sender_and_account(tmp_path):
    model = ScriptedModel()
    orch, channel, api = await application(tmp_path, model, defer_inputs=True)
    try:
        first = text(0, "alice", "first", client_id="upstream-client")
        await channel._accept_inbound(first)
        await channel._accept_inbound(first)
        await channel._accept_inbound(text(0, "bob", "second", client_id="upstream-client"))
        another = WeixinChannel(tmp_path / "channel", "other-account", "https://example.invalid", "")
        await another._api_client.close()
        another._api_client = api
        orch.add_channel(another)
        await another._accept_inbound(first)
        records = await (await orch.input_service()).store.records(InputScope("main", tmp_path))
        assert len(records) == 3 and len({r.envelope.message.id for r in records}) == 3
    finally:
        await orch.stop()


async def test_durable_acceptance_refuses_an_unregistered_target(tmp_path):
    orch, channel, api = await application(tmp_path, ScriptedModel())
    channel._group_id = "missing"
    try:
        with pytest.raises(ValueError, match="unknown group"):
            await channel._accept_inbound(text(1, "alice", "must not ACK"))
    finally:
        await orch.stop()


async def test_duplicate_failed_input_does_not_resend_error_or_rerun_agent(tmp_path):
    from xdog.ai.types import AssistantMessage

    model = ScriptedModel()
    model.replies["fail"] = AssistantMessage(content=(), stop_reason="error", error_message="expected failure")
    orch, channel, api = await application(tmp_path, model)
    offered = text(1, "alice", "fail")
    try:
        await channel._accept_inbound(offered)
        service = await orch.input_service()
        records = await service.store.records(InputScope("main", tmp_path))
        result = await wait(service.wait(InputScope("main", tmp_path), records[0].envelope.message.id))
        assert result.status == "failed"
        assert sent_parts(await wait(api.sent.get())) == ("alice", "ctx:alice", "暂时无法处理这条消息，请稍后再试。")
        await channel._accept_inbound(offered)
        await channel._on_inbound(offered)
        assert model.calls == ["fail"] and api.sent.empty()
    finally:
        await orch.stop()


async def test_full_core_capacity_blocks_cursor_without_losing_first_message(tmp_path, monkeypatch):
    import xdog.claw.channels.weixin.monitor as monitor
    from xdog.claw.core.types import GroupConfig

    monkeypatch.setattr(monitor, "RETRY_DELAY_S", 0.001)
    model = ScriptedModel()
    orch, channel, api = await application(tmp_path, model, defer_inputs=True)
    runtime = orch._runtimes["main"]
    runtime.group = replace(runtime.group, config=GroupConfig(max_queued_messages=1))
    await channel._accept_inbound(text(1, "alice", "accepted"))
    _save_get_updates_buf(channel._state_dir, "account", "previous")
    api.updates.put_nowait(GetUpdatesResp(msgs=(text(2, "bob", "full"),), get_updates_buf="unsafe"))
    await channel.connect()
    try:
        assert (await wait(api.polls.get()))[0] == "previous"
        assert (await wait(api.polls.get()))[0] == "previous"
        assert _load_get_updates_buf(channel._state_dir, "account") == "previous"
        records = await (await orch.input_service()).store.records(InputScope("main", tmp_path))
        assert len(records) == 1 and records[0].envelope.message.instruction == "accepted"
        assert records[0].status == "pending" and model.calls == []
    finally:
        await orch.stop()
