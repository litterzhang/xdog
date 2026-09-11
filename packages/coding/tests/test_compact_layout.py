from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from xdog.coding.modes.interactive.components.footer import FooterComponent
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.coding.modes.interactive.run_status import RunStatus
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.components.bounded_details import BoundedDetails, DetailRecord
from xdog.tui.components.inline_layout import InlineLayout
from xdog.tui.components.text import Text
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component
from xdog.tui.utils import strip_ansi


@dataclass
class _BudgetEditor(Component):
    rows: int = 5
    budget: tuple[int, bool] | None = None

    def set_render_budget(self, max_rows: int, show_borders: bool = True) -> None:
        self.budget = (max_rows, show_borders)

    def render(self, width: int) -> list[str]:
        limit = self.budget[0] if self.budget is not None else self.rows
        return [f"editor-{index}" for index in range(min(self.rows, limit))]


def _plain(component: Component, width: int = 80) -> list[str]:
    return [strip_ansi(line) for line in component.render(width)]


def test_inline_layout_renders_full_transcript_and_bounded_tail() -> None:
    editor = _BudgetEditor()
    layout = InlineLayout(
        transcript=Text("history-1\nhistory-2", 0, 0),
        work=Text("work-1\nwork-2\nwork-3\nwork-4", 0, 0),
        status=Text("status", 0, 0),
        editor=editor,
        queue=Text("queue-1\nqueue-2", 0, 0),
        render_height=8,
    )

    lines = layout.render(80)

    assert [line.rstrip() for line in lines[:2]] == ["history-1", "history-2"]
    assert len(lines) <= 10  # full transcript plus an eight-row transient tail
    assert layout.transient_start == 2
    rendered = "\n".join(lines)
    assert "status" in rendered
    assert "queue-2" in rendered
    assert editor.budget is not None


def test_inline_layout_aux_priority_permission_then_details_then_queue() -> None:
    layout = InlineLayout(
        transcript=Text("history", 0, 0),
        status=Text("status", 0, 0),
        editor=_BudgetEditor(rows=1),
        permission=Text("permission", 0, 0),
        details=Text("details", 0, 0),
        queue=Text("queue", 0, 0),
        render_height=8,
    )

    assert "permission" in "\n".join(layout.render(80))
    layout.permission = None
    assert "details" in "\n".join(layout.render(80))
    layout.details = None
    assert "queue" in "\n".join(layout.render(80))


def test_bounded_details_keeps_frozen_records_and_supports_navigation() -> None:
    records = (
        DetailRecord(title="reasoning", body="one\ntwo\nthree\nfour", kind="reasoning"),
        DetailRecord(title="bash", body="alpha\nbeta", kind="tool"),
    )
    panel = BoundedDetails(lambda: records, max_rows=4)

    assert "bash" in "\n".join(_plain(panel))
    assert panel.handle_input(KeyEvent(key="left"))
    assert "reasoning" in "\n".join(_plain(panel))
    assert panel.handle_input(KeyEvent(key="pageup"))
    assert panel.handle_input(KeyEvent(key="pagedown"))
    assert records[0].body.endswith("four")


def test_footer_is_one_stable_width_bounded_activity_metadata_row() -> None:
    footer = FooterComponent(create_default_theme())
    footer.update(
        model="very-long-model-name",
        session_id="1234567890",
        thinking="high",
        permission_mode="ask",
        working_dir="/a/very/long/project/path",
        context_tokens=500,
        max_context=1000,
    )
    footer.set_activity("thinking... • 12s")

    first = _plain(footer, 42)
    footer.set_activity("thinking... • 12s")
    second = _plain(footer, 42)

    assert len(first) == 1
    assert first == second
    assert len(first[0]) <= 42
    assert "thinking" in first[0]


def _session() -> MagicMock:
    session = MagicMock()
    session.agent.subscribe.return_value = lambda: None
    session.agent.options.thinking = None
    session.permissions.mode = "ask"
    session.session_id = "session-id"
    session.model = "model"
    session.messages = []
    session.working_dir = "/workspace/project"
    session.context_limit = 1000
    return session


def test_permission_arrow_press_and_release_move_only_once() -> None:
    from xdog.coding.core.permissions import PermissionRequest
    from xdog.tui.stdin_buffer import KeyBytes

    mode = InteractiveMode(_session())
    mode._show_permission_request(PermissionRequest(
        id="request", tool_name="bash", arguments={}, summary="command",
    ))
    mode._tui._handle_frame(KeyBytes(b"\x1b[1;1:1B\x1b[1;1:3B"))
    assert "→ Allow for this session" in "\n".join(_plain(mode._layout, 120))
    mode._tui._handle_frame(KeyBytes(b"\x1b[1;1:1A\x1b[1;1:3A"))
    assert "→ Allow once" in "\n".join(_plain(mode._layout, 120))


def test_cancel_status_is_not_replaced_by_thinking_timer() -> None:
    mode = InteractiveMode(_session())
    mode._set_busy(True)
    mode._cancel_active_work()
    mode._poll()
    assert "cancelling" in _plain(mode._footer)[0]
    assert "thinking..." not in _plain(mode._footer)[0]


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_worker_exit_clears_thinking_timer(outcome: str) -> None:
    from unittest.mock import AsyncMock

    session = _session()
    async def stream():
        if outcome == "error":
            raise RuntimeError("upstream failed")
        if outcome == "cancel":
            raise asyncio.CancelledError
        if False:
            yield

    session.agent.prompt = AsyncMock(return_value=stream())
    session._maybe_compact = AsyncMock()
    mode = InteractiveMode(session)
    mode._worker_active = True
    mode._set_busy(True)
    mode._run_agent_turn("hello", 0)
    mode._poll()
    mode._poll()
    assert not mode._worker_active
    assert not mode._is_busy
    assert mode._busy_started is None
    assert "thinking..." not in _plain(mode._footer)[0]


def test_initial_banner_is_added_to_transcript_once() -> None:
    mode = InteractiveMode(_session())

    mode._update_header()
    mode._update_header()

    rendered = "\n".join(_plain(mode._chat_log))
    assert rendered.count("coding | model") == 1


def test_ctrl_o_opens_bounded_latest_details_without_rewriting_transcript() -> None:
    mode = InteractiveMode(_session())
    mode._chat_log.add_assistant("answer", thinking="private reasoning")
    tool = mode._chat_log.add_tool("bash", {"command": "pwd"})
    tool.set_result("/workspace\nRAW-TAIL")
    before = _plain(mode._chat_log)

    assert mode._handle_global_input(KeyEvent(key="o", ctrl=True)) == {"consume": True}

    assert _plain(mode._chat_log) == before
    rendered_details = "\n".join(_plain(mode._details_panel))
    assert "bash" in rendered_details
    assert "RAW-TAIL" in rendered_details
    assert mode._layout.details is mode._details_panel


def test_escape_closes_details_before_canceling_busy_turn() -> None:
    session = _session()
    mode = InteractiveMode(session)
    mode._chat_log.add_assistant("answer", thinking="reasoning")
    mode._run_status = RunStatus().start(0.0)
    mode._handle_global_input(KeyEvent(key="o", ctrl=True))

    result = mode._tui._dispatch_input(KeyEvent(key="escape"))

    assert result is True
    assert mode._layout.details is None
    session.cancel.assert_not_called()


def test_permission_preempts_open_details_in_single_aux_slot() -> None:
    from xdog.coding.core.permissions import PermissionRequest

    mode = InteractiveMode(_session())
    mode._chat_log.add_assistant("answer", thinking="reasoning")
    mode._handle_global_input(KeyEvent(key="o", ctrl=True))
    mode._show_permission_request(PermissionRequest(
        id="request",
        tool_name="bash",
        arguments={"command": "pwd"},
        summary="Run pwd",
    ))

    rendered = "\n".join(_plain(mode._layout))
    assert "Tool permission required" in rendered
    assert mode._layout.permission is mode._permission_prompt
    assert mode._layout._auxiliary() is mode._permission_prompt


def test_all_worker_events_carry_the_captured_turn_stamp() -> None:
    import asyncio

    mode = InteractiveMode(_session())
    mode._worker_generation = 3
    mode._active_stamp = type(mode._active_stamp)(3, 4)
    mode._worker_active = True

    async def finish(_message: str) -> None:
        return None

    mode._async_agent_turn = finish  # type: ignore[method-assign]
    asyncio.run(mode._async_agent_queue("first", 3))

    events = []
    while not mode._event_queue.empty():
        events.append(mode._event_queue.get_nowait())
    assert events
    assert all(event.get("stamp") == mode._active_stamp for event in events)


def test_delayed_old_error_does_not_restore_over_new_draft() -> None:
    mode = InteractiveMode(_session())
    mode._worker_generation = 2
    mode._active_stamp = type(mode._active_stamp)(2, 0)
    mode._editor.set_text("current draft")

    mode._handle_ui_event({
        "type": "error",
        "message": "old failure",
        "generation": 1,
        "restore": ("old queued",),
        "stamp": type(mode._active_stamp)(1, 0),
    })

    assert mode._editor.get_text() == "current draft"


def test_worker_error_never_mutates_editor_and_carries_restore_payload() -> None:
    mode = InteractiveMode(_session())
    mode._editor = MagicMock()
    mode._pending_messages.append(("queued draft", True))
    mode._worker_active = True

    async def fail(_message: str, _generation: int | None = None) -> None:
        raise RuntimeError("boom")

    mode._async_agent_queue = fail  # type: ignore[method-assign]
    mode._run_agent_turn("first", 7)

    mode._editor.get_text.assert_not_called()
    mode._editor.set_text.assert_not_called()
    events = []
    while not mode._event_queue.empty():
        events.append(mode._event_queue.get_nowait())
    error = next(event for event in events if event["type"] == "error")
    assert error["restore"] == ("queued draft",)
    assert error["stamp"].generation == 7


def test_permission_shows_all_options_and_moves_highlight() -> None:
    from xdog.coding.core.permissions import PermissionRequest

    mode = InteractiveMode(_session())
    mode._layout.set_height(24)
    mode._show_permission_request(PermissionRequest(
        id="request", tool_name="bash", arguments={}, summary="echo hello",
    ))
    for selected in ("Allow once", "Allow for this session", "Deny"):
        rows = _plain(mode._layout, 100)
        for option in ("Allow once", "Allow for this session", "Deny"):
            assert any(option in row for row in rows)
        assert sum(row.removeprefix("│ ").startswith("→ ") for row in rows) == 1
        assert any(row.removeprefix("│ ").startswith(f"→ {selected}") for row in rows)
        assert any("echo hello" in row for row in rows)
        assert len(rows[mode._layout.transient_start:]) <= 24
        mode._tui._dispatch_input(KeyEvent(key="down"))


def test_permission_keeps_selected_action_visible_and_summary_scrollable() -> None:
    from xdog.coding.core.permissions import PermissionRequest

    mode = InteractiveMode(_session())
    mode._editor.set_text("untouched draft")
    mode._layout.set_height(6)
    mode._show_permission_request(PermissionRequest(
        id="request", tool_name="bash", arguments={},
        summary="\n".join(f"command-{i}" for i in range(40)),
    ))
    prompt = mode._permission_prompt
    assert prompt is not None
    rows = _plain(mode._layout, 32)
    assert len(rows[mode._layout.transient_start:]) <= 6
    assert any("→ Allow once" in row for row in rows)
    assert prompt.handle_input(KeyEvent(key="pagedown"))
    assert _plain(mode._layout, 32) != rows
    prompt.handle_input(KeyEvent(key="down"))
    prompt.handle_input(KeyEvent(key="down"))
    assert "→ Deny" in "\n".join(_plain(mode._layout, 32))
    assert mode._editor.get_text() == "untouched draft"


def test_escape_in_permission_does_not_cancel_busy_turn() -> None:
    from xdog.coding.core.permissions import PermissionRequest

    mode = InteractiveMode(_session())
    mode._run_status = RunStatus().start(0.0)
    mode._show_permission_request(PermissionRequest(
        id="request", tool_name="bash", arguments={}, summary="command",
    ))
    assert mode._handle_global_input(KeyEvent(key="escape")) is None
    assert mode._permission_prompt is not None
    mode._permission_prompt.handle_input(KeyEvent(key="escape"))
    mode._session.permissions.resolve.assert_called_once_with("request", "deny")
    mode._session.cancel.assert_not_called()


def test_autocomplete_preempts_details_and_queue_without_losing_draft() -> None:
    mode = InteractiveMode(_session())
    mode._chat_log.add_assistant("answer", thinking="DETAIL-MARKER")
    mode._handle_global_input(KeyEvent(key="o", ctrl=True))
    mode._editor.set_text("/")
    rows = mode._layout.render(80)
    tail = "\n".join(rows[mode._layout.transient_start:])
    assert "DETAIL-MARKER" not in tail
    assert mode._handle_global_input(KeyEvent(key="escape")) is None
    mode._editor.handle_input(KeyEvent(key="escape"))
    assert mode._editor.get_text() == "/"
    assert "DETAIL-MARKER" in "\n".join(mode._layout.render(80))


def test_queue_preview_is_bounded_and_status_keeps_count() -> None:
    mode = InteractiveMode(_session())
    mode._pending_messages.extend((f"queued-{i}", True) for i in range(20))
    mode._update_message_queue()
    assert len(mode._message_queue_container.render(80)) <= 4
    assert "queued 20" in "\n".join(_plain(mode._footer))
    assert len(mode._pending_messages) == 20


def test_late_permission_callback_does_not_dismiss_successor_prompt() -> None:
    from xdog.coding.core.permissions import PermissionRequest

    mode = InteractiveMode(_session())
    mode._show_permission_request(PermissionRequest(id="old", tool_name="bash", arguments={}, summary="old"))
    old = mode._permission_prompt
    mode._show_permission_request(PermissionRequest(id="new", tool_name="bash", arguments={}, summary="new"))
    current = mode._permission_prompt
    assert old is not None
    old.handle_input(KeyEvent(key="enter"))
    assert mode._permission_prompt is current
    mode._session.permissions.resolve.assert_not_called()


def test_short_multiline_tool_result_is_compact_and_retained_in_details() -> None:
    mode = InteractiveMode(_session())
    tool = mode._chat_log.add_tool("bash", {})
    tool.set_result("\n".join([*("line" for _ in range(30)), "TAIL"]))
    assert len(tool.render(80)) <= 6
    assert "TAIL" not in "\n".join(tool.render(80))
    assert "TAIL" in tool.detail_body


async def test_delayed_permission_request_retains_originating_stamp() -> None:
    from unittest.mock import AsyncMock

    from xdog.coding.core.permissions import PermissionRequest
    mode = InteractiveMode(_session())
    async def empty():
        if False:
            yield None
    mode._session._maybe_compact = AsyncMock()
    mode._session.agent.prompt = AsyncMock(return_value=empty())
    origin = mode._active_stamp
    await mode._async_agent_turn("old")
    callback = mode._session.permissions.set_request_handler.call_args_list[0].args[0]
    mode._active_stamp = type(origin)(origin.generation + 1, origin.cancel_epoch)
    callback(PermissionRequest(id="old", tool_name="bash", arguments={}, summary="old"))
    event = mode._event_queue.get_nowait()
    assert event["stamp"] == origin
    mode._handle_ui_event(event)
    assert mode._permission_prompt is None


def test_streaming_tool_output_is_available_in_details() -> None:
    mode = InteractiveMode(_session())
    tool = mode._chat_log.add_tool("bash", {})
    tool.set_streaming("first\nactive output")
    assert mode._chat_log.detail_records()[-1].body == "first\nactive output"
    tool.set_streaming("\n".join(["x" * 100] * 20 + ["NEWEST-LINE"]))
    assert "NEWEST-LINE" in "\n".join(tool.render(80))
    assert "hidden" in "\n".join(tool.render(80))


def test_two_row_permission_uses_metadata_row_for_action() -> None:
    from xdog.coding.core.permissions import PermissionRequest
    mode = InteractiveMode(_session())
    mode._layout.set_height(2)
    mode._show_permission_request(PermissionRequest(id="p", tool_name="bash", arguments={}, summary="cmd"))
    rows = mode._layout.render(60)
    assert len(rows[mode._layout.transient_start:]) == 2
    assert any("Allow once" in row for row in rows)


def test_small_multiline_editor_suppresses_borders_before_text() -> None:
    mode = InteractiveMode(_session())
    mode._layout.set_height(4)
    mode._editor.set_text("first\nsecond\nthird")
    rows = _plain(mode._layout)
    assert any("first" in row for row in rows)
    assert any("second" in row for row in rows)
    assert any("third" in row for row in rows)


def test_idle_polls_emit_no_bytes_and_draft_update_emits_one_frame(monkeypatch) -> None:
    import io

    import xdog.tui.tui as module
    output = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", output)
    monkeypatch.setattr(module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(module, "_terminal_height", lambda: 24)
    mode = InteractiveMode(_session())
    mode._tui._do_render()
    output.seek(0)
    output.truncate()
    for _ in range(20):
        mode._poll()
        mode._tui._do_render()
    assert output.getvalue() == ""
    mode._editor.set_text("updated draft")
    mode._tui._do_render()
    rendered = output.getvalue()
    assert rendered.count("\x1b[?2026h") == 1
    assert rendered.count("\x1b[?2026l") == 1
    assert "updated draft" in rendered
    assert "coding |" not in rendered
