"""ACP inputs are descriptive display data, never composer text or approvals."""
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.tui.utils import strip_ansi


def output(ui):
    return "\n".join(strip_ansi(line) for line in ui._chat_log.render(100))


def test_live_external_completion_is_scoped_deduplicated_and_preserves_composer():
    ui = ChatApp("/tmp/not-connected")
    ui._gateway_session_id = "current-session"
    ui._editor.set_text("unsent human draft")
    event = {"type": "acp_input_completed", "group_id": "main", "session_id": "current-session",
             "message_id": "event-1", "source": "Eyes", "instruction": "Review the changed source",
             "response": "Reviewed source", "error": "", "status": "succeeded"}
    ui._handle_response({**event, "group_id": "other"})
    assert "Reviewed source" not in output(ui)
    ui._handle_response(event)
    ui._handle_response(event)
    text = output(ui)
    assert text.count("Reviewed source") == 1 and text.count("Review the changed source") == 1
    assert "Eyes" in text and ui._editor.get_text() == "unsent human draft"
    ui._handle_response({**event, "session_id": "previous-session", "message_id": "event-2"})
    assert output(ui).count("Reviewed source") == 1


def test_replayed_external_input_does_not_enter_human_editor_history():
    ui = ChatApp("/tmp/not-connected")
    ui._replay_history_entries([{"role": "user", "channel": "acp", "message_id": "event-1",
                                 "content": "[External: Eyes]\nInspect the change"}])
    assert "Inspect the change" in output(ui)
    assert "Inspect the change" not in str(ui._editor._history)


def test_first_external_turn_displays_without_claiming_a_tui_session_owner():
    ui = ChatApp("/tmp/not-connected")
    assert not ui._gateway_session_id
    ui._editor.set_text("keep draft")
    ui._handle_response({"type": "acp_input_completed", "group_id": "main", "session_id": "new-session",
                         "message_id": "first", "source": "Eyes", "instruction": "First observation",
                         "response": "First response", "error": "", "status": "succeeded"})
    assert "First observation" in output(ui) and "First response" in output(ui)
    assert not ui._gateway_session_id and ui._editor.get_text() == "keep draft"
