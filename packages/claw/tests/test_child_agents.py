from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.application.gateway import GatewayServer
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef


def stream_fn(contexts, block=False):
    def stream(model, context, options):
        contexts.append(context)
        last = context.messages[-1]
        if last.role == "user" and "write" in str(last.content):
            message = AssistantMessage(content=(ToolCall(id="write", name="filesystem", arguments={
                "action": "write", "path": "target", "content": "child change"}),))
        else:
            message = AssistantMessage(content=(TextContent(text="child done"),))
        async def generate():
            if block:
                await asyncio.sleep(60)
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    return stream


async def setup(tmp_path, contexts, block=False):
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
        groups=(GroupDef(id="main", is_main=True, workspace=str(tmp_path / "workspace")),))
    server = GatewayServer(config)
    with patch("xdog.claw.application.gateway._build_model_and_options",
               return_value=("test/model", stream_fn(contexts, block))):
        await server.start()
    return server, config


async def receive(reader):
    async with asyncio.timeout(6):
        return json.loads(await reader.readline())


@pytest.mark.parametrize("access,restricted,writes", [("read-only", False, False), ("inherit", False, True),
                                                   ("inherit", True, False)])
async def test_real_gateway_child_access_and_replayed_start_receipts(tmp_path, access, restricted, writes):
    contexts = []
    server, config = await setup(tmp_path, contexts)
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    async def send(request):
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        return await receive(reader)
    try:
        runtime = server._orchestrator._runtimes["main"]
        session = runtime.get_or_create_session(allow_reset=False)
        sid = session.meta.session_id
        if restricted:
            session.access.set_read_only(True)
        request = {"type": "child_control", "request_id": "start", "group_id": "main", "session_id": sid,
                   "action": "start", "text": "write", "access": access}
        result = await send(request)
        assert result["success"]
        identifier = result["data"]["child_id"]
        replay = await send(request)
        assert replay == result and len(session.children.runtime.snapshot()["children"]) == 1
        waited = await send({"type": "child_control", "request_id": "wait", "group_id": "main", "session_id": sid,
                             "action": "wait", "child_id": identifier})
        assert waited["data"]["status"] == "completed"
        assert (tmp_path / "workspace" / "target").exists() == writes and len(contexts) == 2
        stale = await send({"type": "child_control", "request_id": "stale", "group_id": "main", "session_id": "old",
                            "action": "cancel", "child_id": identifier})
        assert not stale["success"] and "session/group changed" in stale["error"]
        assert session.children.store.load()[0]["messages"]
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_socket_reader_stays_responsive_during_child_wait_and_navigation_is_guarded(tmp_path):
    contexts = []
    server, config = await setup(tmp_path, contexts, block=True)
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    def send(identifier, action, **kwargs):
        writer.write((json.dumps({"type": "child_control", "request_id": identifier, "group_id": "main",
                                  "session_id": sid, "action": action, **kwargs}) + "\n").encode())
    try:
        runtime = server._orchestrator._runtimes["main"]
        session = runtime.get_or_create_session(allow_reset=False)
        sid = session.meta.session_id
        send("start", "start", text="task")
        result = await receive(reader)
        child = result["data"]["child_id"]
        send("wait", "wait", child_id=child)
        writer.write(b'{"type":"ping","group_id":"main"}\n')
        pong = await receive(reader)
        assert pong["type"] == "pong" and pong["child_controls"] == 1
        with pytest.raises(RuntimeError, match="Child work"):
            runtime.reset_session()
        send("cancel", "cancel", child_id=child)
        replies = [await receive(reader), await receive(reader)]
        assert {item["request_id"] for item in replies} == {"wait", "cancel"}
        assert all(item["data"]["status"] == "canceled" for item in replies)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_claw_child_message_without_admitted_parent_input_is_safely_refused(tmp_path):
    from xdog.claw.core.tools.tool_message import create_message_tool

    def stream(model, context, options):
        if context.messages[-1].role == "user":
            message = AssistantMessage(content=(ToolCall(id="catalog", name="message", arguments={
                "action": "channels"}),))
        else:
            message = AssistantMessage(content=(TextContent(text="done"),))
        return EventStream.empty(message)

    server, _config = await setup(tmp_path, [])
    try:
        runtime = server._orchestrator._runtimes["main"]
        session = runtime.get_or_create_session(allow_reset=False)
        assert session.agent.tool_context.get("_message_port") is None
        session.agent.set_tools((*[tool for tool in session.agent.local_tools if tool.name != "message"],
                                create_message_tool()))
        session.agent.set_stream_fn(stream)
        identifier = await session.children.runtime.start("list channels", access="inherit")
        result = await session.children.runtime.control("wait", identifier)
        assert result["status"] == "completed"
        assert "requires an actual admitted host input scope" in str(result["messages"])
        assert await (await server._orchestrator.output_service()).store.records() == []
    finally:
        await server.stop()


@pytest.mark.parametrize("access,restricted,limit", [
    ("inherit", False, None),
    ("read-only", False, None),
    ("inherit", True, None),
    ("inherit", False, "child"),
    ("inherit", False, "parent"),
])
async def test_child_single_channel_message_cannot_invent_scope_or_expand_permissions(
    tmp_path, access, restricted, limit,
):
    from unittest.mock import AsyncMock

    from xdog.ai.types import ToolResultMessage
    from xdog.claw.channels.tui.channel import NativeNoticeChannel

    contexts = []
    target, other = NativeNoticeChannel(AsyncMock()), NativeNoticeChannel(AsyncMock())
    target.bind_group("main")
    other.bind_group("elsewhere")
    server, _config = await setup(tmp_path, [])
    binding = server._orchestrator.register_reply_channel(target)
    server._orchestrator.register_reply_channel(other)

    def stream(model, context, options):
        contexts.append(context)
        message = (AssistantMessage(content=(ToolCall(id="send", name="message", arguments={
            "action": "send", "channel": binding, "text": "current-group child message"}),))
            if context.messages[-1].role == "user" else AssistantMessage(content=(TextContent(text="done"),)))
        return EventStream.empty(message)

    try:
        runtime = server._orchestrator._runtimes["main"]
        session = runtime.get_or_create_session(allow_reset=False)
        assert "message" in {tool.name for tool in session.agent.state.tools}
        assert "group_message" not in {tool.name for tool in session.agent.state.tools}
        assert session.agent.tool_context.get("_message_port") is None
        session.agent.set_stream_fn(stream)
        if restricted:
            session.access.set_read_only(True)
        if limit == "parent":
            session.agent.set_active_tools(["current_time"],
                expected_revision=session.agent.tool_selection_revision)
        identifier = await session.children.runtime.start("send", access=access,
            config={"tools": ["current_time"]} if limit == "child" else None)
        result = await session.children.runtime.control("wait", identifier)
        assert result["status"] == "completed"
        assert contexts and all("group_message" not in {tool.name for tool in ctx.tools} for ctx in contexts)
        tool_result = next(message for message in contexts[-1].messages if isinstance(message, ToolResultMessage))
        assert tool_result.is_error
        if limit is not None:
            assert all({tool.name for tool in ctx.tools} == {"current_time"} for ctx in contexts)
            assert "not found" in str(tool_result.content)
        elif restricted or access == "read-only":
            assert "read-only" in str(tool_result.content)
        else:
            assert "requires an actual admitted host input scope" in str(tool_result.content)
        assert await (await server._orchestrator.output_service()).store.records() == []
        target._publish.assert_not_awaited()
        other._publish.assert_not_awaited()
    finally:
        await target.disconnect()
        await other.disconnect()
        await server.stop()


async def test_real_parent_turn_spawns_child_inheriting_message_port_and_settles_on_host_loop(tmp_path):
    from xdog.ai.types import ToolResultMessage
    from xdog.claw.channels.weixin.channel import WeixinChannel
    from xdog.claw.channels.weixin.context_tokens import clear_context_tokens, set_context_token
    from xdog.claw.core.inputs.models import InputScope
    from xdog.claw.core.types import UserInput

    from packages.claw.tests.weixin_application_intake_helpers import ChannelApi, sent_parts

    host = asyncio.get_running_loop()
    parent_contexts, child_contexts, child_receipts = [], [], []
    child_id = None

    def text(message):
        return ''.join(part.text for part in message.content if isinstance(part, TextContent))

    def stream(model, context, options):
        nonlocal child_id
        last = context.messages[-1]
        is_parent = any(tool.name == "agent" for tool in context.tools)
        (parent_contexts if is_parent else child_contexts).append(context)
        if is_parent:
            if last.role == "user":
                response = ToolCall(id="spawn", name="agent", arguments={"action": "spawn", "access": "inherit",
                    "task": "Send exactly one current-group Weixin message through message.channels/send",
                    "config": {"tools": ["message"]}})
            elif last.tool_call_id == "spawn":
                assert not last.is_error, text(last)
                child_id = text(last)
                response = ToolCall(id="wait", name="agent", arguments={"action": "wait", "id": child_id})
            else:
                assert not last.is_error, text(last)
                assert json.loads(text(last))["status"] == "completed"
                response = TextContent(text="Parent display only")
        elif last.role == "user":
            response = ToolCall(id="catalog", name="message", arguments={"action": "channels"})
        else:
            assert isinstance(last, ToolResultMessage) and not last.is_error, text(last)
            value = json.loads(text(last))
            if last.tool_call_id == "catalog":
                binding, = [row["id"] for row in value["channels"] if row["name"] == "weixin"]
                response = ToolCall(id="send", name="message", arguments={
                    "action": "send", "channel": binding, "text": "Delegated explicit message",
                    "reference": "issue:child-42"})
            else:
                child_receipts.append(value)
                response = TextContent(text="Child done, not another channel message")
        return EventStream.empty(AssistantMessage(content=(response,)))

    server, _config = await setup(tmp_path, [])
    channel = WeixinChannel(tmp_path / "weixin", "child-outbound", "https://example.invalid", "")
    await channel._api_client.close()
    api = ChannelApi()
    delivered = []

    async def send(request):
        assert asyncio.get_running_loop() is host
        await asyncio.sleep(0)
        delivered.append(request.msg)

    api.send_message = send
    channel._api_client = api
    channel._proactive_recipient = lambda: "original-peer"
    clear_context_tokens("child-outbound")
    set_context_token("child-outbound", "original-peer", "original-context")
    server._orchestrator.add_channel(channel)
    display = []

    async def show(body):
        display.append(body)

    try:
        app = server._orchestrator
        session = app._runtimes["main"].get_or_create_session(allow_reset=False)
        assert session.agent.tool_context.get("_message_port") is None
        session.agent.set_stream_fn(stream)
        result = await asyncio.wait_for(app.route_message(
            UserInput(group_id="main", channel="tui", content="Delegate the explicit send"),
            message_id="parent-input", on_assistant_message=show), 10)
        assert result is not None and not result.error and result.response_text == "Parent display only"
        assert display == ["Parent display only"]
        assert len(parent_contexts) == 3 and len(child_contexts) == 3
        assert all({tool.name for tool in context.tools} == {"message"} for context in child_contexts)
        assert len(delivered) == 1
        assert sent_parts(delivered[0]) == ("original-peer", "original-context", "Delegated explicit message")
        rows = await (await app.output_service()).store.records(InputScope("main", tmp_path / "workspace"))
        row, = rows
        assert row.status == "delivered" and delivered[0].client_id == row.id
        assert row.envelope.message.provenance["worker"] == child_id
        assert row.envelope.message.provenance["reference"] == "issue:child-42"
        assert row.envelope.message.provenance["input_id"] == "parent-input"
        assert child_receipts[0]["outputs"] == [{"id": row.id, "status": "delivered"}]
        assert child_receipts[0]["default_reply_route_changed"] is False
        assert session.agent.tool_context["_message_port"] is None
    finally:
        await server.stop()
        clear_context_tokens("child-outbound")


async def test_actual_claw_gateway_child_model_reasoning_route_and_read_only_gate(tmp_path):
    from xdog.agent.helpers import stream_fn_from_provider
    from xdog.ai.types import Model, StreamOptions
    server, config = await setup(tmp_path, [])
    calls = []
    class Peer:
        def model(self, name):
            return {"first/parent": Model(id="first/parent", context_window=128000, reasoning=True),
                    "second/review": Model(id="second/review", context_window=64000, max_tokens=1500,
                                           reasoning=True, supported_efforts=("low", "medium"))}.get(name)
        def stream(self, model, context, options):
            calls.append((model, context, options))
            if context.messages[-1].role == "user" and "write" in str(context.messages[-1].content):
                message = AssistantMessage(content=(ToolCall(id="write", name="filesystem", arguments={
                    "action": "write", "path": "target", "content": "mutated"}),))
            else:
                message = AssistantMessage(content=(TextContent(text="done"),))
            return EventStream.empty(message)
    ui = ChatApp(config.socket_path)
    ui._io_generation = 1
    ui._handle_response({"type": "_connection_opened", "generation": 1})
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.004)
    try:
        await wait(lambda: ui._connection_ready and ui._child_model_supported)
        runtime = server._orchestrator._runtimes["main"]
        session = runtime.get_or_create_session(allow_reset=False)
        session.agent.set_model("first/parent")
        session.agent.set_options(StreamOptions(thinking="high", max_tokens=4000))
        session.agent.set_stream_fn(stream_fn_from_provider(Peer()))
        parent_options = session.agent.options
        ui._gateway_session_id = session.meta.session_id
        ui._agents_command("start --model second/review --thinking low --fresh write")
        await wait(lambda: len(calls) == 2 and not session.children.runtime.active and not ui._children_pending)
        assert not (tmp_path / "workspace" / "target").exists()
        assert all(value[0] == "second/review" and value[2].thinking == "low" for value in calls)
        assert calls[0][2].max_tokens <= 1500
        assert session.agent.state.model == "first/parent" and session.agent.options == parent_options
        saved = session.children.store.load()[0]
        assert saved["effective"]["model"] == "second/review" and saved["config"]["thinking"] == "low"
        ui._open_children()
        ui._child_configured_start("review", "read-only", {"model": "second/review", "thinking": "medium", "tools": []})
        await wait(lambda: len(calls) == 3 and not session.children.runtime.active and not ui._children_pending)
        assert calls[-1][0] == "second/review" and calls[-1][2].thinking == "medium" and not calls[-1][1].tools
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('text', ['[]', 'null', '"wrong-shape"', '{"action":"return_commit"}', '{invalid'])
async def test_malformed_child_queue_packets_refuse_without_dropping_owned_socket(tmp_path, text):
    server, config = await setup(tmp_path, [])
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
        writer.write((json.dumps({'type': 'child_control', 'request_id': 'bad-queue', 'group_id': 'main',
                                  'session_id': session.meta.session_id, 'action': 'queue',
                                  'child_id': '0123456789ab', 'text': text}) + '\n').encode())
        await writer.drain()
        rejected = await receive(reader)
        assert rejected['type'] == 'child_result' and rejected['success'] is False
        writer.write(b'{"type":"ping","group_id":"main"}\n')
        await writer.drain()
        pong = await receive(reader)
        assert pong['type'] == 'pong' and pong['child_controls'] == 1
        assert not session.children.runtime.snapshot()['children'] and not session.agent.messages
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
