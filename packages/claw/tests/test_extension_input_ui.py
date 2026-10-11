from __future__ import annotations

import asyncio
import base64
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, ImageContent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.types import SystemInput, SystemInputKind
from xdog.tui.attachments import attachment

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=")


@pytest.mark.parametrize("text", ["user prompt", "hook-reject", "hook-fail", "hook-bad", "hook-handled"])
async def test_live_claw_input_hook_canonical_history_or_restore_without_raw_fallback(tmp_path, text):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    contexts = []

    def stream(model, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="INPUT-MODEL-DONE"),)))

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "input.sock"),
        pid_file=str(tmp_path / "input.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        extensions=("inputter",),
        extension_dir=str(root),
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))

    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)

    try:
        await wait(lambda: ui._connection_ready)
        # Explicit controlled peer metadata avoids a hosted model catalog lookup.
        ui._state["image_input"] = True
        ui._state["image_limits"] = {}
        ui._attachment_transport = True
        runtime = server._orchestrator._runtimes["main"]
        runtime.validate_images = lambda images: None
        image = attachment(PNG, "keep.png")
        ui._composer.items[image.id] = image
        ui._handle_submit(text)
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        if text == "user prompt":
            assert len(contexts) == 1
            user = contexts[0].messages[-1]
            assert user.content[0].text == "INPUT-HOOK-user prompt"
            assert any(isinstance(part, ImageContent) for part in user.content)
            record = next(record for record in ui._chat_log.transcript_records() if record.role == "user")
            assert record.body.startswith("INPUT-HOOK-user prompt")
            saved = runtime.transcript_store.load_transcript(runtime.get_new_session_id())
            assert saved[0]["content"][0]["text"] == "INPUT-HOOK-user prompt"
            assert not ui._composer.items
        elif text == "hook-handled":
            assert not contexts and not runtime._active_session.agent.state.messages
            assert not runtime._active_session.agent.state.error
            assert ui._composer.items[image.id].data == PNG and text not in ui._editor.get_text()
            assert "INPUT-HANDLER-FEEDBACK" in "\n".join(ui._chat_log.render(120))
        else:
            assert not contexts and not runtime._active_session.agent.state.messages
            assert text in ui._editor.get_text() and ui._composer.items[image.id].data == PNG
        # Scheduler/goal/system inputs are intentionally not run through human-input interception.
        if text == "user prompt":
            session = runtime._active_session
            await session.run_turn(SystemInput(content="system work", kind=SystemInputKind.GOAL_RUNNER))
            assert contexts[-1].messages[-1].content[0].text == "system work"
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize("handled", [False, True])
async def test_claw_child_input_hook_uses_child_runtime_once_and_keeps_parent_context_private(tmp_path, handled):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    contexts = []

    def stream(model, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="child done"),)))

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "child-input.sock"),
        pid_file=str(tmp_path / "child-input.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        extensions=("inputter",),
        extension_dir=str(root),
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    try:
        session = server._orchestrator._runtimes["main"].get_or_create_session()
        child = await session.children.supervisor.start(
            "hook-handled" if handled else "child input", access="read-only"
        )
        result = await session.children.supervisor.control("wait", child)
        assert result["status"] == "completed"
        if handled:
            assert not contexts and result["calls"] == 0 and not result["messages"]
            assert result["input_feedback"] == ["INPUT-HANDLER-FEEDBACK (not a model answer)"]
        else:
            assert len(contexts) == 1 and contexts[0].messages[-1].content[0].text == "INPUT-HOOK-child input"
        assert not session.agent.state.messages
    finally:
        await server.stop()


@pytest.mark.parametrize("handled", [False, True])
async def test_live_claw_followup_hook_preserves_queue_image_and_consumption_identity(tmp_path, handled):
    from xdog.ai.types import DoneEvent

    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    entered, release = asyncio.Event(), asyncio.Event()
    contexts = []

    def stream(model, context, options):
        contexts.append(context)
        message = AssistantMessage(content=(TextContent(text="done"),))

        async def generate():
            if len(contexts) == 1:
                entered.set()
                await release.wait()
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "follow-input.sock"),
        pid_file=str(tmp_path / "follow-input.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        extensions=("inputter",),
        extension_dir=str(root),
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))

    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)

    try:
        await wait(lambda: ui._connection_ready)
        runtime = server._orchestrator._runtimes["main"]
        runtime.validate_images = lambda images: None
        ui._state["image_input"], ui._state["image_limits"], ui._attachment_transport = True, {}, True
        ui._handle_submit("initial")
        await asyncio.wait_for(entered.wait(), 3)
        ui._composer.items["queue-image"] = attachment(PNG, "queued.png")
        ui._send_runtime_input("follow_up", "hook-handled" if handled else "queued text")
        await wait(lambda: bool(ui._routed_inputs) and not ui._input_requests)
        release.set()
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        if handled:
            assert len(contexts) == 1 and ui._composer.items
            assert not ui._routed_inputs and not ui._transformed_inputs
            assert "INPUT-HANDLER-FEEDBACK" in "\n".join(ui._chat_log.render(120))
        else:
            assert len(contexts) == 2 and contexts[-1].messages[-1].content[0].text == "INPUT-HOOK-queued text"
            assert any(isinstance(part, ImageContent) for part in contexts[-1].messages[-1].content)
            assert any(
                record.role == "user" and record.body.startswith("INPUT-HOOK-queued text")
                for record in ui._chat_log.transcript_records()
            )
            assert not ui._routed_inputs and not ui._transformed_inputs and not ui._composer.items
    finally:
        release.set()
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
