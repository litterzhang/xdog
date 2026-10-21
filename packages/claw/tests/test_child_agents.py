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
        assert replay == result and len(session.children.supervisor.snapshot()["children"]) == 1
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
        identifier = session.children.supervisor.snapshot()["children"][0]["id"]
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


async def test_claw_child_group_service_executes_and_settles_on_original_host_loop(tmp_path):
    from xdog.claw.core.tools.tool_message import create_message_tool
    host = asyncio.get_running_loop()
    sent = []
    def stream(model, context, options):
        if context.messages[-1].role == "user":
            message = AssistantMessage(content=(ToolCall(id="send", name="message", arguments={
                "action": "send", "group_id": "main", "text": "child message"}),))
        else:
            message = AssistantMessage(content=(TextContent(text="done"),))
        return EventStream.empty(message)
    server, _config = await setup(tmp_path, [])
    try:
        runtime = server._orchestrator._runtimes["main"]
        session = runtime.get_or_create_session(allow_reset=False)
        async def send(group, text):
            assert asyncio.get_running_loop() is host
            await asyncio.sleep(0)
            sent.append((group, text))
        session.agent.set_tool_context({**session.agent.tool_context, "_send_fn": send})
        session.agent.set_tools((*[tool for tool in session.agent.state.tools if tool.name != "message"],
                                create_message_tool()))
        session.agent.set_stream_fn(stream)
        identifier = await session.children.supervisor.start("send", access="inherit")
        result = await session.children.supervisor.control("wait", identifier)
        assert result["status"] == "completed" and sent == [("main", "child message")]
        assert "Message sent." in str(result["messages"])
    finally:
        await server.stop()


@pytest.mark.parametrize("access,restricted,limit,sends", [
    ("inherit", False, None, True),
    ("read-only", False, None, False),
    ("inherit", True, None, False),
    ("inherit", False, "child", False),
    ("inherit", False, "parent", False),
])
async def test_message_uses_real_channel_route_without_expanding_child_permissions(
    tmp_path, access, restricted, limit, sends,
):
    from unittest.mock import AsyncMock, Mock

    from xdog.claw.channels.base import Channel

    host = asyncio.get_running_loop()
    contexts, sent = [], []
    def stream(model, context, options):
        contexts.append(context)
        message = (AssistantMessage(content=(ToolCall(id="send", name="message", arguments={
            "action": "send", "group_id": "destination", "text": "routed child message"}),))
            if context.messages[-1].role == "user" else AssistantMessage(content=(TextContent(text="done"),)))
        return EventStream.empty(message)

    async def deliver(group_id, text):
        assert asyncio.get_running_loop() is host
        await asyncio.sleep(0)
        sent.append((group_id, text))

    server, _config = await setup(tmp_path, [])
    target, other = Mock(spec=Channel), Mock(spec=Channel)
    target.accepts_group.side_effect = lambda group_id: group_id == "destination"
    other.accepts_group.side_effect = lambda group_id: group_id == "elsewhere"
    target.send_message = AsyncMock(side_effect=deliver)
    other.send_message = AsyncMock()
    server._orchestrator.add_channel(target)
    server._orchestrator.add_channel(other)
    try:
        runtime = server._orchestrator._runtimes["main"]
        session = runtime.get_or_create_session(allow_reset=False)
        assert "message" in {tool.name for tool in session.agent.state.tools}
        assert "group_message" not in {tool.name for tool in session.agent.state.tools}
        # Keep the actual session context -> runtime callback -> Orchestrator route.
        assert session.agent.tool_context["_send_fn"] == server._orchestrator._send_to_channels
        session.agent.set_stream_fn(stream)
        if restricted:
            session.access.set_read_only(True)
        if limit == "parent":
            session.agent.set_active_tools(["current_time"],
                expected_revision=session.agent.tool_selection_revision)
        identifier = await session.children.supervisor.start("send", access=access,
            config={"tools": ["current_time"]} if limit == "child" else None)
        result = await session.children.supervisor.control("wait", identifier)
        assert result["status"] == "completed"
        assert contexts and all("group_message" not in {tool.name for tool in ctx.tools} for ctx in contexts)
        if limit is not None:
            assert all({tool.name for tool in ctx.tools} == {"current_time"} for ctx in contexts)
        if sends:
            assert sent == [("destination", "routed child message")]
            target.send_message.assert_awaited_once_with("destination", "routed child message")
            assert "Message sent." in str(result["messages"])
        else:
            assert not sent
            target.send_message.assert_not_awaited()
            assert "Message sent." not in str(result["messages"])
            if restricted or access == "read-only":
                assert "read-only" in str(result["messages"])
        other.send_message.assert_not_awaited()
    finally:
        await server.stop()


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
        child = session.children.supervisor.snapshot()["children"][0]["id"]
        await session.children.supervisor.control("wait", child)
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
        session.agent.set_tools((*session.agent.state.tools,
                                 AgentTool(name="run", parameters={"type": "object"}, execute=execute)))
        session.agent.set_stream_fn(stream)
        child = await session.children.supervisor.start("work", access="inherit")
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.002)
        live = await source(session, child, "live")
        assert "CLAW-LIVE-PROGRESS" in live and "final result pending" in live
        assert "CLAW-FINAL-RESULT" not in live and session.children.supervisor.active
        release.set()
        await session.children.supervisor.control("wait", child)
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
        await wait(lambda: len(contexts) == 2 and not session.children.supervisor.active and not ui._children_pending)
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
        await wait(lambda: len(contexts) == 3 and not session.children.supervisor.active and not ui._children_pending)
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
        await wait(lambda: len(calls) == 2 and not session.children.supervisor.active and not ui._children_pending)
        assert not (tmp_path / "workspace" / "target").exists()
        assert all(value[0] == "second/review" and value[2].thinking == "low" for value in calls)
        assert calls[0][2].max_tokens <= 1500
        assert session.agent.state.model == "first/parent" and session.agent.options == parent_options
        saved = session.children.store.load()[0]
        assert saved["effective"]["model"] == "second/review" and saved["config"]["thinking"] == "low"
        ui._open_children()
        ui._child_configured_start("review", "read-only", {"model": "second/review", "thinking": "medium", "tools": []})
        await wait(lambda: len(calls) == 3 and not session.children.supervisor.active and not ui._children_pending)
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
        await wait(lambda: not session.children.supervisor.active)
        assert not (tmp_path / "workspace" / "target").exists()
        assert "CLAW-PRESET-INSTRUCTIONS" not in str(calls[0].system_prompt)
        assert "CLAW-PRESET-INSTRUCTIONS" in str(calls[0].messages)
        assert [tool.name for tool in calls[0].tools] == ["current_time"]
        saved = session.children.store.load()[0]
        assert "max_tokens" not in saved["config"] and saved["config"]["preset"] == "reviewer"
        ui._agents_command("presets")
        await wait(lambda: ui._child_panel is not None and ui._child_panel._presets)
        ui._child_configured_start("plain", "read-only", chosen)
        await wait(lambda: len(calls) == 3 and not ui._children_pending and not session.children.supervisor.active)
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
        child = await session.children.supervisor.start('held text')
        async with asyncio.timeout(5):
            while not contexts:
                await asyncio.sleep(0.003)
        image = ImageContent(data=('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE'
                                  'QVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), mime_type='image/png')
        await session.children.supervisor.control('steer', child, 'UNREMOVED RETURN', images=(image,))
        caps = await send({'type': 'ping', 'group_id': 'main', 'child_image_return': 1, 'child_recovery': 1})
        assert caps['child_controls'] == caps['child_images'] == 1
        assert not any(name in caps for name in ('child_image_return', 'child_return_owner', 'child_recovery'))
        before = (await session.children.supervisor.control('queue', child, '{"action":"list"}'))['queue']
        snapshot = json.dumps(before, sort_keys=True).encode()
        pending = session.children.supervisor._children[child].agent.pending_inputs()
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
        after = (await session.children.supervisor.control('queue', child, '{"action":"list"}'))['queue']
        assert json.dumps(after, sort_keys=True).encode() == snapshot
        assert session.children.supervisor._children[child].agent.pending_inputs() == pending
        listed = await send({**packet, 'request_id': 'list', 'text': '{"action":"list"}'})
        assert listed['success'] and listed['data']['queue'] == before
        pong = await send({'type': 'ping', 'group_id': 'main'})
        assert pong['type'] == 'pong' and pong['child_controls'] == 1
        assert not session.agent.messages and len(contexts) == 1
        await session.children.supervisor.control('cancel', child)
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
        assert not session.children.supervisor.snapshot()['children'] and not session.agent.messages
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
