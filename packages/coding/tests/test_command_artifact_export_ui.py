from __future__ import annotations

from pathlib import Path

from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


def test_stale_file_read_and_missing_owned_artifact_are_not_auto_exported(tmp_path):
    from unittest.mock import MagicMock

    from xdog.agent.extensions.command_result import ExtensionCommandResult
    from xdog.agent.extensions.tool_result import make_result
    session = MagicMock()
    session.working_dir = tmp_path
    session.session_id = 'current'
    ui = InteractiveMode(session)
    try:
        ui._editor.set_text('parent draft')
        artifact = ExtensionCommandResult('rich', 'ext-rich-show', make_result(structured=None))
        ui._handle_ui_event({'type': 'artifact_loaded', 'context': 'old-owner', 'artifact': artifact})
        assert not any(item.__class__.__name__ == 'ExtensionCommand' for item in ui._chat_log.children)
        ui._output_command('export', str(tmp_path / 'not-created.json') + ' artifact')
        assert not ui._outputs.pending and not (tmp_path / 'not-created.json').exists()
        assert ui._editor.get_text() == 'parent draft'
        session.agent.prompt.assert_not_called()
    finally:
        ui._request_exit()
