from __future__ import annotations

from dataclasses import dataclass

from xdog.tui.components.prompt_editor import PromptEditor
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


@dataclass(frozen=True)
class PlainTheme:
    accent = staticmethod(lambda value: value)
    bold = staticmethod(lambda value: value)
    dim = staticmethod(lambda value: value)
    border = staticmethod(lambda value: value)


def make_editor(**kwargs: object) -> PromptEditor:
    return PromptEditor(PlainTheme(), **kwargs)  # type: ignore[arg-type]


def test_text_callbacks_focus_and_history_contract() -> None:
    editor = make_editor()
    changed: list[str] = []
    editor.on_change = changed.append

    editor.focused = True
    editor.set_text("draft")
    editor.add_to_history("one")
    editor.add_to_history("two")
    editor.add_to_history("two")
    editor.handle_input(KeyEvent(key="up"))

    assert editor.focused
    assert editor.get_text() == "two"
    assert changed[-1] == "two"

    editor.reset_history()
    editor.handle_input(KeyEvent(key="down"))
    assert editor.get_text() == "two"


def test_grapheme_input_movement_and_deletion_are_safe() -> None:
    family = "👨‍👩‍👧‍👦"
    editor = make_editor()
    editor.set_text(f"a{family}b")

    editor.handle_input(KeyEvent(key="left"))
    editor.handle_input(KeyEvent(key="left"))
    assert editor._cursor == 1
    editor.handle_input(KeyEvent(key="delete"))

    assert editor.get_text() == "ab"


def test_paste_normalizes_lines_and_removes_terminal_controls_but_keeps_zwj() -> None:
    editor = make_editor()
    editor.set_text("ab")
    editor._cursor = 1

    assert editor.handle_paste("👩‍💻\r\n\x1b[31mred\x1b[0m\x00")
    assert editor.get_text() == "a👩‍💻\nredb"


def test_undo_redo_and_kill_yank_without_stealing_ctrl_z() -> None:
    editor = make_editor()
    editor.set_text("abc")
    editor.handle_input(KeyEvent(key="k", ctrl=True))
    assert editor.get_text() == "abc"
    editor.handle_input(KeyEvent(key="a", ctrl=True))
    editor.handle_input(KeyEvent(key="k", ctrl=True))
    assert editor.get_text() == ""
    editor.handle_input(KeyEvent(key="y", ctrl=True))
    assert editor.get_text() == "abc"
    assert not editor.handle_input(KeyEvent(key="z", ctrl=True))
    editor.handle_input(KeyEvent(key="z", alt=True))
    assert editor.get_text() == ""
    editor.handle_input(KeyEvent(key="z", alt=True, shift=True))
    assert editor.get_text() == "abc"
    editor.handle_input(KeyEvent(key="z", alt=True))
    editor.handle_input(KeyEvent(key="y", ctrl=True))
    editor.handle_input(KeyEvent(key="y", alt=True))
    assert editor.get_text() == "abc"


def test_long_draft_respects_total_render_budget_and_cursor_view() -> None:
    editor = make_editor(max_rows=8)
    editor.set_text("\n".join(f"line-{index}" for index in range(20)))
    editor.set_render_budget(4, show_borders=True)

    lines = [strip_ansi(line) for line in editor.render(20)]

    assert len(lines) == 4
    # Edit space takes priority over decorations when the draft is taller.
    assert "line-16" in lines[0]
    assert "line-17" in lines[1]
    assert "line-18" in lines[2]
    assert "line-19" in lines[3]


def test_dynamic_commands_normalize_slashes_and_use_three_row_budget() -> None:
    commands = {"help": "Help", "/hello": "Hello", "history": "History", "hooks": "Hooks"}
    editor = make_editor(command_provider=lambda: commands)
    editor.set_render_budget(6)
    editor.set_text("/h")

    lines = [strip_ansi(line) for line in editor.render(40)]

    assert len(lines) <= 6
    assert sum("/help" in line or "/hello" in line or "/history" in line or "/hooks" in line for line in lines) <= 3
    editor.handle_input(KeyEvent(key="tab"))
    assert editor.get_text().startswith("/")


def test_sticky_column_resets_after_horizontal_motion() -> None:
    editor = make_editor()
    editor.render(20)
    editor.set_text("abcdef\nx\nabcdef")
    editor.handle_input(KeyEvent(key="up"))
    editor.handle_input(KeyEvent(key="up"))
    assert editor._cursor == 6
    editor.handle_input(KeyEvent(key="left"))
    editor.handle_input(KeyEvent(key="down"))
    editor.handle_input(KeyEvent(key="down"))
    assert editor._cursor == len("abcdef\nx\nabcde")


def test_exact_width_cursor_marker_is_rendered_on_empty_row() -> None:
    editor = make_editor()
    editor.focused = True
    editor.set_text("abcdef")

    lines = editor.render(8)

    assert len(lines) == 4
    assert any("\x1b_pi:c\x07" in line for line in lines)


def test_ctrl_enter_encodings_insert_newline_without_submitting() -> None:
    from xdog.tui.keys import parse_key_events

    for frame in (b"\x1b[13;5u", b"\x1b[27;5;13~"):
        editor = make_editor()
        submitted: list[str] = []
        editor.on_submit = submitted.append
        editor.set_text("draft")
        for event in parse_key_events(frame):
            editor.handle_input(event)
        assert editor.get_text() == "draft\n"
        assert submitted == []
