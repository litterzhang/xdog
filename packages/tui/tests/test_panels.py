"""Panels work independently of Coding and Claw policy."""
from xdog.tui.components import DetailsPanel, InputPanel, PermissionPanel, StatusLine
from xdog.tui.components.bounded_details import BoundedDetails
from xdog.tui.components.details_panel import DetailRecord
from xdog.tui.components.prompt_editor import PromptEditor
from xdog.tui.components.select_list import SelectItem
from xdog.tui.keys import KeyEvent, KeyEventType
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
    panel.render(80)
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


def test_hidden_or_clipped_permission_actions_cannot_be_confirmed():
    selected = []
    panel = PermissionPanel(
        summary="Run example", tool_name="example", choices=[SelectItem("Approve", "yes")],
        theme=Theme(), on_decision=selected.append, on_cancel=lambda: selected.append("cancel"),
    )
    panel.handle_input(KeyEvent(key="enter"))
    assert selected == []  # A newly created panel has not been displayed.
    for budget, width in ((0, 80), (1, 80), (12, 2)):
        panel.set_render_budget(budget)
        panel.render(width)
        panel.handle_input(KeyEvent(key="enter"))
        assert selected == []
    panel.set_render_budget(12)
    panel.render(80)
    panel.handle_input(KeyEvent(key="enter", ctrl=True))
    panel.handle_input(KeyEvent(key="enter", event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent(key="enter", event_type=KeyEventType.RELEASE))
    panel.handle_input(KeyEvent(key=" "))
    assert selected == []
    panel.handle_input(KeyEvent(key="enter"))
    assert selected == ["yes"]
    panel.set_render_budget(0)
    panel.render(80)
    panel.handle_input(KeyEvent(key="escape"))
    assert selected == ["yes", "cancel"]


def test_permission_summary_uses_dim_on_every_wrapped_and_scrolled_line_without_dim_title():
    from xdog.tui.utils import strip_ansi

    class Colored(Theme):
        dim = staticmethod(lambda text: f"\x1b[90m{text}\x1b[39m")
        bold = staticmethod(lambda text: f"\x1b[1m{text}\x1b[22m")

    decisions = []
    summary = 'Run goal: ' + '具体指令与参数，保留审批保护。' * 30
    panel = PermissionPanel(summary, 'goal', [SelectItem('Allow once', 'yes'), SelectItem('Deny', 'no')],
                            Colored(), decisions.append, lambda: decisions.append('no'))
    for width in (24, 60, 120):
        panel.set_render_budget(10)
        for _ in range(3):
            rows = panel.render(width)
            assert all(visible_width(row) <= width for row in rows)
            content_rows = [row for row in rows if 'Run goal:' in strip_ansi(row)
                            or any(char in strip_ansi(row) for char in '具体指令参数保护')]
            assert content_rows and all('\x1b[90m' in row for row in content_rows)
            title = next(row for row in rows if 'Tool permission required' in row)
            assert '\x1b[1m' in title and '\x1b[90m' not in title
            panel.handle_input(KeyEvent(key='pagedown'))
    assert panel.summary == summary and not decisions
    panel.handle_input(KeyEvent(key='escape'))
    assert decisions == ['no']
