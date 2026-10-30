from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


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
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
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


async def test_actual_claw_tui_sends_child_commands_and_preserves_draft(tmp_path):
    contexts = []
    server, config = await setup(tmp_path, contexts)
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
                await asyncio.sleep(0.005)
    try:
        await wait(lambda: ui._connection_ready and ui._children_supported)
        ui._editor.set_text("preserved draft")
        ui._agents_command("start task")
        await wait(lambda: not ui._children_pending and ui._gateway_session_id)
        session = server._orchestrator._runtimes["main"]._active_session
        identifier = session.children.runtime.snapshot()["children"][0]["id"]
        ui._agents_command("wait " + identifier)
        await wait(lambda: not ui._children_pending)
        assert ui._editor.get_text() == "preserved draft" and len(contexts) == 1
        assert "child done" in "\n".join(ui._chat_log.render(80))
        ui._agents_command("inspect " + identifier)
        await wait(lambda: not ui._children_pending)
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
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

    from packages.claw.tests.test_weixin_application_intake import ChannelApi, sent_parts

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


async def test_actual_claw_child_browser_chunked_source_search_and_context_fences(tmp_path):
    contexts = []
    server, config = await setup(tmp_path, contexts)
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
                await asyncio.sleep(0.005)
    try:
        await wait(lambda: ui._connection_ready and ui._child_source_supported)
        ui._agents_command("start task")
        await wait(lambda: not ui._children_pending and ui._gateway_session_id)
        session = server._orchestrator._runtimes["main"]._active_session
        child = session.children.runtime.snapshot()["children"][0]["id"]
        await session.children.runtime.control("wait", child)
        ui._editor.set_text("parent draft")
        ui._agents_command("inspect " + child)
        await wait(lambda: ui._child_panel is not None and ui._child_panel.preview is not None
                   and ui._child_panel.preview.settled and ui._child_source_pending is None)
        preview = ui._child_panel.preview
        assert "child done" in str(preview.records)
        preview.search("child done")
        await wait(lambda: preview.settled)
        assert preview._view.matches == 1
        old = tuple(preview.records)
        ui._handle_response({"type": "child_source_ready", "request_id": "foreign", "group_id": "main",
                             "session_id": ui._gateway_session_id + ":" + child, "records": ()})
        assert preview.records == old
        ui._close_children()
        assert ui._editor.get_text() == "parent draft" and ui._tui._focused is ui._editor
        assert len(contexts) == 1
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_claw_child_source_streams_running_progress_before_final_result(tmp_path):
    import threading

    from xdog.agent import AgentTool, AgentToolResult
    entered, release = threading.Event(), threading.Event()
    server, config = await setup(tmp_path, [])
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    async def execute(identifier, args, cancel=None, on_update=None, **kwargs):
        await on_update(AgentToolResult(content=(TextContent(text="CLAW-LIVE-PROGRESS"),)))
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.002)
        return AgentToolResult(content=(TextContent(text="CLAW-FINAL-RESULT"),))
    def stream(model, context, options):
        message = (AssistantMessage(content=(ToolCall(id="run", name="run", arguments={}),))
                   if context.messages[-1].role == "user" else AssistantMessage(content=(TextContent(text="done"),)))
        return EventStream.empty(message)
    async def source(session, child, identifier):
        import base64
        writer.write((json.dumps({"type": "child_source_read", "request_id": identifier, "group_id": "main",
                                  "session_id": session.meta.session_id, "child_id": child}) + "\n").encode())
        await writer.drain()
        data = []
        while True:
            value = await receive(reader)
            if value["type"] == "child_source_chunk":
                data.append(base64.b64decode(value["data"]))
            elif value["type"] == "child_source_end":
                return b"".join(data).decode()
    try:
        session = server._orchestrator._runtimes["main"].get_or_create_session(allow_reset=False)
        session.agent.set_tools((*session.agent.local_tools,
                                 AgentTool(name="run", parameters={"type": "object"}, execute=execute)))
        session.agent.set_stream_fn(stream)
        child = await session.children.runtime.start("work", access="inherit")
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.002)
        live = await source(session, child, "live")
        assert "CLAW-LIVE-PROGRESS" in live and "final result pending" in live
        assert "CLAW-FINAL-RESULT" not in live and session.children.runtime.active
        release.set()
        await session.children.runtime.control("wait", child)
        final = await source(session, child, "final")
        assert "CLAW-FINAL-RESULT" in final and "CLAW-LIVE-PROGRESS" not in final
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_claw_configured_child_gateway_and_cli_restrict_tools_and_preserve_context_policy(tmp_path):
    from xdog.ai.types import UserMessage
    contexts = []
    server, config = await setup(tmp_path, contexts)
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
        await wait(lambda: ui._connection_ready)
        runtime = server._orchestrator._runtimes["main"]
        session = runtime.get_or_create_session(allow_reset=False)
        session._restored = True
        session.agent.append_message(UserMessage(content="PARENT-HISTORY-MARKER"))
        # Session created outside this UI; refresh its lease before explicit start.
        ui._gateway_session_id = session.meta.session_id
        ui._agents_command('start --label Reviewer --instructions "CLAW-SPECIALIST" --tools current_time '
                           '--fresh -- write')
        await wait(lambda: len(contexts) == 2 and not session.children.runtime.active and not ui._children_pending)
        assert "CLAW-SPECIALIST" not in str(contexts[0].system_prompt)
        assert "CLAW-SPECIALIST" in str(contexts[0].messages)
        assert "PARENT-HISTORY-MARKER" not in str(contexts[0].messages)
        assert [tool.name for tool in contexts[0].tools] == ["current_time"]
        assert not (tmp_path / "workspace" / "target").exists()
        record = session.children.store.load()[0]
        assert record["config"]["label"] == "Reviewer" and "max_tokens" not in record["config"]
        assert session.agent.messages[0].content == "PARENT-HISTORY-MARKER"
        ui._open_children()
        ui._child_configured_start("plain", "read-only", {"label": "UI specialist", "tools": []})
        await wait(lambda: len(contexts) == 3 and not session.children.runtime.active and not ui._children_pending)
        assert not contexts[-1].tools
        assert any(record["config"]["label"] == "UI specialist" for record in session.children.store.load())
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_old_gateway_cannot_silently_ignore_specialist_settings():
    ui = ChatApp("unused.sock")
    ui._connection_ready = True
    ui._children_supported = True
    ui._child_config_supported = False
    try:
        ui._agents_command('start --label Reviewer --tools none task')
        assert ui._send_queue.empty() and "--label Reviewer" in ui._editor.get_text()
        ui._open_children()
        # The roster read is safe on old gateways; clear it before specialized start.
        while not ui._send_queue.empty():
            ui._send_queue.get_nowait()
        ui._child_configured_start("task", "read-only", {"tools": []})
        assert ui._send_queue.empty() and "no settings silently ignored" in ui._child_panel._error
    finally:
        ui._request_exit()


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


def test_child_config_v1_gateway_does_not_silently_ignore_model_reasoning_selection():
    ui = ChatApp("unused.sock")
    ui._connection_ready = True
    ui._children_supported = True
    ui._child_config_supported = True
    ui._child_model_supported = False
    try:
        ui._agents_command("start --model second/review --thinking low task")
        assert ui._send_queue.empty() and "--model second/review" in ui._editor.get_text()
        ui._open_children()
        while not ui._send_queue.empty():
            ui._send_queue.get_nowait()
        ui._child_configured_start("task", "read-only", {"model": "second/review", "thinking": "low"})
        assert ui._send_queue.empty() and "no routing/reasoning silently ignored" in ui._child_panel._error
    finally:
        ui._request_exit()


async def test_claw_preset_catalog_cli_and_picker_apply_frozen_template_without_extra_authority(tmp_path):
    from xdog.tui.keys import KeyEvent
    calls = []
    presets = {"reviewer": {"description": "Read-only review", "config": {
        "instructions": "CLAW-PRESET-INSTRUCTIONS", "tools": ["current_time"], "inherit_context": False}}}
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "presets.sock"), pid_file=str(tmp_path / "presets.pid"),
        groups=(GroupDef(id="main", is_main=True, workspace=str(tmp_path / "workspace")),), child_presets=presets)
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
               return_value=("test/model", stream_fn(calls))):
        await server.start()
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
        await wait(lambda: ui._connection_ready and ui._child_presets_supported)
        ui._agents_command("presets")
        await wait(lambda: ui._child_panel is not None and ui._child_panel._presets)
        assert not calls and not ui._gateway_session_id  # Browsing creates neither a session nor model work.
        ui._child_panel.render(80)
        ui._child_panel.handle_input(KeyEvent("enter"))
        chosen = ui._child_panel._config
        assert chosen["preset"] == "reviewer" and "max_model_calls" not in chosen and not calls
        ui._close_children()
        ui._agents_command("start --preset reviewer write")
        await wait(lambda: len(calls) == 2 and not ui._children_pending)
        session = server._orchestrator._runtimes["main"]._active_session
        await wait(lambda: not session.children.runtime.active)
        assert not (tmp_path / "workspace" / "target").exists()
        assert "CLAW-PRESET-INSTRUCTIONS" not in str(calls[0].system_prompt)
        assert "CLAW-PRESET-INSTRUCTIONS" in str(calls[0].messages)
        assert [tool.name for tool in calls[0].tools] == ["current_time"]
        saved = session.children.store.load()[0]
        assert "max_tokens" not in saved["config"] and saved["config"]["preset"] == "reviewer"
        ui._agents_command("presets")
        await wait(lambda: ui._child_panel is not None and ui._child_panel._presets)
        ui._child_configured_start("plain", "read-only", chosen)
        await wait(lambda: len(calls) == 3 and not ui._children_pending and not session.children.runtime.active)
        assert len(session.children.store.load()) == 2
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_invalid_claw_preset_cannot_fallback_to_default_configuration(tmp_path):
    from xdog.claw.config import ToolConfigError, load_config
    file = tmp_path / "config.yaml"
    file.write_text('model: test/model\nchild_presets:\n  writer:\n    config:\n      access: inherit\n')
    with pytest.raises(ToolConfigError, match="Invalid child_presets"):
        load_config(file)


def test_old_config_gateways_receive_only_their_supported_inherited_fields():
    ui = ChatApp("unused.sock")
    ui._connection_ready = True
    ui._children_supported = True
    ui._child_config_supported = True
    ui._child_model_supported = False
    ui._child_presets_supported = False
    try:
        ui._open_children()
        while not ui._send_queue.empty():
            ui._send_queue.get_nowait()
        ui._child_configured_start("task", "read-only", {"tools": []})
        request = ui._send_queue.get_nowait()
        assert request["action"] == "start" and request["config"]["tools"] == []
        assert not {"model", "thinking", "preset"} & set(request["config"])
        ui._agents_command("start --preset reviewer task")
        assert ui._send_queue.empty() and "--preset reviewer" in ui._editor.get_text()
    finally:
        ui._request_exit()


@pytest.mark.parametrize('command', ['return_prepare', 'return_commit', 'recover'])
async def test_retired_socket_recovery_commands_refuse_before_backend_and_keep_queued_images(tmp_path, command):
    from unittest.mock import AsyncMock

    from xdog.ai.types import ImageContent
    contexts = []
    server, config = await setup(tmp_path, contexts, block=True)
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=1024 * 1024)
    async def send(request):
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        return await receive(reader)
    try:
        session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
        sid = session.meta.session_id
        child = await session.children.runtime.start('held text')
        async with asyncio.timeout(5):
            while not contexts:
                await asyncio.sleep(0.003)
        image = ImageContent(data=('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE'
                                  'QVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), mime_type='image/png')
        await session.children.runtime.control('steer', child, 'UNREMOVED RETURN', images=(image,))
        caps = await send({'type': 'ping', 'group_id': 'main', 'child_image_return': 1, 'child_recovery': 1})
        assert caps['child_controls'] == caps['child_images'] == 1
        assert not any(name in caps for name in ('child_image_return', 'child_return_owner', 'child_recovery'))
        before = (await session.children.runtime.control('queue', child, '{"action":"list"}'))['queue']
        snapshot = json.dumps(before, sort_keys=True).encode()
        pending = session.children.runtime._children[child].agent.pending_inputs()
        assert len(pending) == 1 and pending[0].images[0].data == image.data
        item = before['inputs'][0]
        packet = {'type': 'child_control', 'request_id': 'retired', 'group_id': 'main', 'session_id': sid,
                  'action': 'queue', 'child_id': child, 'text': json.dumps({
                      'action': command, 'id': item['id'], 'revision': before['revision']})}
        backend = AsyncMock(side_effect=AssertionError('Retired frontend command reached backend'))
        with patch.object(server._orchestrator, 'manage_children', backend):
            for _ in range(2):
                rejected = await send(packet)
                assert rejected['type'] == 'child_result' and not rejected['success']
                assert rejected['request_id'] == 'retired' and 'read-only' in rejected['error']
            backend.assert_not_awaited()
        after = (await session.children.runtime.control('queue', child, '{"action":"list"}'))['queue']
        assert json.dumps(after, sort_keys=True).encode() == snapshot
        assert session.children.runtime._children[child].agent.pending_inputs() == pending
        listed = await send({**packet, 'request_id': 'list', 'text': '{"action":"list"}'})
        assert listed['success'] and listed['data']['queue'] == before
        pong = await send({'type': 'ping', 'group_id': 'main'})
        assert pong['type'] == 'pong' and pong['child_controls'] == 1
        assert not session.agent.messages and len(contexts) == 1
        await session.children.runtime.control('cancel', child)
        assert session.children.store.load()[0]['undelivered_inputs'][0]['images'][0]['data'] == image.data
        assert len(contexts) == 1  # Neither denial nor cancellation replays queued content.
    finally:
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
