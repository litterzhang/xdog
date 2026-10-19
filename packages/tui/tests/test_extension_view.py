"""Shared bounded renderer knows only generic status rows, never a feature schema."""
from __future__ import annotations

import pytest
from xdog.tui.components.extension_view import ExtensionView
from xdog.tui.utils import string_width


@pytest.mark.parametrize("width", [0, 1, 8, 80])
def test_generic_plugin_rows_bound_width_and_sanitize_terminal_controls_preserving_unicode(width):
    snapshot = {"ui": {"plugin:status": {"kind": "status", "lines": ["海🙂\x1b[31mprivate\nline"]}}}
    rows = ExtensionView(lambda: snapshot).render(width)
    assert all(string_width(line) <= width for line in rows)
    assert "\x1b" not in "".join(rows) and "\n" not in "".join(rows)
    if width == 80:
        assert "海🙂" in "".join(rows)


def test_shared_view_clears_from_current_owner_snapshot_without_a_feature_state_machine():
    snapshot = {"ui": {"plugin:status": {"kind": "status", "lines": ["Shown"]}}}
    view = ExtensionView(lambda: snapshot)
    assert view.render(80)
    snapshot["ui"].clear()
    assert view.render(80) == []
