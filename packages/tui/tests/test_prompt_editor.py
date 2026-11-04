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


def test_unfocused_editor_has_no_cursor_marker_or_reverse_video_cursor() -> None:
    from xdog.tui.tui import CURSOR_MARKER
    editor = make_editor()
    editor.set_text("a👩‍💻b")
    editor.handle_input(KeyEvent(key="left"))
    editor.handle_input(KeyEvent(key="left"))
    unfocused = editor.render(40)
    assert not any(CURSOR_MARKER in line or "\x1b[7m" in line for line in unfocused)
    editor.focused = True
    focused = editor.render(40)
    assert any(CURSOR_MARKER + "👩‍💻" in line for line in focused)
    assert not any("\x1b[7m" in line for line in focused)
    assert [strip_ansi(line) for line in focused] == [strip_ansi(line) for line in unfocused]


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


def test_command_styling_preserves_wrapping_graphemes_tabs_and_hardware_cursor() -> None:
    import re

    from xdog.tui.components.input_panel import _with_cursor
    from xdog.tui.editor_layout import layout_editor
    from xdog.tui.utils import string_width

    text = '/测试👩‍💻\targ\nnext'
    editor = InputPanel(CommandColors(), command_provider=lambda: {'测试👩‍💻': 'Command'})
    editor.set_render_budget(30, show_borders=False)
    editor.set_autocomplete_visible(False)
    editor.set_text(text)
    for width in (5, 6, 9, 40):
        layout = layout_editor(text, width - editor.content_column)
        offsets = {offset for row in layout.rows for offset, _column in row.boundaries}
        for focused in (True, False):
            editor.focused = focused
            for offset in offsets:
                editor._cursor = offset
                cursor_row, cursor_column = layout.position(offset)
                actual = editor.render(width)
                # Remove SGR only: the hardware cursor marker must survive at
                # exactly its original position, including inside the command.
                unstyled = [re.sub(r'\x1b\[[0-9;]*m', '', row) for row in actual]
                expected = [('> ' if index == 0 else '  ') + (
                    _with_cursor(row.text, cursor_column, focused) if index == cursor_row else row.text)
                    for index, row in enumerate(layout.rows)]
                assert unstyled == expected
                assert all(string_width(row) <= width for row in actual)
    editor.focused = False
    rows = editor.render(5)
    assert any('\x1b[33m' in row[2:] for row in rows[1:])  # Wrapped command stays highlighted.
    assert all('\x1b[33m' not in row for row in rows if 'arg' in strip_ansi(row) or 'next' in strip_ansi(row))


def test_highlight_rendering_does_not_query_command_provider_or_change_submit() -> None:
    calls = []

    def commands():
        calls.append(True)
        return {'goal': 'Goal'}

    editor = InputPanel(CommandColors(), command_provider=commands)
    editor.set_text('/goal status')
    before = len(calls)
    for _ in range(10):
        editor.render(80)
        editor.poll_autocomplete()
        editor.handle_input(KeyEvent(key='left'))
    assert len(calls) == before
    submitted = []
    editor.on_submit = submitted.append
    editor.handle_input(KeyEvent(key='enter'))
    assert submitted == ['/goal status'] and editor.get_text() == ''
    editor.set_text('ordinary text')
    assert not any('\x1b[33mordinary' in row for row in editor.render(80))


def test_newly_registered_exact_command_invalidates_highlighting_without_suggestions() -> None:
    commands = {}
    editor = InputPanel(CommandColors(), command_provider=lambda: commands)
    editor.set_text('/new')
    assert not editor.autocomplete_active
    commands['new'] = 'New command'
    assert editor.poll_autocomplete()
    assert not editor.autocomplete_active
    assert any('\x1b[33m/new\x1b[39m' in row for row in editor.render(80))
    assert not editor.poll_autocomplete()
    commands.clear()
    assert editor.poll_autocomplete()
    assert not any('\x1b[33m/new' in row for row in editor.render(80))


def test_completion_list_styles_remain_unchanged() -> None:
    from xdog.tui.components.input_panel import SlashSelectList

    menu = SlashSelectList([('/goal', 'Goal'), ('/help', 'Help')])
    selected, other = menu.render(80, CommandColors())
    assert selected.startswith('\x1b[33m→ /goal')
    assert other.startswith('\x1b[90m  /help') and other.endswith('\x1b[39m')
