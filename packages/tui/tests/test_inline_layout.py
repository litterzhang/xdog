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


def test_status_activity_context_and_model_share_one_dim_style() -> None:
    from xdog.tui.components.status_line import StatusLine
    from xdog.tui.utils import strip_ansi

    def gray(text: str) -> str:
        return f"\x1b[90m{text}\x1b[39m"

    status = StatusLine(gray)
    status.set_fields(activity="ready", model="model-name", context="ctx:1k/32k", queued=2)
    for width in (8, 32, 80):
        row = status.render(width)[0]
        assert row == gray(strip_ansi(row))
        assert visible_width(row) <= width
    assert "ready" in strip_ansi(status.render(80)[0])
    assert "model-name" in strip_ansi(status.render(80)[0])
    assert "ctx:1k/32k" in strip_ansi(status.render(80)[0])


def test_compact_hint_style_applies_after_every_update_and_truncation() -> None:
    from xdog.tui.components.inline_layout import CompactText
    from xdog.tui.utils import strip_ansi

    def gray(text: str) -> str:
        return f"\x1b[90m{text}\x1b[39m"

    hints = CompactText("", 0, 0, style=gray)
    for text in ("Enter send · Ctrl+Enter newline", "Cancelling · draft preserved", ""):
        hints.set_text(text)
        assert hints.text == text
        for width in (0, 8, 40):
            rows = hints.render(width)
            assert all(row == gray(strip_ansi(row)) for row in rows)
            assert all(visible_width(row) <= width for row in rows)
    plain = CompactText("plain", 0, 0, style=lambda text: text)
    assert plain.render(20) == ["plain"]


@pytest.mark.parametrize("last_role", ["user", "assistant", "tool"])
def test_status_does_not_add_a_second_gap_after_message_padding(last_role: str) -> None:
    from xdog.tui.components.markdown import DefaultTextStyle, MarkdownTheme
    from xdog.tui.components.messages import NormalMessage, Transcript, UserMessage
    from xdog.tui.components.tool_message import ToolMessage
    from xdog.tui.utils import strip_ansi

    transcript = Transcript()
    if last_role == "user":
        transcript.add_child(UserMessage("question", MarkdownTheme(), DefaultTextStyle(
            bg_color=lambda text: f"\x1b[44m{text}\x1b[49m",
        )))
    elif last_role == "assistant":
        transcript.add_child(NormalMessage("answer", MarkdownTheme()))
    else:
        tool = ToolMessage()
        tool.update(header="• Ran pwd", summary="", output="/workspace", running=False,
                    style=lambda text: text)
        transcript.add_child(tool)
    layout = InlineLayout(transcript, Text("ready", 0, 0), Text("editor", 0, 0), render_height=10)
    rows = layout.render(40)
    plain = [strip_ansi(row).rstrip() for row in rows]
    status = plain.index("ready")
    assert plain[status - 1] == ""
    assert plain[status - 2] != ""
    assert layout.transient_start == status  # no redundant transient separator
    if last_role == "user":
        assert "\x1b[44m" in rows[status - 1]  # preserve the user-owned background


@pytest.mark.parametrize("auxiliary", [False, True])
@pytest.mark.parametrize("trailing_blank", [False, True])
def test_status_gap_uses_the_actual_preceding_work_or_queue_row(auxiliary: bool, trailing_blank: bool) -> None:
    from xdog.tui.utils import strip_ansi

    content = Text("summary\n " if trailing_blank else "summary", 0, 0)
    layout = InlineLayout(Text("history", 0, 0), Text("ready", 0, 0), Text("editor", 0, 0),
                          work=None if auxiliary else content, queue=content if auxiliary else None,
                          render_height=12)
    plain = [strip_ansi(row).rstrip() for row in layout.render(40)]
    status = plain.index("ready")
    assert plain[status - 2:status] == ["summary", ""]


@pytest.mark.parametrize("width", [8, 24, 80])
def test_status_and_shortcuts_align_with_editable_text_not_prompt_marker(width: int) -> None:
    from xdog.tui.components.input_panel import InputPanel
    from xdog.tui.components.panel import PanelTheme
    from xdog.tui.components.status_line import StatusLine
    from xdog.tui.utils import strip_ansi

    editor = InputPanel(PanelTheme())
    editor.set_text("INPUT")
    status = StatusLine(lambda text: text)
    status.set_fields(activity="ready", model="model")
    hints = Text("Enter send", 0, 0)
    layout = InlineLayout(Text("", 0, 0), status, editor, hints=hints, render_height=24)
    rows = [strip_ansi(row) for row in layout.render(width)]
    status_row = next(row for row in rows if "ready" in row)
    input_row = next(row for row in rows if "INPUT" in row)
    hint_row = next(row for row in rows if "Enter" in row)
    assert status_row.index("ready") == input_row.index("INPUT") == hint_row.index("Enter") == 2
    assert all(visible_width(row) <= width for row in rows)
    if width >= 24:
        assert status_row.endswith("model") and visible_width(status_row) == width
