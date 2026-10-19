"""Actual model/tools, owned journal and transport data preserve the creator."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, ToolCall
from xdog.claw.acp.server import ClawAcpServer
from xdog.claw.channels.weixin.channel import WeixinChannel, _TypingTicketCache
from xdog.claw.channels.weixin.context_tokens import set_context_token
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.observers.manager import ManagedObservers
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group

from packages.claw.tests.eyes_helpers import ManualClock, ScriptedModel, at
from packages.claw.tests.test_gateway_inputs import receive, send
from packages.claw.tests.test_weixin_application_intake import ChannelApi, sent_parts, text


async def until(operation):
    async with asyncio.timeout(5):
        while True:
            result = await operation()
            if result:
                return result
            await asyncio.sleep(0.005)


def instruct(model, source, name="watch"):
    model.replies["WATCH"] = AssistantMessage(
        content=(
            ToolCall(
                id="create-watch",
                name="eyes",
                arguments={"action": "create", "name": name, "source": source, "instruction": "CALLBACK"},
            ),
        ),
        stop_reason="toolUse",
    )


@asynccontextmanager
async def weixin(tmp_path):
    clock, model = ManualClock(), ScriptedModel()
    app = Orchestrator(
        ClawConfig(data_dir=str(tmp_path / "data"), eyes_enabled=True, enabled_tools=("eyes",)),
        model="test/dummy",
        stream_fn=model,
    )
    app.register_group(Group(id="main", workspace=str(tmp_path / "work"), enabled_tools=("eyes",)))
    channel = WeixinChannel(tmp_path / "channel", "account", "https://example.invalid", "")
    await channel._api_client.close()
    api = ChannelApi()
    channel._api_client = api
    channel._typing_tickets = _TypingTicketCache(api)
    app.add_channel(channel)
    endpoint = tmp_path / "acp.sock"
    server = await ClawAcpServer.start(app, endpoint)
    manager = ManagedObservers(app, endpoint, clocks=clock.clocks())
    app.set_observers(manager)
    await manager.start()
    try:
        yield app, manager, channel, api, clock, model, server
    finally:
        await manager.close()
        await server.close()
        await app.stop()


async def speak(channel, identifier, sender, body):
    message = text(identifier, sender, body)
    await channel._accept_inbound(message)
    await channel._on_inbound(message)


@pytest.mark.parametrize("source_type", ["scheduler", "file"])
async def test_real_tool_captures_alice_before_bob_and_delivers_to_original_context(tmp_path, source_type):
    async with weixin(tmp_path) as (app, manager, channel, api, clock, model, server):
        root = app.get_group_workspace("main")
        watched = root / "watched.txt"
        watched.write_text("initial")
        source = (
            at()
            if source_type == "scheduler"
            else {"type": "file", "root": ".", "include": ["watched.txt"], "poll_interval_ms": 10}
        )
        instruct(model, source)
        await speak(channel, 1, "alice", "WATCH")
        (created,) = await manager.list("main")
        record = await manager.store.get("main", root, created["id"])
        assert record.reply.original.sender == "alice"
        assert record.reply.original.metadata["context_token"] == "ctx:alice"
        # The original input's private capability must not remain on an idle Agent.
        assert app._runtimes["main"]._active_session.agent.tool_context.get("_reply_reference") is None
        await speak(channel, 2, "bob", "BOB")
        while not api.sent.empty():
            api.sent.get_nowait()
        set_context_token("account", "alice", "newer-context-not-the-original")
        assert channel._user_id_map["main"] == "bob"
        if source_type == "scheduler":
            clock.advance(240)
        else:

            async def watching():
                return (await manager.status("main", created["id"]))["state"] == "watching"

            await until(watching)
            watched.write_text("changed evidence")
        sent = await asyncio.wait_for(api.sent.get(), 5)
        assert sent_parts(sent) == ("alice", "ctx:alice", "Scheduled report")
        assert model.calls.count("CALLBACK") == 1
        status = await manager.status("main", created["id"])
        assert "ctx:alice" not in str(status) and "newer-context" not in str(status)


async def test_no_creator_destination_never_selects_a_group_peer(tmp_path):
    async with weixin(tmp_path) as (app, manager, channel, api, clock, model, server):
        await speak(channel, 1, "bob", "BOB")
        while not api.sent.empty():
            api.sent.get_nowait()
        created = await manager.create("main", "without-recipient", at(), "CALLBACK")
        clock.advance(240)

        async def completed():
            status = await manager.status("main", created["id"])
            rows = status["occurrences"]
            return status if rows and rows[0]["delivery_status"] == "not-required" else None

        await until(completed)
        assert api.sent.empty() and model.calls.count("CALLBACK") == 1


@pytest.mark.parametrize("creator_online", [True, False])
async def test_native_notice_uses_only_creator_binding_never_a_new_peer(tmp_path, creator_online):
    clock, model = ManualClock(), ScriptedModel()
    config = ClawConfig(
        model="test/dummy",
        data_dir=str(tmp_path / "data"),
        eyes_enabled=True,
        enabled_tools=("eyes",),
        socket_path=str(tmp_path / "native.sock"),
        pid_file=str(tmp_path / "native.pid"),
        acp_socket_path=str(tmp_path / "a.sock"),
        groups=(GroupDef(id="main", workspace=str(tmp_path / "work")),),
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/dummy", model)):
        await server.start()
    manager = server._observers
    manager.clocks = clock.clocks()
    instruct(model, at())
    original_reader, original_writer = await asyncio.open_unix_connection(config.socket_path)
    other_reader, other_writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        await send(original_writer, {"type": "message", "group_id": "main", "run_id": "creator", "content": "WATCH"})
        await receive(original_reader, "response")
        (created,) = await manager.list("main")
        if not creator_online:
            original_writer.close()
            await original_writer.wait_closed()
            # A ping round-trip after disconnect lets the actual cleanup detach its binding.
            await send(other_writer, {"type": "ping", "group_id": "main"})
            greeting = await receive(other_reader, "pong")
            if greeting.get("history_count", 0):
                await receive(other_reader, "history_end")
        clock.advance(240)
        if creator_online:
            notice = await receive(original_reader, "notification")
            assert notice["content"] == "Scheduled report"

        async def completed():
            state = await manager.status("main", created["id"])
            rows = state["occurrences"]
            want = "delivered" if creator_online else "pending"
            return (
                state
                if rows and rows[0]["execution_status"] == "succeeded" and rows[0]["delivery_status"] == want
                else None
            )

        await until(completed)
        await send(other_writer, {"type": "ping", "group_id": "main"})
        frame = await asyncio.wait_for(other_reader.readline(), 3)
        import json

        assert json.loads(frame)["type"] == "pong", "An unrelated peer received an unsolicited report"
        assert model.calls.count("CALLBACK") == 1
        if not creator_online:
            await manager.cancel_notifications("main", created["id"])
            assert (await manager.status("main", created["id"]))["occurrences"][0]["delivery_status"] == "not-required"
    finally:
        original_writer.close()
        other_writer.close()
        await original_writer.wait_closed()
        await other_writer.wait_closed()
        await server.stop()


async def test_saved_weixin_report_recovers_original_target_after_application_restart_without_model_replay(
    tmp_path, monkeypatch
):
    async with weixin(tmp_path) as (app, manager, channel, api, clock, model, server):
        instruct(model, at())
        await speak(channel, 1, "alice", "WATCH")
        (created,) = await manager.list("main")
        while not api.sent.empty():
            api.sent.get_nowait()
        reached = asyncio.Event()

        async def hold_before_send(record):
            reached.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(manager, "_report", hold_before_send)
        clock.advance(240)
        await asyncio.wait_for(reached.wait(), 5)
        assert model.calls.count("CALLBACK") == 1 and api.sent.empty()
        await manager.close()
        await server.close()
        await manager.orchestrator.stop()
        # A new app/transport, same explicit account and private source endpoint.
        groups = [runtime.group for runtime in app._runtimes.values()]
        recovered = Orchestrator(app._config, model="test/dummy", stream_fn=model)
        for group in groups:
            recovered.register_group(group)
        ch = WeixinChannel(tmp_path / "channel", "account", "https://example.invalid", "")
        await ch._api_client.close()
        new_api = ChannelApi()
        ch._api_client = new_api
        ch._typing_tickets = _TypingTicketCache(new_api)
        ch._user_id_map["main"] = "different-peer"
        recovered.add_channel(ch)
        new_server = await ClawAcpServer.start(recovered, manager.endpoint)
        restored = ManagedObservers(recovered, manager.endpoint, clocks=clock.clocks())
        recovered.set_observers(restored)
        try:
            await restored.start()
            sent = await asyncio.wait_for(new_api.sent.get(), 5)
            assert sent_parts(sent) == ("alice", "ctx:alice", "Scheduled report")
            assert model.calls.count("CALLBACK") == 1
        finally:
            await restored.close()
            await new_server.close()
            await recovered.stop()


async def test_a_later_creator_or_forged_reply_argument_cannot_retarget_an_existing_observer(tmp_path):
    from xdog.claw.core.tools.tool_eyes import create_eyes_tool

    async with weixin(tmp_path) as (app, manager, channel, api, clock, model, server):
        instruct(model, at())
        await speak(channel, 1, "alice", "WATCH")
        (created,) = await manager.list("main")
        original = await manager.store.get("main", manager._cwd("main"), created["id"])
        await speak(channel, 2, "bob", "WATCH")  # Same name/definition, different frozen creator.
        assert (await manager.store.get("main", manager._cwd("main"), created["id"])).reply == original.reply
        ctx = {**app._runtimes["main"]._active_session.agent.tool_context, "_reply_reference": original.reply}
        result = await create_eyes_tool().execute(
            "spoof",
            {
                "action": "create",
                "name": "extra-fields",
                "source": at(),
                "instruction": "CALLBACK",
                "reply": {"recipient": "bob"},
                "reply_reference": {"recipient": "bob"},
                "group_id": "other",
            },
            ctx=ctx,
        )
        assert not result.is_error
        saved = next(
            item for item in await manager.store.list("main", manager._cwd("main")) if item.name == "extra-fields"
        )
        assert saved.reply == original.reply


async def test_partial_target_send_is_unknown_and_never_replays_accepted_chunks_or_model(tmp_path):
    from xdog.ai.types import TextContent

    async with weixin(tmp_path) as (app, manager, channel, api, clock, model, server):
        instruct(model, at())
        await speak(channel, 1, "alice", "WATCH")
        (created,) = await manager.list("main")
        while not api.sent.empty():
            api.sent.get_nowait()
        model.replies["CALLBACK"] = AssistantMessage(content=(TextContent(text="x" * 3500),))
        sends = []

        async def partially_accepted(request):
            sends.append(request.msg)
            if len(sends) > 1:
                raise ConnectionError("second chunk ACK uncertain")
            api.sent.put_nowait(request.msg)

        api.send_message = partially_accepted
        clock.advance(240)

        async def unknown():
            status = await manager.status("main", created["id"])
            rows = status["occurrences"]
            return rows[0] if rows and rows[0]["delivery_status"] == "unknown" else None

        row = await until(unknown)
        import json

        (outcome,) = json.loads(row["delivery_error"])
        assert outcome["accepted_chunks"] == 1 and len(sends) == 2
        assert all(item.to_user_id == "alice" and item.context_token == "ctx:alice" for item in sends)
        assert len({item.client_id for item in sends}) == 2
        await manager.close()
        fresh = ManagedObservers(app, manager.endpoint, clocks=clock.clocks())
        app.set_observers(fresh)
        try:
            await fresh.start()
            await fresh.store.pending_reports()  # Owned disk round trip after startup reconciliation.
            assert len(sends) == 2 and model.calls.count("CALLBACK") == 1
        finally:
            await fresh.close()
