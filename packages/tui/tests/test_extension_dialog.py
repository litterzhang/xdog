from __future__ import annotations

from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import visible_width


def test_visible_confirmation_navigation_paste_and_cancel_are_not_implicit_approval():
    answers = []
    panel = ExtensionDialog("test", {"kind": "confirm", "title": "Proceed", "message": "Details"}, answers.append)
    panel.handle_input(KeyEvent("down"))
    panel.handle_input(KeyEvent("enter"))
    assert not answers
    panel.set_render_budget(2)
    panel.render(80)
    panel.handle_input(KeyEvent("enter"))
    assert not answers
    panel.set_render_budget(8)
    panel.render(80)
    panel.handle_paste("\n")
    assert not answers
    panel.handle_input(KeyEvent("enter"))
    assert answers == [True]
    panel.handle_input(KeyEvent("escape"))
    assert answers == [True]


def test_input_and_selection_render_bounds_and_control_code_sanitization():
    for kind, extra in (("input", {"default": "editable"}), ("select", {"choices": ["Red", "Blue"]})):
        answers = []
        panel = ExtensionDialog("test", {"kind": kind, "title": "title\x1b]52;invalid\a", "message": "x" * 8000,
                                          **extra}, answers.append)
        for height in (0, 1, 2, 6, 12):
            panel.set_render_budget(height)
            for width in (0, 1, 24, 80):
                lines = panel.render(width)
                assert len(lines) <= height and all(visible_width(line) <= width for line in lines)
                assert "\x1b]52" not in "\n".join(lines)
        panel.handle_input(KeyEvent("escape"))
        assert answers == [None]
