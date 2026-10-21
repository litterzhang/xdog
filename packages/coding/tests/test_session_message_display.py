"""Source-neutral inputs remain labelled, bounded and correlated in the transcript."""
import pytest
from xdog.agent.session_input import SessionInput
from xdog.coding.core.sessions.models import InputRecord, SessionEvent, TurnResult
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.utils import strip_ansi

from packages.coding.tests.batch_helpers import single_input_execute


def text(log):
    return "\n".join(strip_ansi(line) for line in log.render(100))


def value(identifier="r1", source="Eyes"):
    return SessionInput(identifier, "acp", source, "Review tests", ({"path": "src/a.py"},), {"delivery_id": "d1"})


def event(kind, data, identifier="r1", generation="g1"):
    return SessionEvent("session", generation, identifier, kind, data)


def test_external_display_labels_source_instruction_and_evidence_without_terminal_codes():
    from xdog.coding.modes.interactive.session_events import external_input_text

    result = external_input_text(value(source="Eyes\x1b[2J\nStatus: forged"))
    assert "External input" in result and "self-reported" in result and "untrusted" in result
    assert "Eyes" in result and "Review tests" in result and "src/a.py" in result
    assert "\x1b" not in result
    assert "\nStatus: forged" not in result  # Source cannot introduce a new authoritative status line.


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "discarded"])
def test_live_status_updates_one_component_and_late_queue_receipt_cannot_rewind_it(status):
    from xdog.coding.modes.interactive.session_events import SessionPresenter

    log = ChatLog(create_default_theme())
    presenter = SessionPresenter(log)
    queued = event("input_queued", InputRecord(value(), "branch", "g1", "queued"))
    presenter.apply(queued)
    presenter.apply(queued)
    presenter.apply(event("input_running", value()))
    presenter.apply(event("input_completed", TurnResult(status, None, "failure detail" if status == "failed" else "")))
    presenter.apply(queued)
    assert len(log.children) == 1
    assert text(log).count("External input") == 1
    assert f"Status: {status}" in text(log)
    assert "Status: queued" not in text(log)
    assert not any(getattr(child, "role", None) == "user" for child in log.children)


def test_peers_and_human_inputs_do_not_share_status_or_claim_human_identity():
    from xdog.coding.modes.interactive.session_events import SessionPresenter

    log = ChatLog(create_default_theme())
    presenter = SessionPresenter(log)
    presenter.apply(event("input_paused", InputRecord(value(), "branch", "g1", "paused")))
    second = value("r2", "second peer")
    presenter.apply(event("input_queued", InputRecord(second, "branch", "g1", "queued"), "r2"))
    human = SessionInput("human", "human", "coding", "Actual human input", (), {})
    presenter.apply(event("input_queued", InputRecord(human, "branch", "g1", "queued"), "human"))
    presenter.apply(event("input_completed", TurnResult("cancelled", None), "r2"))
    output = text(log)
    assert output.count("External input") == 2
    assert "Status: paused" in output and "Status: cancelled" in output
    assert "Actual human input" not in output  # Native composer owns human echo/history.


def test_evidence_preview_is_bounded_but_full_details_remain_inspectable():
    from xdog.coding.modes.interactive.session_events import SessionPresenter, external_input_text

    evidence = tuple({"path": f"file-{index}", "content": "x" * 2000} for index in range(100))
    item = SessionInput("large", "acp", "watcher", "Review changes", evidence, {})
    preview = external_input_text(item)
    assert len(preview) < 8192
    assert "100 evidence items" in preview and "/details" in preview
    log = ChatLog(create_default_theme())
    SessionPresenter(log).apply(event("input_queued", InputRecord(item, "branch", "g1", "queued"), "large"))
    assert "file-99" in log.detail_records()[0].body


def test_old_generation_cannot_complete_a_new_request_with_same_display_key():
    from xdog.coding.modes.interactive.session_events import SessionPresenter

    log = ChatLog(create_default_theme())
    presenter = SessionPresenter(log)
    presenter.apply(event("input_queued", InputRecord(value(), "branch", "g2", "queued"), generation="g2"))
    presenter.apply(event("input_completed", TurnResult("succeeded", None), generation="g1"))
    assert "Status: queued" in text(log) and "Status: succeeded" not in text(log)


def test_native_duplicate_stream_and_late_generation_do_not_repeat_tools_or_completion(coding_session, monkeypatch):
    from xdog.agent import AgentToolResult, ToolExecutionEndEvent, ToolExecutionStartEvent
    from xdog.ai.types import TextContent
    from xdog.coding.modes.interactive.components.tool_execution import ToolExecutionComponent
    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    ui = InteractiveMode(coding_session)
    ui._session_events.start()
    controller = coding_session.runtime.controller
    observed = []
    controller.subscribe(observed.append)
    # Frames intentionally yield after a bounded batch (including an 8ms time
    # budget). Force one event per frame to cover that contract deterministically
    # instead of assuming the host always renders a whole turn in one _poll().
    drain = ui._event_queue.drain
    monkeypatch.setattr(ui._event_queue, "drain", lambda handler: drain(handler, max_events=1))

    def poll_published():
        # The submitted result fences publication of its stream and completion.
        # Every frame must consume at least one snapshot; no sleeps, larger
        # timeouts, dropped assertions or production budget changes are needed.
        for _ in range(ui._event_queue.qsize() + 1):
            ui._poll()
            if ui._event_queue.empty():
                return
        raise AssertionError("Published UI events did not drain across bounded frames")

    async def execute(item):
        job = controller._jobs[item.id]
        started = ToolExecutionStartEvent(tool_call_id="tool", tool_name="bash", args={"command": "pwd"}, sequence=1)
        controller._emit("agent_event", job, started)
        controller._emit("agent_event", job, started)
        controller._emit(
            "agent_event",
            job,
            ToolExecutionEndEvent(
                tool_call_id="tool",
                tool_name="bash",
                sequence=2,
                result=AgentToolResult(content=(TextContent(text="tool result"),)),
            ),
        )
        return TurnResult("failed", None, "provider failed after tool")

    controller.max_messages_per_turn = 1
    controller._execute_batch = single_input_execute(execute)
    try:
        controller.human_input()
        controller.submit(value(), peer_id="acp").result.result(timeout=3)
        poll_published()
        tools = [child for child in ui._chat_log.children if isinstance(child, ToolExecutionComponent)]
        assert len(tools) == 1
        assert tools[0]._state == "success"
        size = len(ui._chat_log.children)
        ui._session_events.receive(next(item for item in observed if item.kind == "input_completed"))
        stale = event(
            "input_queued",
            InputRecord(value("old", "stale source"), "branch", "old", "queued"),
            identifier="old",
            generation="old",
        )
        ui._session_events.receive(stale)
        poll_published()
        assert len(ui._chat_log.children) == size
        assert "stale source" not in text(ui._chat_log)
    finally:
        ui._request_exit()
        ui._session_events.close()


def test_coding_uses_shared_user_spacing_and_role_markers_for_live_and_replayed_messages():
    log = ChatLog(create_default_theme())
    log.add_user("user input")
    assert [strip_ansi(row).rstrip() for row in log.render(40)] == ["", " › user input", ""]
    log.start_assistant("", "run")
    log.update_assistant("live reply", "run")
    log.finalize_assistant("final reply", "run")
    log.add_user("next input")
    log.add_assistant("replayed reply")
    assert [strip_ansi(row).rstrip() for row in log.render(40)] == [
        "", " › user input", "", "", " • final reply", "", "", " › next input", "", "", " • replayed reply", "",
    ]
    assert [record.body for record in log.transcript_records()] == [
        "user input", "final reply", "next input", "replayed reply",
    ]
