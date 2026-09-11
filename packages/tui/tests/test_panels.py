"""Panels work independently of Coding and Claw policy."""
from xdog.tui.components import DetailsPanel, InputPanel, PermissionPanel, StatusLine
from xdog.tui.components.bounded_details import BoundedDetails
from xdog.tui.components.details_panel import DetailRecord
from xdog.tui.components.prompt_editor import PromptEditor
from xdog.tui.components.select_list import SelectItem
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import visible_width


class Theme:
    accent = staticmethod(lambda text: text)
    inverse = staticmethod(lambda text: text)
    bold = staticmethod(lambda text: text)
    dim = staticmethod(lambda text: text)
    border = staticmethod(lambda text: text)


def test_public_panel_aliases_and_input_behavior():
    assert PromptEditor is InputPanel
    assert BoundedDetails is DetailsPanel
    editor = InputPanel(Theme())
    editor.set_text("draft")
    editor.handle_input(KeyEvent(key="enter", ctrl=True))
    assert editor.get_text() == "draft\n"


def test_permission_panel_reports_supplied_decisions_without_policy():
    selected = []
    panel = PermissionPanel(
        summary="Run example", tool_name="example",
        choices=[SelectItem("Approve", "yes"), SelectItem("Reject", "no")],
        theme=Theme(), on_decision=selected.append, on_cancel=lambda: selected.append("cancel"),
    )
    panel.handle_input(KeyEvent(key="down"))
    panel.handle_input(KeyEvent(key="enter"))
    panel.handle_input(KeyEvent(key="escape"))
    assert selected == ["no", "cancel"]
    for budget in (1, 4, 12):
        panel.set_render_budget(budget)
        rows = panel.render(40)
        assert len(rows) <= budget
        assert all(visible_width(row) <= 40 for row in rows)


def test_details_panel_and_status_line_are_standalone():
    panel = DetailsPanel(lambda: [DetailRecord("tool", "\n".join(str(i) for i in range(20)))])
    panel.show_latest(from_start=True)
    assert panel.render(40)[1] == "0"
    panel.handle_input(KeyEvent(key="end"))
    assert panel.render(40)[-1] == "19"
    status = StatusLine(lambda value: value)
    status.set_text("ready\nconnected")
    assert status.render(40) == ["ready connected"]
    status.set_fields(activity="running", model="model", context="ctx:1k/10k")
    assert "ctx:1k/10k" in status.render(50)[0]
    assert len(status.render(12)) == 1
    assert visible_width(status.render(12)[0]) <= 12
