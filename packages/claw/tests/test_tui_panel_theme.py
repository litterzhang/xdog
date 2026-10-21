"""Claw styles shared read-only browsers with its own main-UI callbacks."""
from __future__ import annotations

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp, theme_header
from xdog.tui.utils import strip_ansi, visible_width


@pytest.mark.parametrize('plain', [False, True])
def test_history_and_transcript_follow_the_claw_main_ui(monkeypatch, plain):
    monkeypatch.delenv('NO_COLOR', raising=False)
    monkeypatch.setenv('TERM', 'xterm-256color')
    monkeypatch.setenv('XDOG_TUI_THEME', 'plain' if plain else 'native')
    app = ChatApp('/tmp/unused-theme.sock')
    try:
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'first'})
        app._open_navigation()
        app._layout.render(80)
        panel = app._navigation_panel
        assert panel is not None
        rows = panel.render(80)
        assert strip_ansi(rows[0]).startswith('History ·')
        assert ('\x1b[' in ''.join(rows)) == (not plain)
        if not plain:
            assert rows[0] == theme_header(strip_ansi(rows[0]))
        app._close_navigation()
        app._open_transcript()
        app._layout.render(80)
        panel = app._transcript_panel
        assert panel is not None
        rows = panel.render(80)
        assert ('\x1b[' in ''.join(rows)) == (not plain)
        assert all(visible_width(row) <= 80 for row in rows)
        if not plain:
            assert rows[0] == theme_header(strip_ansi(rows[0]))
    finally:
        app._request_exit()
