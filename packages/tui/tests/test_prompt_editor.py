from __future__ import annotations

from dataclasses import dataclass

from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


@dataclass(frozen=True)
class PlainTheme:
    accent = staticmethod(lambda value: value)
    bold = staticmethod(lambda value: value)
    dim = staticmethod(lambda value: value)
    border = staticmethod(lambda value: value)


def make_editor(**kwargs: object) -> InputPanel:
    return InputPanel(PlainTheme(), **kwargs)  # type: ignore[arg-type]


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


class CommandColors(PlainTheme):
    accent = staticmethod(lambda value: '\x1b[33m' + value + '\x1b[39m')
    dim = staticmethod(lambda value: '\x1b[90m' + value + '\x1b[39m')


def test_typed_registered_command_is_highlighted_but_not_its_arguments() -> None:
    editor = InputPanel(CommandColors(), command_provider=lambda: {'goal': 'Manage goal', '/help': 'Help'})
    editor.set_autocomplete_visible(False)
    editor.set_text('/goal status /help')
    rows = editor.render(80)
    assert any('\x1b[33m/goal\x1b[39m status /help' in row for row in rows)
    assert editor.get_text() == '/goal status /help'
    editor.set_text('/HELP')
    assert any('\x1b[33m/HELP\x1b[39m' in row for row in editor.render(80))
    editor.set_text('/go')
    assert not any('\x1b[33m/go' in row for row in editor.render(80))
    editor.set_text('/tmp/file')
    assert not any('\x1b[33m/tmp' in row for row in editor.render(80))
    editor.set_text('ordinary /help')
    assert not any('\x1b[33m/help' in row for row in editor.render(80))
