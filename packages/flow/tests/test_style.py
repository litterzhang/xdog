"""Tests for flow.builder.style — colored cells align to display width."""

from __future__ import annotations

from xdog.flow.builder import style
from xdog.tui import string_width, strip_ansi


def test_fg_wraps_and_preserves_text() -> None:
    s = style.fg("hi", style.AGENT)
    assert strip_ansi(s) == "hi"
    assert s.startswith("\x1b[38;2;")
    assert s.endswith("\x1b[39m")


def test_bg_and_bold_and_dim_preserve_text() -> None:
    assert strip_ansi(style.bg("x", style.SELECT_BG)) == "x"
    assert strip_ansi(style.bold("x")) == "x"
    assert strip_ansi(style.dim("x")) == "x"


def test_pad_plain_to_width() -> None:
    assert string_width(style.pad("abc", 10)) == 10
    assert style.pad("abc", 10) == "abc" + " " * 7


def test_pad_colored_cell_aligns_to_display_width() -> None:
    """A colored 3-char cell must pad to the SAME visible width as plain."""
    colored = style.fg("abc", style.AGENT)
    padded = style.pad(colored, 10)
    assert string_width(padded) == 10
    assert strip_ansi(padded).rstrip() == "abc"


def test_pad_composed_fg_on_bg_aligns() -> None:
    cell = style.bg(style.fg("node", style.SCRIPT), style.SELECT_BG)
    assert string_width(style.pad(cell, 20)) == 20


def test_pad_truncates_by_visible_columns() -> None:
    padded = style.pad("abcdefghij", 4)
    assert string_width(padded) == 4
    assert strip_ansi(padded) == "abcd"


def test_pad_truncates_colored_without_bleeding() -> None:
    colored = style.fg("abcdefghij", style.AGENT)
    padded = style.pad(colored, 4)
    assert string_width(padded) == 4
    assert strip_ansi(padded) == "abcd"
    # styling is closed, not left dangling
    assert padded.endswith("\x1b[0m") or padded.endswith("\x1b[39m")


def test_pad_zero_width() -> None:
    assert style.pad("abc", 0) == ""


def test_pad_flattens_embedded_newlines_and_tabs() -> None:
    """A cell with embedded newlines/tabs stays a SINGLE physical line.

    Regression: a node's multiline prompt (``"a\\n\\nb"``) leaked its newlines
    into the rendered row, so one list element spanned several terminal lines —
    corrupting the differential renderer (duplicated/broken boxes) when moving
    the selection.  ``pad`` must collapse control whitespace to spaces first.
    """
    padded = style.pad("a\n\nb", 10)
    assert "\n" not in padded and "\r" not in padded and "\t" not in padded
    assert string_width(padded) == 10
    # a run of control whitespace collapses to a single space
    assert strip_ansi(padded) == "a b" + " " * 7

    tabbed = style.pad("x\ty", 10)
    assert "\t" not in tabbed
    assert string_width(tabbed) == 10
