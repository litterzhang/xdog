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


def test_suspend_reports_platform_support(monkeypatch) -> None:
    from xdog.tui.tui import TUI

    tui = TUI()
    monkeypatch.setattr("xdog.tui.tui.os.name", "nt")
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
