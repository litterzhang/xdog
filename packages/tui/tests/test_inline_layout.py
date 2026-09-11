from __future__ import annotations

from dataclasses import dataclass

import pytest
from xdog.tui.components.bounded_details import BoundedDetails, DetailRecord
from xdog.tui.components.inline_layout import InlineLayout
from xdog.tui.components.text import Text
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component
from xdog.tui.utils import visible_width


@dataclass
class _Editor(Component):
    budget: int = 8
    borders: bool = True

    def set_render_budget(self, max_rows: int, show_borders: bool = True) -> None:
        self.budget = max_rows
        self.borders = show_borders

    def render(self, width: int) -> list[str]:
        return ["editor"] * self.budget


def test_inline_layout_exposes_transient_prefix_and_height_budget() -> None:
    editor = _Editor()
    layout = InlineLayout(
        Text("one\ntwo", 0, 0),
        Text("status", 0, 0),
        editor,
        work=Text("work\nmore work", 0, 0),
        queue=Text("queued", 0, 0),
        render_height=6,
    )

    rows = layout.render(40)

    assert layout.transient_start == 2
    assert len(rows[layout.transient_start:]) == 6
    assert editor.budget == 4


def test_inline_layout_preserves_status_editor_and_auxiliary_at_tiny_height() -> None:
    editor = _Editor()
    layout = InlineLayout(
        Text("history", 0, 0),
        Text("status", 0, 0),
        editor,
        permission=Text("permission-1\npermission-2\npermission-3\npermission-4", 0, 0),
        render_height=4,
    )

    tail = layout.render(40)[layout.transient_start:]

    assert len(tail) == 4
    assert any("status" in row for row in tail)
    assert any("editor" in row for row in tail)
    assert any("permission" in row for row in tail)


def test_inline_layout_set_height_updates_editor_budget() -> None:
    editor = _Editor()
    layout = InlineLayout(Text("history", 0, 0), Text("status", 0, 0), editor)

    layout.set_height(4)
    layout.render(40)

    assert editor.budget == 3
    assert editor.borders


def test_bounded_details_navigates_records_and_scrolls() -> None:
    records = (
        DetailRecord("first", "1\n2\n3\n4", "tool"),
        DetailRecord("second", "a\nb", "reasoning"),
    )
    details = BoundedDetails(lambda: records, max_rows=3)

    assert "second" in details.render(80)[0]
    assert details.handle_input(KeyEvent(key="left"))
    assert "first" in details.render(80)[0]
    before = details.render(80)
    assert details.handle_input(KeyEvent(key="pageup"))
    assert details.render(80) != before


def test_single_row_terminal_keeps_editor_within_height_budget() -> None:
    layout = InlineLayout(
        Text("history", 0, 0), Text("status", 0, 0), _Editor(),
        queue=Text("queued", 0, 0), render_height=1,
    )

    rows = layout.render(20)

    assert rows[layout.transient_start:] == ["editor"]


def test_details_respect_display_cell_width_for_wide_text() -> None:
    details = BoundedDetails(lambda: (DetailRecord("工具结果", "界" * 20),))

    rows = details.render(10)

    assert all(visible_width(row) <= 10 for row in rows)


def test_details_can_scroll_to_every_part_of_a_long_line() -> None:
    details = BoundedDetails(lambda: (DetailRecord("tool", "HEAD" + "x" * 100 + "TAIL"),), max_rows=3)
    assert "TAIL" in "".join(details.render(12))
    for _ in range(20):
        details.handle_input(KeyEvent(key="pageup"))
    assert "HEAD" in "".join(details.render(12))


@pytest.mark.parametrize("height", [6, 10, 24])
@pytest.mark.parametrize("width", [12, 80])
def test_editor_layout_budget_with_unicode_and_details(height: int, width: int) -> None:
    from types import SimpleNamespace

    from xdog.tui.components.prompt_editor import PromptEditor
    def plain(text: str) -> str:
        return text
    editor = PromptEditor(SimpleNamespace(accent=plain, bold=plain, dim=plain, border=plain))
    editor.set_text("界e\u0301👩\u200d💻🇺🇸\t\n" * 5 + "draft")
    layout = InlineLayout(
        Text("history", 0, 0), Text("status", 0, 0), editor,
        details=BoundedDetails(lambda: (DetailRecord("tool", "result" * 20),)),
        render_height=height,
    )
    rows = layout.render(width)[layout.transient_start:]
    assert len(rows) <= height
    assert all(visible_width(row) <= width for row in rows)
    assert any("draft" in row for row in rows)


def test_cell_truncation_does_not_split_joined_emoji() -> None:
    from xdog.tui.utils import truncate_to_width
    emoji = "👩\u200d💻"
    assert truncate_to_width(emoji + "abcd", 3, "") == emoji + "a"


def test_small_detail_pages_visit_every_line_and_clamp_at_top() -> None:
    details = BoundedDetails(lambda: (DetailRecord("tool", "\n".join(f"line-{i}" for i in range(20))),))
    details.set_render_budget(3)
    seen = set()
    for _ in range(30):
        seen.update(details.render(60)[1:])
        details.handle_input(KeyEvent(key="pageup"))
    assert seen == {f"line-{i}" for i in range(20)}
    top = details.render(60)
    details.handle_input(KeyEvent(key="pagedown"))
    assert details.render(60) != top


def test_hidden_work_summary_has_visible_omission_count() -> None:
    layout = InlineLayout(Text("", 0, 0), Text("idle", 0, 0), _Editor(),
                          work=Text("goal\ntodos", 0, 0), render_height=4)
    rows = layout.render(60)
    assert any("2 work summaries hidden" in row for row in rows)


def test_open_details_from_start_pins_top_until_explicit_end() -> None:
    records = [DetailRecord("tool", "\n".join(f"row-{i}" for i in range(30)), "tool")]
    details = BoundedDetails(lambda: records, max_rows=4)
    details.show_latest(from_start=True)
    assert "row-0" in details.render(80)
    assert "row-29" not in details.render(80)
    records[0] = DetailRecord("tool", records[0].body + "\nrow-30", "tool")
    assert "row-0" in details.render(80)
    details.handle_input(KeyEvent(key="end"))
    assert "row-30" in details.render(80)
    details.show_latest(from_start=True)
    assert "row-0" in details.render(80)
