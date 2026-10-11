"""Model the terminal cells we actually write, not only escape substrings."""
from __future__ import annotations

import re

from xdog.tui.main_buffer_renderer import MainBufferRenderer


def test_resize_without_editor_cursor_reanchors_shortened_panel() -> None:
    renderer = MainBufferRenderer()
    renderer.render([f"old-{i}" for i in range(20)], 80, 24, transient_start=10)
    renderer.reanchor(5)
    output = renderer.render([f"new-{i}" for i in range(12)], 36, 6, transient_start=8)
    assert "new-11" in output
    assert renderer.state.origin + len(renderer.state.lines) - 1 == 5

class Screen:
    """Small VT subset for deterministic main-buffer cursor/scroll tests."""

    def __init__(self, height: int = 8, width: int = 40, row: int = 2) -> None:
        self.height, self.width = height, width
        self.rows = [""] * height
        self.rows[0:2] = ["shell before 1", "shell before 2"]
        self.history: list[str] = []
        self.row, self.col = row, 0

    def feed(self, data: str) -> None:
        while data:
            match = re.match(r"\x1b\[([?0-9;]*)([A-Za-z])", data)
            if match:
                params, final = match.groups()
                count = int(params) if params.isdigit() else 1
                if final == "A":
                    self.row = max(0, self.row - count)
                elif final == "B":
                    self.row = min(self.height - 1, self.row + count)
                elif final == "G":
                    self.col = count - 1
                elif final == "K":
                    self.rows[self.row] = ""
                elif final == "J":
                    raise AssertionError("screen/scrollback erase is not allowed")
                elif final in ("H", "f"):
                    raise AssertionError("origin must not be assumed to be row zero")
                data = data[match.end():]
                continue
            char, data = data[0], data[1:]
            if char == "\r":
                self.col = 0
            elif char == "\n":
                if self.row == self.height - 1:
                    self.history.append(self.rows.pop(0))
                    self.rows.append("")
                else:
                    self.row += 1
            else:
                line = self.rows[self.row].ljust(self.col)
                self.rows[self.row] = (line[:self.col] + char + line[self.col + 1:])[:self.width]
                self.col = min(self.width - 1, self.col + 1)


def test_initial_render_and_shrink_preserve_preexisting_shell() -> None:
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen()
    screen.feed(renderer.render(["chat", "status", "> draft", "choices"], 40, 8, (2, 3), 1))
    assert screen.rows[:6] == ["shell before 1", "shell before 2", "chat", "status", "> draft", "choices"]
    assert (screen.row, screen.col) == (4, 3)
    screen.feed(renderer.render(["chat", "> draft"], 40, 8, (1, 4), 1))
    assert screen.rows[:6] == ["shell before 1", "shell before 2", "chat", "> draft", "", ""]
    assert not screen.history
    assert (screen.row, screen.col) == (3, 4)


def test_growing_transcript_does_not_commit_old_composer_to_scrollback() -> None:
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen(height=6)
    screen.feed(renderer.render(["chat-0", "status", "> old draft"], 40, 6, (2, 2), 1))
    transcript = [f"chat-{i}" for i in range(9)]
    screen.feed(renderer.render(transcript + ["status", "> new draft"], 40, 6, (10, 4), 9))
    assert screen.rows == transcript[-4:] + ["status", "> new draft"]
    assert "> old draft" not in screen.history
    assert "status" not in screen.history
    assert screen.history[:2] == ["shell before 1", "shell before 2"]
    assert (screen.row, screen.col) == (5, 4)


def test_cursor_only_frame_moves_without_rewriting_content() -> None:
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen()
    lines = ["chat", "> draft", "bottom"]
    screen.feed(renderer.render(lines, 40, 8, (1, 2), 1))
    update = renderer.render(lines, 40, 8, (1, 6), 1)
    assert "draft" not in update
    screen.feed(update)
    assert (screen.row, screen.col) == (3, 6)


def test_offscreen_history_change_is_not_replayed() -> None:
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen(height=6)
    lines = [f"chat-{i}" for i in range(10)] + ["> draft"]
    screen.feed(renderer.render(lines, 40, 6, (10, 2), 10))
    history = list(screen.history)
    update = renderer.render(["changed"] + lines[1:], 40, 6, (10, 2), 10)
    screen.feed(update)
    assert "changed" not in update
    assert screen.history == history


def test_transient_growth_then_shrink_keeps_input_once() -> None:
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen(height=6)
    screen.feed(renderer.render(["chat", "status", "> draft"], 40, 6, (2, 2), 1))
    screen.feed(renderer.render(["chat", "status", "> draft", "option-a", "option-b"], 40, 6, (2, 2), 1))
    screen.feed(renderer.render(["chat", "status", "> draft"], 40, 6, (2, 2), 1))
    assert screen.rows.count("> draft") == 1
    assert "> draft" not in screen.history
    assert "option-a" not in screen.rows
    assert "option-b" not in screen.rows


def test_resize_uses_reported_cursor_and_does_not_assume_row_zero() -> None:
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen(height=8)
    lines = ["chat", "status", "> draft"]
    screen.feed(renderer.render(lines, 40, 8, (2, 2), 1))
    # A terminal growing downward keeps its cursor at the same physical row.
    screen.rows.extend([""] * 4)
    screen.height = 12
    renderer.reanchor(screen.row, screen.col)
    screen.feed(renderer.render(lines, 40, 12, (2, 2), 1))
    assert screen.rows[:5] == ["shell before 1", "shell before 2", *lines]
    assert (screen.row, screen.col) == (4, 2)
    assert not screen.history
    assert renderer.render(lines, 40, 12, (2, 2), 1) == ""


def test_exact_queue_permission_details_resize_and_history_sequence() -> None:
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen(height=8)
    screen.feed(renderer.render(["chat-0", "status", "> draft", "queued"], 40, 8, (2, 7), 1))
    assert screen.rows == ["shell before 1", "shell before 2", "chat-0", "status", "> draft", "queued", "", ""]
    assert (screen.row, screen.col) == (4, 7)
    screen.feed(renderer.render(["chat-0", "status", "> draft", "Allow", "Deny"], 40, 8, (2, 7), 1))
    assert screen.rows[2:] == ["chat-0", "status", "> draft", "Allow", "Deny", ""]
    transcript = [f"chat-{i}" for i in range(8)]
    lines = transcript + ["status", "> draft", "detail"]
    screen.feed(renderer.render(lines, 40, 8, (9, 7), 8))
    assert screen.rows == transcript[3:] + ["status", "> draft", "detail"]
    assert screen.history == ["shell before 1", "shell before 2", *transcript[:3]]
    # Model a bottom-anchored terminal shrink and use its reported cursor.
    screen.history.extend(screen.rows[:2])
    screen.rows = screen.rows[2:]
    screen.height, screen.width, screen.row = 6, 30, screen.row - 2
    renderer.reanchor(screen.row, screen.col)
    screen.feed(renderer.render(lines, 30, 6, (9, 7), 8))
    assert screen.rows == transcript[5:] + ["status", "> draft", "detail"]
    assert (screen.row, screen.col) == (4, 7)
    screen.feed(renderer.render(lines[:-1], 30, 6, (9, 2), 8))
    assert screen.rows == transcript[5:] + ["status", "> draft", ""]
    assert (screen.row, screen.col) == (4, 2)
    assert screen.history == ["shell before 1", "shell before 2", *transcript[:5]]


def test_streaming_and_final_answer_leave_no_status_in_history():
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen(height=10, width=60)
    for output in ("a very long old output line", "short", "latest"):
        transcript = ["bash · Run Python script", output, ""]
        screen.feed(renderer.render(transcript + ["running bash", "> draft"], 60, 10,
                                    (len(transcript) + 1, 7), len(transcript)))
    transcript = ["bash · success · Run Python script", "Verified", ""] + [f"answer-{i}" for i in range(20)]
    screen.feed(renderer.render(transcript + ["ready", "> draft"], 60, 10,
                                (len(transcript) + 1, 7), len(transcript)))
    assert screen.rows[-2:] == ["ready", "> draft"]
    assert all("running bash" not in line for line in screen.history + screen.rows)
    assert all("old output" not in line for line in screen.history + screen.rows)


def test_cursor_is_visible_only_for_an_on_screen_editor_marker() -> None:
    renderer = MainBufferRenderer(origin_row=2)
    lines = ["chat", "> draft"]
    show = renderer.render(lines, 40, 8, (1, 3), 1)
    assert "\x1b[?25h" in show
    assert renderer.state.cursor_visible
    assert renderer.render(lines, 40, 8, (1, 3), 1) == ""

    hide = renderer.render(lines, 40, 8, transient_start=1)
    assert "\x1b[?25l" in hide
    assert "\x1b[?25h" not in hide
    assert not renderer.state.cursor_visible
    assert renderer.render(lines, 40, 8, transient_start=1) == ""

    show_again = renderer.render(lines, 40, 8, (1, 0), 1)
    assert "\x1b[?25h" in show_again
    assert "draft" not in show_again  # Visibility changes do not rewrite content.


def test_offscreen_cursor_marker_keeps_hardware_cursor_hidden() -> None:
    renderer = MainBufferRenderer()
    output = renderer.render([f"line-{i}" for i in range(8)], 40, 3, (0, 2))
    assert "\x1b[?25h" not in output
    assert not renderer.state.cursor_visible


def test_repaint_hides_cursor_until_positioned_inside_synchronized_frame() -> None:
    renderer = MainBufferRenderer()
    lines = ["chat", "> draft"]
    renderer.render(lines, 40, 8, (1, 3), 1)
    output = renderer.render(lines, 40, 8, (1, 3), 1, force=True)
    assert output.startswith("\x1b[?2026h\x1b[?25l")
    assert output.endswith("\x1b[?25h\x1b[?2026l")
    assert output.index("\x1b[?25l") < output.index("draft") < output.index("\x1b[?25h")


def test_overlay_never_scrolls_dialog_text_into_native_history(monkeypatch) -> None:
    import io

    import xdog.tui.tui as module
    from xdog.tui.components.text import Text
    from xdog.tui.tui import TUI, Component, OverlayOptions

    class Conversation(Component):
        def __init__(self):
            self.transcript = [f"chat-{i}" for i in range(6)]
            self.transient_start = len(self.transcript)

        def render(self, width):
            self.transient_start = len(self.transcript)
            return self.transcript + ["status", "> draft"]

    output = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", output)
    monkeypatch.setattr(module, "_terminal_width", lambda: 40)
    monkeypatch.setattr(module, "_terminal_height", lambda: 6)
    screen = Screen(height=6)
    tui = TUI()
    tui._painter = MainBufferRenderer(origin_row=2)
    conversation = Conversation()
    tui.add_child(conversation)

    def paint():
        output.seek(0)
        output.truncate(0)
        tui._do_render()
        screen.feed(output.getvalue())

    paint()
    handle = tui.show_overlay(Text("MODAL", 0, 0), OverlayOptions(width=10, anchor="top-left"))
    paint()
    assert "MODAL" in "\n".join(screen.rows)
    conversation.transcript.extend(f"chat-{i}" for i in range(6, 12))
    paint()
    assert "MODAL" not in "\n".join(screen.history)
    handle.hide()
    paint()
    assert screen.rows == conversation.transcript[-4:] + ["status", "> draft"]
    assert "MODAL" not in "\n".join(screen.history)


def test_row_styles_do_not_bleed_into_following_rows_or_shell() -> None:
    renderer = MainBufferRenderer()
    output = renderer.render(["\x1b[31mcolored", "plain"], 40, 8)
    assert "\x1b[31mcolored\x1b[0m\r" in output
    assert "\x1b[0m\x1b[2Kplain\x1b[0m\r" in output
    shrink = renderer.render(["\x1b[31mcolored"], 40, 8)
    assert "\x1b[0m\x1b[2K" in shrink


def test_clearing_scrolled_transcript_reanchors_editor_without_erasing_history() -> None:
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen(height=6)
    transcript = [f"chat-{i}" for i in range(20)]
    screen.feed(renderer.render(transcript + ["status", "> draft"], 40, 6, (21, 7), 20))
    history = list(screen.history)
    update = renderer.render(["cleared", "status", "> draft"], 40, 6, (2, 7), 1)
    screen.feed(update)
    assert screen.rows == ["cleared", "status", "> draft", "", "", ""]
    assert screen.history == history
    assert (screen.row, screen.col) == (2, 7)
    assert renderer.state.cursor_visible
    assert "\n" not in update
    assert renderer.render(["cleared", "status", "> draft"], 40, 6, (2, 7), 1) == ""


def test_restoring_shorter_branch_paints_only_owned_tail_not_replayed_history() -> None:
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen(height=6)
    screen.feed(renderer.render([f"old-{i}" for i in range(40)] + ["status", "> draft"], 40, 6, (41, 7), 40))
    history = list(screen.history)
    branch = [f"branch-{i}" for i in range(12)]
    update = renderer.render(branch + ["status", "> draft"], 40, 6, (13, 7), 12)
    screen.feed(update)
    assert screen.rows == branch[-4:] + ["status", "> draft"]
    assert screen.history == history
    assert "branch-0" not in update
    assert "\n" not in update


def test_external_handoff_clears_transients_and_does_not_replay_history():
    painter = MainBufferRenderer(origin_row=3)
    lines = ["DURABLE-HISTORY", "STATUS", "PRIVATE-DRAFT"]
    painter.render(lines, 80, 24, cursor=(2, 10), transient_start=1)
    handoff = painter.handoff()
    assert handoff.count("\x1b[2K") == 2
    assert "DURABLE-HISTORY" not in handoff
    assert "\x1b[2J" not in handoff and "\x1b[3J" not in handoff
    assert painter.state.cursor_row == 4
    painter.adopt_external_cursor(20, 0, 80, 24)
    output = painter.render(lines, 80, 24, cursor=(2, 10), transient_start=1)
    assert "DURABLE-HISTORY" not in output
    assert "PRIVATE-DRAFT" in output
    assert painter.state.origin + 1 == 20


def test_screen_relative_overlay_resize_anchors_owned_tail_even_without_async_cursor():
    renderer = MainBufferRenderer()
    renderer.render(['old overlay'] * 24, 80, 24, cursor=(3, 20), transient_start=0)
    renderer.reanchor(0, 20)  # A terminal reflow may put the actual CPR on row zero.
    output = renderer.render(['new overlay header'] + [''] * 13, 50, 14,
                             transient_start=0, anchor_tail=True)
    assert renderer.state.origin == 0 and 'new overlay header' in output
    assert '\x1b[2J' not in output and '\x1b[3J' not in output and '\x1b[?1049' not in output
    assert renderer.state.cursor_row == 13
    output = renderer.render(['ready overlay'] + [''] * 13, 50, 14, cursor=(2, 10),
                             transient_start=0, anchor_tail=True)
    assert 'ready overlay' in output and renderer.state.cursor_visible and renderer.state.origin == 0


def test_screen_relative_overlay_only_repaints_visible_tail_of_long_transcript_on_resize():
    renderer = MainBufferRenderer()
    renderer.render(['history'] * 60 + ['old view'] * 24, 80, 24, transient_start=60)
    renderer.reanchor(0)
    output = renderer.render(['immutable history'] * 60 + ['new view'] * 14, 50, 14,
                             transient_start=60, anchor_tail=True)
    assert renderer.state.origin == -60
    assert 'immutable history' not in output and output.count('new view') == 14
    assert '\r\n' not in output and '\x1b[2J' not in output and '\x1b[3J' not in output
