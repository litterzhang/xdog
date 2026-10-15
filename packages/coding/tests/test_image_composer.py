from __future__ import annotations

import asyncio
import base64
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, DoneEvent, ImageContent, Model, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment
from xdog.tui.keys import KeyEvent

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=")


def test_coding_sends_and_persists_real_typed_images_not_path_placeholders(tmp_path):
    contexts = []

    def stream_fn(model, context, options):
        contexts.append(context)
        message = AssistantMessage(content=(TextContent(text="answer"),))

        async def generate():
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)

    agent = Agent(stream_fn, config=AgentConfig(model="test/vision"))
    manager = SessionManager(tmp_path / "sessions")
    session = AgentSession(agent=agent, session_data=manager.create_session(model="test/vision", working_dir=tmp_path),
                           session_manager=manager, settings=SettingsManager(global_path=tmp_path / "none"),
                           tool_registry=None, bash=None, working_dir=tmp_path,
                           model_info=Model(id="test/vision", input=("text", "image")))
    session._maybe_compact = AsyncMock()
    ui = InteractiveMode(session)
    image = attachment(PNG, "example.png")
    try:
        ui._composer.restore((image,))
        ui._editor.set_text("inspect")
        ui._editor.handle_input(KeyEvent("enter"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and ui._worker_active:
            ui._poll()
            time.sleep(0.005)
        ui._poll()
        assert not ui._worker_active and contexts
        user = next(message for message in contexts[0].messages if isinstance(message, UserMessage))
        images = [part for part in user.content if isinstance(part, ImageContent)]
        assert len(images) == 1 and base64.b64decode(images[0].data) == PNG
        saved = manager.load_session(session.session_id)
        assert isinstance(saved.messages[0].content[1], ImageContent)
        assert not ui._composer.items
    finally:
        ui._request_exit()
        ui._session_events.close()


def test_model_capability_rejection_keeps_images_and_text_editable(tmp_path):
    session = MagicMock()
    session.working_dir = tmp_path
    session.validate_image_inputs.side_effect = ValueError("text-only model")
    ui = InteractiveMode(session)
    image = attachment(PNG, "example.png")
    ui._composer.restore((image,))
    ui._editor.set_text("preserve")
    ui._editor.handle_input(KeyEvent("enter"))
    assert ui._editor.get_text() == "preserve"
    assert tuple(ui._composer.items.values()) == (image,)
    assert not ui._worker_active
    session.agent.prompt.assert_not_called()
    ui._request_exit()


def test_claw_preserves_images_on_offline_rejection_uncertain_disconnect_and_no_replay():
    ui = ChatApp("/tmp/unused-images.sock")
    image = attachment(PNG, "example.png")
    ui._handle_response({"type": "pong", "attachment_transport": 1, "image_input": True, "session_id": "session"})
    ui._composer.restore((image,))
    ui._handle_submit("inspect")
    request = ui._send_queue.get_nowait()
    assert request["_images"] == (image,)
    ui._editor.set_text("live draft")
    ui._handle_response({"type": "internal_error", "message": "lost write"})
    assert tuple(ui._composer.items.values()) == (image,)
    assert "live draft" in ui._editor.get_text()
    ui._handle_response({"type": "_connection_opened", "generation": 1})
    ui._handle_response({"type": "pong", "attachment_transport": 1, "image_input": True, "session_id": "session"})
    assert ui._send_queue.empty()
    ui._request_exit()


async def test_identified_steering_edit_and_recovery_retain_image_parts():
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/vision"))
    image = ImageContent(data=base64.b64encode(PNG).decode())
    agent.queue_input("one", "original", "steer", (image,))
    assert agent.change_input("one", "edit", "edited")
    record = agent.pending_inputs()[0]
    assert record.images == (image,)
    message = agent._steering_queue[0]
    assert message.content[0].text == "edited" and message.content[1] == image
    assert agent.drain_inputs()[0].images == (image,)


def test_coding_later_queue_failure_recovers_current_images_not_first_turn(coding_session):
    import threading

    from xdog.coding.core.sessions.models import TurnResult
    ui = InteractiveMode(coding_session)
    first, second = attachment(PNG, "first.png"), attachment(PNG, "second.png")
    entered, release = threading.Event(), threading.Event()
    runtime = coding_session.enable_runtime(bound=True)
    async def execute(value):
        if value.instruction == 'first':
            entered.set()
            await asyncio.to_thread(release.wait)
            return TurnResult('succeeded', None)
        raise ValueError('second failed')
    runtime.controller._execute = execute
    try:
        ui._start_turn('first', images=(first,))
        assert entered.wait(3)
        ui._start_turn('second', images=(second,))
        release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and ui._worker_active:
            ui._poll()
            time.sleep(.005)
        ui._poll()
        assert tuple(ui._composer.items.values()) == (second,)
        assert not ui._local_images
        assert not ui._worker_active
    finally:
        release.set()
        ui._request_exit()
        ui._session_events.close()


@pytest.mark.parametrize("limits", [None, [], {"count": "four"}, {"bytes": True}, {"types": 123}])
def test_claw_rejected_dequeued_images_survive_malformed_limits(limits):
    ui = ChatApp("/tmp/unused-images.sock")
    image = attachment(PNG, "queued.png")
    try:
        ui._handle_response({"type": "pong", "attachment_transport": 1, "image_input": True,
                             "image_limits": limits})
        ui._pending_messages.append("queued")
        keys = ui._pending_ids.sync(1)
        ui._local_images[keys[0]] = (image,)
        ui._dispatch_next_pending()
        assert tuple(ui._composer.items.values()) == (image,)
        assert ui._editor.get_text() == "queued"
        assert ui._send_queue.empty()
    finally:
        ui._request_exit()


def test_claw_unavailable_explicit_owner_and_orphan_disconnect_restore_images():
    ui = ChatApp("/tmp/unused-images.sock")
    image = attachment(PNG, "steer.png")
    try:
        ui._handle_response({"type": "pong", "attachment_transport": 1, "image_input": True})
        ui._composer.restore((image,))
        ui._send_runtime_input("steer", "inspect")
        assert tuple(ui._composer.items.values()) == (image,)
        assert not ui._routed_images and not ui._routed_inputs
        ui._composer.take()
        ui._routed_images["orphan"] = (image,)
        ui._handle_response({"type": "internal_error", "message": "disconnected"})
        assert tuple(ui._composer.items.values()) == (image,)
        assert not ui._routed_images
    finally:
        ui._request_exit()


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_image_queue_edit_move_remove_preserves_attachment_identity(kind, tmp_path):
    session = MagicMock()
    session.working_dir = tmp_path
    ui = InteractiveMode(session) if kind == "coding" else ChatApp("/tmp/unused-images.sock")
    first, second = attachment(PNG, "first.png"), attachment(PNG, "second.png")
    try:
        if kind == "coding":
            ui._pending_messages.extend((("first", True), ("second", True)))
        else:
            ui._pending_messages.extend(("first", "second"))
        keys = ui._pending_ids.sync(2)
        ui._local_images.update({keys[0]: (first,), keys[1]: (second,)})
        ui._queue_command("edit q2 replacement")
        ui._queue_command("move q2 up")
        assert list(ui._pending_ids.ids) == ["q2", "q1"]
        assert ui._local_images["q2"] == (second,)
        ui._queue_command("remove q1")
        assert ui._local_images == {"q2": (second,)}
    finally:
        ui._request_exit()


def test_cli_initial_image_is_typed_and_print_mode_rejects_unknown_capability(tmp_path, capsys):
    from xdog.coding.cli.initial_message import build_initial_message
    from xdog.coding.modes.print_mode import run_print_mode

    path = tmp_path / "actual.png"
    path.write_bytes(PNG)
    images = []
    assert build_initial_message(files=(path,), images=images) is None
    assert len(images) == 1 and base64.b64decode(images[0].data) == PNG
    agent = Agent(lambda *args: pytest.fail("Rejected input must not invoke the provider"),
                  config=AgentConfig(model="test/text"))
    manager = SessionManager(tmp_path / "sessions")
    session = AgentSession(agent=agent, session_data=manager.create_session(model="test/text", working_dir=tmp_path),
                           session_manager=manager, settings=SettingsManager(global_path=tmp_path / "none"),
                           tool_registry=None, bash=None, working_dir=tmp_path)
    assert asyncio.run(run_print_mode(session, "", images=tuple(images))) == 1
    assert "image" in capsys.readouterr().err.lower()


def test_image_preview_and_late_clipboard_cannot_approve_or_submit(tmp_path, monkeypatch):
    import xdog.tui.tui as module
    from xdog.coding.core.permissions import PermissionRequest

    session = MagicMock()
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    image = attachment(PNG, "preview.png")
    monkeypatch.setattr(module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(module, "_terminal_height", lambda: 24)
    ui._tui._previous_width, ui._tui._previous_height = 80, 24
    try:
        ui._editor.set_text("live draft")
        ui._open_attachment_preview(image)
        panel = ui._attachment_panel
        ui._tui._dispatch_input(KeyEvent("enter"))
        assert not ui._worker_active
        ui._show_permission_request(PermissionRequest(id="approval", tool_name="bash", arguments={}, summary="cmd"))
        prompt = ui._permission_prompt
        ui._composer.handle({"epoch": ui._composer.epoch, "image": image, "text": "late clipboard"})
        assert ui._tui._focused is prompt
        session.permissions.resolve.assert_not_called()
        session.agent.prompt.assert_not_called()
        ui._tui._dispatch_input(KeyEvent("escape"))
        session.permissions.resolve.assert_called_once_with("approval", "deny")
        assert ui._tui._focused is panel
        assert "late clipboard" in ui._editor.get_text() and "live draft" in ui._editor.get_text()
        assert tuple(ui._composer.items.values()) == (image,)
    finally:
        ui._request_exit()
