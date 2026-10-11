from __future__ import annotations

from xdog.tui.components.plan_panel import PlanPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import visible_width


def test_visible_revision_confirmation_resize_and_paste_cannot_bypass_guard():
    state = {"mode": "planning", "revision": 2, "objective": "Objective", "steps": [
        {"description": "step", "status": "pending"}]}
    actions = []
    panel = PlanPanel(lambda: state, lambda *args: actions.append(args), lambda: None)
    panel.render(80)
    panel.handle_input(KeyEvent("e"))
    panel.handle_input(KeyEvent("enter"))
    assert not actions  # No confirmation rendered yet.
    panel.set_render_budget(2)
    panel.render(80)
    panel.handle_input(KeyEvent("enter"))
    assert not actions
    panel.set_render_budget(8)
    panel.render(80)
    panel.handle_paste("\n")
    assert not actions
    state["revision"] = 3
    panel.handle_input(KeyEvent("enter"))
    assert not actions  # Background revision change invalidates confirmation.
    panel.handle_input(KeyEvent("e"))
    panel.render(80)
    panel.handle_input(KeyEvent("enter"))
    assert actions == [("execute", 3)]
    state["revision"] = 4
    panel.handle_input(KeyEvent("e"))
    panel.render(80)
    state["busy"] = True
    panel.handle_input(KeyEvent("enter"))
    assert actions == [("execute", 3)]


def test_large_plan_render_is_bounded_and_sanitized_at_tiny_sizes():
    state = {"mode": "planning", "revision": 1, "objective": "x" * 4000, "steps": [
        {"description": "payload\x1b]52;secret\a" * 20, "status": "pending"} for _ in range(32)]}
    panel = PlanPanel(lambda: state, lambda *args: None, lambda: None)
    for height in (0, 1, 2, 6, 12):
        panel.set_render_budget(height)
        for width in (0, 1, 24, 80):
            rows = panel.render(width)
            assert len(rows) <= height and all(visible_width(row) <= width for row in rows)
            assert "\x1b]52" not in "\n".join(rows)


def test_arrow_navigation_scrolls_selected_step_into_view_after_long_objective():
    state = {"mode": "planning", "revision": 1, "objective": "long objective " * 200, "steps": [
        {"description": "first step", "status": "pending"},
        {"description": "second step", "status": "pending"}]}
    panel = PlanPanel(lambda: state, lambda *args: None, lambda: None)
    panel.set_render_budget(6)
    panel.render(40)
    panel.handle_input(KeyEvent("down"))
    assert "▶ 2. [pending] second step" in "\n".join(panel.render(40))
