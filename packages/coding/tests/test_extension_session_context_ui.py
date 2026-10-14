from __future__ import annotations

from pathlib import Path

from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


def test_stale_deferred_browser_cannot_act_on_a_different_command_or_history_epoch(tmp_path):
    from unittest.mock import MagicMock
    session = MagicMock()
    session.session_id = 'current'
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    try:
        ui._editor.set_text('draft')
        ui._extension_browser = ('old-command', ui._mcp_context_key(), 'sessions')
        ui._finish_extension_browser('new-command', True)
        assert ui._navigation_panel is None and ui._extension_browser is None
        ui._extension_browser = ('command', ui._mcp_context_key(), 'tree')
        ui._output_epoch += 1
        ui._finish_extension_browser('command', True)
        assert ui._navigation_panel is None and ui._editor.get_text() == 'draft'
        session.agent.prompt.assert_not_called()
    finally:
        ui._request_exit()
