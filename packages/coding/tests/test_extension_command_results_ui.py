from __future__ import annotations

from pathlib import Path

from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


def test_coding_stale_rich_command_cannot_append_or_change_the_new_owner(tmp_path):
    from unittest.mock import MagicMock

    from xdog.agent.extensions.command_result import ExtensionCommandResult
    from xdog.agent.extensions.tool_result import make_result
    session = MagicMock()
    session.working_dir = tmp_path
    session.session_id = 'new-session'
    ui = InteractiveMode(session)
    ui._update_footer = lambda: None
    ui._update_header = lambda: None
    try:
        ui._editor.set_text('new owner draft')
        artifact = ExtensionCommandResult('rich', 'ext-rich-show', make_result(structured=None))
        ui._handle_ui_event({'type': 'command_result', 'rich_output': artifact,
                             'owner_session': 'old-session', 'output': 'ignored', 'prompt': 'not a turn request'})
        assert not any(child.__class__.__name__ == 'ExtensionCommand' for child in ui._chat_log.children)
        assert ui._editor.get_text() == 'new owner draft'
        session.agent.prompt.assert_not_called()
    finally:
        ui._request_exit()
