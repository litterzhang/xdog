from __future__ import annotations

import asyncio
import base64
import json
import shutil
import time
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, ImageContent, Model, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=")


@pytest.mark.parametrize("text", ["user prompt", "hook-reject", "hook-fail", "hook-bad", "hook-handled"])
def test_live_coding_pre_admission_input_transform_or_refusal_restores_original_attachments(tmp_path, text):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["inputter"], "extension_dir": str(root)}))
    calls = []

    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="INPUT-MODEL-DONE"),)))

    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=store.create_session(working_dir=tmp_path, model="test/model"),
        session_manager=store,
        settings=SettingsManager(global_path=settings),
        working_dir=tmp_path,
        tool_registry=None,
        bash=None,
        permissions=PermissionManager("ask"),
        model_info=Model(id="test/model", input=("text", "image")),
    )
    ui = InteractiveMode(session)

    def wait(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("Input hook did not settle")

    try:
        image = attachment(PNG, "keep.png")
        ui._composer.items[image.id] = image
        ui._handle_submit(text)
        wait(lambda: not ui._worker_active)
        if text == "user prompt":
            assert len(calls) == 1
            message = calls[0].messages[-1]
            assert message.content[0].text == "INPUT-HOOK-user prompt"
            assert any(isinstance(part, ImageContent) for part in message.content)
            rows = ui._chat_log.transcript_records()
            assert next(record.body for record in rows if record.role == "user").startswith("INPUT-HOOK-user prompt")
            saved = store.load_session(session.session_id)
            assert saved.messages[0].content[0].text == "INPUT-HOOK-user prompt"
            assert not ui._composer.items
        elif text == "hook-handled":
            assert not calls and not session.messages and not session.agent.state.error
            assert ui._composer.items[image.id].data == PNG
            assert text not in ui._editor.get_text()  # Handled is logical consumption, not rejection/replay.
            assert "INPUT-HANDLER-FEEDBACK" in "\n".join(ui._chat_log.render(120))
        else:
            assert not calls and not session.messages
            assert text in ui._editor.get_text() and ui._composer.items[image.id].data == PNG
            assert "no automatic replay" in "\n".join(ui._chat_log.render(120))
        assert session.permissions.mode == "ask"
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()


@pytest.mark.parametrize("handled", [False, True])
async def test_child_owned_input_hook_filters_task_once_without_parent_history_mutation(tmp_path, handled):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"extensions": ["inputter"], "extension_dir": str(root)}))
    contexts = []

    def stream(model, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="child done"),)))

    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=store.create_session(working_dir=tmp_path),
        session_manager=store,
        settings=SettingsManager(global_path=path),
        working_dir=tmp_path,
        tool_registry=None,
        bash=None,
    )
    try:
        child = await session.children.supervisor.start(
            "hook-handled" if handled else "child input", access="read-only"
        )
        result = await session.children.supervisor.control("wait", child)
        assert result["status"] == "completed"
        if handled:
            from xdog.tui.child_browser import child_records

            assert not contexts and result["calls"] == 0 and not result["messages"]
            assert result["input_feedback"] == ["INPUT-HANDLER-FEEDBACK (not a model answer)"]
            assert any(
                record.role == "system" and "INPUT-HANDLER-FEEDBACK" in record.body for record in child_records(result)
            )
        else:
            assert len(contexts) == 1 and contexts[0].messages[-1].content[0].text == "INPUT-HOOK-child input"
        assert not session.agent.state.messages
    finally:
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()


@pytest.mark.parametrize("handled", [False, True])
def test_live_coding_followup_hook_preserves_identified_image_and_shows_canonical_consumed_text(tmp_path, handled):
    import threading

    from xdog.ai.types import DoneEvent

    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["inputter"], "extension_dir": str(root)}))
    entered, release = threading.Event(), threading.Event()
    contexts = []

    def stream(model, context, options):
        contexts.append(context)
        message = AssistantMessage(content=(TextContent(text="done"),))

        async def generate():
            if len(contexts) == 1:
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(0.005)
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)

    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=store.create_session(working_dir=tmp_path),
        session_manager=store,
        settings=SettingsManager(global_path=settings),
        working_dir=tmp_path,
        tool_registry=None,
        bash=None,
        model_info=Model(id="test/model", input=("text", "image")),
    )
    ui = InteractiveMode(session)

    def wait(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("Follow-up hook did not settle")

    try:
        ui._handle_submit("initial")
        assert entered.wait(3)
        image = attachment(PNG, "queued.png")
        ui._composer.items[image.id] = image
        ui._send_runtime_input("follow_up", "hook-handled" if handled else "queued text")
        wait(lambda: bool(ui._routed_inputs))
        release.set()
        wait(lambda: not ui._worker_active)
        if handled:
            assert len(contexts) == 1 and ui._composer.items[image.id].data == PNG
            assert not ui._routed_inputs and not ui._transformed_inputs
            assert "INPUT-HANDLER-FEEDBACK" in "\n".join(ui._chat_log.render(120))
        else:
            assert len(contexts) == 2 and contexts[-1].messages[-1].content[0].text == "INPUT-HOOK-queued text"
            assert any(isinstance(part, ImageContent) for part in contexts[-1].messages[-1].content)
            user_records = [record.body for record in ui._chat_log.transcript_records() if record.role == "user"]
            assert any(body.startswith("INPUT-HOOK-queued text") and "Images: 1" in body for body in user_records)
            assert not ui._routed_inputs and not ui._transformed_inputs and not ui._composer.items
    finally:
        release.set()
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()


def test_rejected_local_turn_uses_dispatch_owned_images_not_an_outdated_ui_active_batch(tmp_path):
    from unittest.mock import MagicMock

    from xdog.agent import InputRejectedEvent
    from xdog.ai.types import UserMessage

    session = MagicMock()
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    try:
        wrong, owned = attachment(PNG, "old.png"), attachment(PNG, "current.png")
        ui._active_images = (wrong,)
        ui._dispatch_images = (owned,)
        ui._dispatch_agent_event(InputRejectedEvent(UserMessage(content="current text"), "prompt"))
        ui._event_queue.drain(ui._handle_ui_event)
        assert owned.id in ui._composer.items and wrong.id not in ui._composer.items
        assert "current text" in ui._editor.get_text()
    finally:
        ui._request_exit()


@pytest.mark.parametrize("mode", ["print", "print-json", "rpc"])
@pytest.mark.parametrize("handled", [False, True])
async def test_headless_input_refusal_is_visible_not_empty_success_or_original_fallback(
    tmp_path, capsys, mode, handled
):
    from xdog.coding.modes.print_mode import run_print_mode
    from xdog.coding.modes.rpc.rpc_mode import _handle_prompt

    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"extensions": ["inputter"], "extension_dir": str(root)}))
    calls = []
    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(
        agent=Agent(
            lambda *args: calls.append(args) or EventStream.empty(AssistantMessage()),
            config=AgentConfig(model="test/model"),
        ),
        session_data=store.create_session(working_dir=tmp_path),
        session_manager=store,
        settings=SettingsManager(global_path=path),
        working_dir=tmp_path,
        tool_registry=None,
        bash=None,
    )
    try:
        if mode in {"print", "print-json"}:
            assert await run_print_mode(
                session,
                "hook-handled" if handled else "hook-reject",
                output_format="json" if mode == "print-json" else "text",
            ) == (0 if handled else 1)
            output = capsys.readouterr()
            assert "INPUT-HANDLER-FEEDBACK" in output.out if handled else "rejected input" in output.err
            if handled and mode == "print-json":
                payload = json.loads(output.out)
                assert (
                    payload["role"] == "system" and payload["type"] == "input_handled" and payload["assistant"] is None
                )

        else:
            events = []
            await _handle_prompt(session, "hook-handled" if handled else "hook-reject", events.append)
            if handled:
                assert any(
                    event["type"] == "input_handled" and "INPUT-HANDLER-FEEDBACK" in event["feedback"]
                    for event in events
                )
                assert events[-1]["type"] == "message_end" and events[-1]["input_handled"]
            else:
                assert any(event["type"] == "error" and "rejected input" in event["message"] for event in events)
                assert not any(event["type"] == "message_end" for event in events)
        assert not calls and not session.messages
    finally:
        await session.extensions.aclose()
        session.dispose()
