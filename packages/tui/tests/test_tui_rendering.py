"""Regression tests for the main-buffer differential renderer."""

from __future__ import annotations

import io

from xdog.tui.components.text import Text
from xdog.tui.tui import TUI, Component, OverlayOptions


class _MutableLines(Component):
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def render(self, width: int) -> list[str]:
        return list(self.lines)


def test_full_redraw_preserves_terminal_scrollback(monkeypatch) -> None:
    import xdog.tui.tui as tui_module

    output = io.StringIO()
    monkeypatch.setattr(tui_module.sys, "stdout", output)
    monkeypatch.setattr(tui_module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(tui_module, "_terminal_height", lambda: 24)

    component = _MutableLines(["one", "two", "three"])
    tui = TUI()
    tui.add_child(component)
    tui._do_render()

    output.seek(0)
    output.truncate(0)
    component.lines = ["one"]
    tui._do_render()

    rendered = output.getvalue()
    assert "\x1b[2J" not in rendered
    assert "\x1b[3J" not in rendered
    assert "\x1b[2K" in rendered
    # Only the two vacated rows are erased; shrinking never scrolls or replays.
    assert rendered.count("\x1b[2K") == 2
    assert "\n" not in rendered
    assert "one" not in rendered


def test_full_redraw_does_not_replay_offscreen_history(monkeypatch) -> None:
    import xdog.tui.tui as tui_module

    output = io.StringIO()
    monkeypatch.setattr(tui_module.sys, "stdout", output)
    monkeypatch.setattr(tui_module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(tui_module, "_terminal_height", lambda: 3)

    component = _MutableLines([f"history-{index}" for index in range(6)])
    tui = TUI()
    tui.add_child(component)
    tui._do_render()

    output.seek(0)
    output.truncate(0)
    # Native scrollback is immutable; an offscreen change needs no repaint.
    component.lines[0] = "changed-offscreen-history"
    tui._do_render()

    rendered = output.getvalue()
    assert "\x1b[2J" not in rendered
    assert rendered == ""


def test_shrinking_transient_panel_does_not_replay_input_box(monkeypatch) -> None:
    import xdog.tui.tui as tui_module

    output = io.StringIO()
    monkeypatch.setattr(tui_module.sys, "stdout", output)
    monkeypatch.setattr(tui_module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(tui_module, "_terminal_height", lambda: 4)

    component = _MutableLines([
        "chat",
        "status",
        "footer",
        "> input",
        "permission choice 1",
        "permission choice 2",
    ])
    component.transient_start = 1  # Declare chrome separately from durable chat.
    tui = TUI()
    tui.add_child(component)
    tui._do_render()

    output.seek(0)
    output.truncate(0)
    component.lines = ["chat", "status", "footer", "> input"]
    tui._do_render()

    rendered = output.getvalue()
    # The existing input stays in place while the two permission rows clear.
    assert "> input" not in rendered
    assert rendered.count("\x1b[2K") == 2
    assert "\n" not in rendered
    assert "\x1b[2J" not in rendered


def test_suspend_without_job_control_signal_fails_closed(monkeypatch) -> None:
    from xdog.tui.tui import TUI

    tui = TUI()
    monkeypatch.delattr("xdog.tui.tui.signal.SIGTSTP")
    assert tui.suspend() is False
    assert tui._suspend_requested is False


def test_fullscreen_mode_uses_alternate_screen() -> None:
    from xdog.tui.tui import TUI

    tui = TUI(fullscreen=True)
    assert tui.fullscreen is True
    assert tui._terminal_enter_sequence().startswith("\x1b[?1049h")
    assert tui._terminal_leave_sequence().endswith("\x1b[?1049l")


def test_nested_overlay_restores_previous_focus() -> None:
    from xdog.tui.components.text import Text
    from xdog.tui.tui import TUI

    tui = TUI()
    original = Text("original")
    first = Text("first")
    second = Text("second")
    tui.set_focus(original)

    first_handle = tui.show_overlay(first)
    second_handle = tui.show_overlay(second)
    second_handle.hide()
    assert tui._focused is first
    first_handle.hide()
    assert tui._focused is original


def test_overlay_is_composited_into_visible_tail_viewport() -> None:
    tui = TUI()
    base_lines = [f"history-{index}" for index in range(30)]
    tui.show_overlay(
        Text("PERMISSION DIALOG", 0, 0),
        OverlayOptions(width=24, anchor="center"),
    )

    composited = tui._composite_overlays(base_lines, width=80, height=10)

    assert not any("PERMISSION DIALOG" in line for line in composited[:-10])
    assert any("PERMISSION DIALOG" in line for line in composited[-10:])


def test_ctrl_l_forces_owned_viewport_repaint_without_changing_focus(monkeypatch) -> None:
    import xdog.tui.tui as tui_module
    from xdog.tui.keys import KeyEvent

    output = io.StringIO()
    monkeypatch.setattr(tui_module.sys, "stdout", output)
    monkeypatch.setattr(tui_module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(tui_module, "_terminal_height", lambda: 3)
    component = _MutableLines([f"history-{i}" for i in range(6)])
    tui = TUI()
    tui.add_child(component)
    tui.set_focus(component)
    tui._do_render()
    output.seek(0)
    output.truncate(0)

    assert tui._dispatch_input(KeyEvent(key="l", ctrl=True))
    assert tui._focused is component
    assert tui._render_requested
    tui._do_render()

    rendered = output.getvalue()
    assert "history-5" in rendered
    assert "history-0" not in rendered
    assert "\x1b[2J" not in rendered
    assert "\x1b[3J" not in rendered
    assert "\n" not in rendered


def test_focused_editor_cursor_visibility_tracks_details_focus(monkeypatch) -> None:
    from types import SimpleNamespace

    import xdog.tui.tui as module
    from xdog.tui.components.input_panel import InputPanel

    def plain(text):
        return text
    editor = InputPanel(SimpleNamespace(accent=plain, bold=plain, dim=plain, border=plain))
    editor.set_text("draft")
    details = Text("details", 0, 0)
    output = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", output)
    monkeypatch.setattr(module, "_terminal_width", lambda: 40)
    monkeypatch.setattr(module, "_terminal_height", lambda: 8)
    tui = TUI()
    tui.add_child(editor)
    tui.add_child(details)
    tui.set_focus(editor)
    tui._do_render()
    assert "\x1b[?25h" in output.getvalue()
    output.seek(0)
    output.truncate(0)

    tui.set_focus(details)
    tui._do_render()
    assert "\x1b[?25l" in output.getvalue()
    assert "\x1b[?25h" not in output.getvalue()
    assert not editor.focused
    output.seek(0)
    output.truncate(0)

    tui.set_focus(editor)
    tui._do_render()
    assert "\x1b[?25h" in output.getvalue()
    assert editor.focused
    assert editor.get_text() == "draft"


class _FocusableLines(_MutableLines):
    focused = False

    def __init__(self, label: str) -> None:
        super().__init__([label])
        self.inputs = []
        self.pastes = []

    def handle_input(self, event):
        self.inputs.append(event)
        return True

    def handle_paste(self, text):
        self.pastes.append(text)
        return True


def test_overlay_flags_and_pop_restore_editor_without_terminal_writes(monkeypatch) -> None:
    output = io.StringIO()
    monkeypatch.setattr("xdog.tui.tui.sys.stdout", output)
    tui = TUI()
    editor, dialog = _FocusableLines("editor"), _FocusableLines("dialog")
    tui.set_focus(editor)
    tui.show_overlay(dialog)
    assert dialog.focused and not editor.focused
    tui.hide_overlay()
    assert tui._focused is editor
    assert editor.focused and not dialog.focused
    assert output.getvalue() == ""


def test_removing_underlying_overlay_never_restores_removed_component() -> None:
    tui = TUI()
    editor, lower, upper = (_FocusableLines(label) for label in ("editor", "lower", "upper"))
    tui.set_focus(editor)
    first = tui.show_overlay(lower)
    second = tui.show_overlay(upper)
    first.hide()
    second.hide()
    assert tui._focused is editor
    assert editor.focused
    assert not lower.focused and not upper.focused


def test_temporarily_hidden_overlay_releases_input_and_restores_on_show() -> None:
    from xdog.tui.keys import KeyEvent
    tui = TUI()
    editor, dialog = _FocusableLines("editor"), _FocusableLines("dialog")
    tui.set_focus(editor)
    handle = tui.show_overlay(dialog)
    tui._render_requested = False
    handle.set_hidden(True)
    assert tui._render_requested
    assert tui._focused is editor
    assert editor.focused and not dialog.focused
    tui._dispatch_input(KeyEvent(key="x"))
    tui._dispatch_paste("paste")
    assert len(editor.inputs) == 1 and editor.pastes == ["paste"]
    assert not dialog.inputs and not dialog.pastes
    handle.set_hidden(False)
    assert tui._focused is dialog
    assert not editor.focused and dialog.focused


def test_overlay_visibility_change_releases_keyboard_and_paste_focus(monkeypatch) -> None:
    from xdog.tui.keys import KeyEvent
    visible = [True]
    tui = TUI()
    editor, dialog = _FocusableLines("editor"), _FocusableLines("dialog")
    tui.set_focus(editor)
    tui.show_overlay(dialog, OverlayOptions(visible=lambda width, height: visible[0]))
    visible[0] = False
    tui._dispatch_input(KeyEvent(key="x"))
    tui._dispatch_paste("paste")
    assert tui._focused is editor
    assert len(editor.inputs) == 1 and editor.pastes == ["paste"]
    assert not dialog.inputs and not dialog.pastes
    assert tui._composite_overlays(["base"], 40, 8) == ["base"]
    visible[0] = True
    tui._dispatch_input(KeyEvent(key="y"))
    assert tui._focused is dialog and len(dialog.inputs) == 1


def test_overlay_focus_brings_it_to_front_and_unfocus_releases_capture() -> None:
    tui = TUI()
    editor, lower, upper = (_FocusableLines(label) for label in ("editor", "lower", "upper"))
    tui.set_focus(editor)
    first = tui.show_overlay(lower)
    tui.show_overlay(upper)
    first.focus()
    assert first.is_focused()
    assert tui._overlay_stack[-1].component is lower
    assert lower.focused and not upper.focused
    first.unfocus()
    assert tui._focused is upper
    assert upper.focused and not lower.focused
    first.hide()
    tui.hide_overlay()
    assert tui._focused is editor


def test_non_capturing_overlay_keeps_base_focus() -> None:
    tui = TUI()
    editor, decoration = _FocusableLines("editor"), _FocusableLines("decoration")
    tui.set_focus(editor)
    tui.show_overlay(decoration, OverlayOptions(non_capturing=True))
    assert tui._focused is editor
    assert editor.focused and not decoration.focused
    tui.hide_overlay()
    assert tui._focused is editor


def test_overlay_composites_by_display_cells_and_clears_whole_footprint() -> None:
    from xdog.tui.utils import strip_ansi, visible_width
    tui = TUI()
    tui.show_overlay(_MutableLines(["X"]), OverlayOptions(width=4, row=0, col=3))
    base = "\x1b[31m界👩‍💻éabcdefgh\x1b[0m"
    row = tui._composite_overlays([base], 14, 1)[0]
    # The joined emoji crosses column 3, so its uncovered cell becomes a space.
    assert strip_ansi(row) == "界 X   cdefgh "
    assert visible_width(row) == 14
    assert "\x1b[31m" in row
    assert "\x1b[0m" in row


def test_overlay_margin_and_height_are_clamped_to_tiny_terminal() -> None:
    tui = TUI()
    dialog = _MutableLines(["too wide"] * 8)
    tui.show_overlay(dialog, OverlayOptions(width=20, min_width=10, margin=100, row=50, col=50))
    rows = tui._composite_overlays(["abc"], 3, 2)
    from xdog.tui.utils import visible_width
    assert len(rows) <= 2
    assert all(visible_width(row) <= 3 for row in rows)


def test_non_capturing_overlay_hides_only_a_cursor_it_occludes(monkeypatch) -> None:
    from xdog.tui.tui import CURSOR_MARKER
    tui = TUI()
    base = "abc" + CURSOR_MARKER + "defgh"
    handle = tui.show_overlay(_MutableLines(["XX"]), OverlayOptions(width=2, row=0, col=3, non_capturing=True))
    covered = tui._composite_overlays([base], 8, 1)[0]
    assert CURSOR_MARKER not in covered
    handle.hide()
    tui.show_overlay(_MutableLines(["XX"]), OverlayOptions(width=2, row=0, col=5, non_capturing=True))
    uncovered = tui._composite_overlays([base], 8, 1)[0]
    assert CURSOR_MARKER in uncovered


def test_modal_rejected_paste_does_not_reach_base_editor() -> None:
    tui = TUI()
    editor = _FocusableLines("editor")
    tui.set_focus(editor)
    tui.show_overlay(Text("dialog", 0, 0))
    assert not tui._dispatch_paste("unintended input")
    assert not editor.pastes


def test_confirmation_during_unpainted_resize_is_consumed_until_new_frame(monkeypatch) -> None:
    from xdog.tui.keys import KeyEvent
    component = _FocusableLines("permission")
    component.requires_visible_confirmation = True
    tui = TUI()
    tui.set_focus(component)
    tui._previous_width, tui._previous_height = 80, 24
    monkeypatch.setattr("xdog.tui.tui._terminal_width", lambda: 80)
    monkeypatch.setattr("xdog.tui.tui._terminal_height", lambda: 1)
    assert tui._dispatch_input(KeyEvent(key="enter"))
    assert not component.inputs
    assert tui._render_requested
    tui._previous_height = 1
    assert tui._dispatch_input(KeyEvent(key="enter"))
    assert len(component.inputs) == 1


def test_fullscreen_resize_rehomes_only_alternate_painter_and_restores_complete_header(monkeypatch):
    from dataclasses import replace

    import xdog.tui.tui as module
    from xdog.tui.main_buffer_renderer import MainBufferRenderer
    output = io.StringIO()
    monkeypatch.setattr(module.sys, 'stdout', output)
    monkeypatch.setattr(module, '_terminal_width', lambda: 80)
    monkeypatch.setattr(module, '_terminal_height', lambda: 24)
    tui = TUI(fullscreen=True)
    saved = MainBufferRenderer(origin_row=4)
    saved.render(['native scrollback', 'parent draft'], 80, 24)
    before = saved.state
    tui._saved_main_painter = saved
    tui._painter.state = replace(tui._painter.state, width=20, height=5, origin=-2, logical_cursor=2)
    tui._previous_width, tui._previous_height = 20, 5
    tui.add_child(_MutableLines(['owned header', 'view title', 'body']))
    tui._do_render()
    assert '\x1b[2J\x1b[H' in output.getvalue() and 'owned header' in output.getvalue()
    assert tui._painter.state.origin == 0 and tui._saved_main_painter is saved and saved.state == before
