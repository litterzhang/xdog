"""Native Coding browsers and the pre-runtime resume picker share the main theme."""
from __future__ import annotations

import pytest
from xdog.coding.cli.session_picker import SessionPicker
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.utils import strip_ansi, visible_width


@pytest.mark.parametrize('mode', ['native', 'dark', 'light', 'plain'])
async def test_browsers_and_resume_picker_follow_the_coding_theme(coding_session, monkeypatch, mode):
    monkeypatch.delenv('NO_COLOR', raising=False)
    monkeypatch.setenv('TERM', 'xterm-256color')
    monkeypatch.setenv('XDOG_TUI_THEME', mode)
    monkeypatch.setenv('XDOG_TUI_COLOR', 'truecolor')
    session = coding_session
    ui = InteractiveMode(session)
    try:
        ui._open_navigation()
        ui._layout.render(80)
        panel = ui._navigation_panel
        assert panel is not None
        rows = panel.render(80)
        assert rows[0] == ui._theme.header(strip_ansi(rows[0]))
        assert ('\x1b[' in ''.join(rows)) == (mode != 'plain')
        ui._close_navigation()
        ui._open_transcript()
        ui._layout.render(80)
        panel = ui._transcript_panel
        assert panel is not None
        rows = panel.render(80)
        assert rows[0] == ui._theme.header(strip_ansi(rows[0]))
        assert ('\x1b[' in ''.join(rows)) == (mode != 'plain')
        picker = SessionPicker(session.session_manager.list_sessions(limit=None), session.working_dir)
        rows = picker.render(80)
        assert rows[0] == ui._theme.header(strip_ansi(rows[0]))
        assert ('\x1b[' in ''.join(rows)) == (mode != 'plain')
        assert all(visible_width(row) <= 80 for row in rows)
    finally:
        ui._request_exit()
        await session.aclose()


async def test_no_color_also_disables_nested_panel_styles(coding_session, monkeypatch):
    monkeypatch.setenv('NO_COLOR', '1')
    monkeypatch.setenv('TERM', 'xterm-256color')
    ui = InteractiveMode(coding_session)
    try:
        ui._open_navigation()
        assert '\x1b[' not in ''.join(ui._layout.render(80))
        picker = SessionPicker(coding_session.session_manager.list_sessions(limit=None), coding_session.working_dir)
        assert '\x1b[' not in ''.join(picker.render(80))
    finally:
        ui._request_exit()
        await coding_session.aclose()
