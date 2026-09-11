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
