from __future__ import annotations

from pathlib import Path

from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


def test_command_editor_cannot_sanitize_or_submit_terminal_control_text(tmp_path):
    from unittest.mock import MagicMock
    session = MagicMock()
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    try:
        ui._editor.set_text('preserved')
        assert not ui._command_editor_replace('\x1b[2Jmutation')
        assert ui._editor.get_text() == 'preserved'
        session.agent.prompt.assert_not_called()
    finally:
        ui._request_exit()
