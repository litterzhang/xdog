from __future__ import annotations

from pathlib import Path

from xdog.claw.channels.tui.tui_app import ChatApp

FIXTURE = Path(__file__).parents[2] / 'agent/tests/fixtures/extensions'


def test_claw_stale_or_unnegotiated_command_artifact_never_becomes_raw_prompt_or_base64():
    from xdog.agent.extensions.command_result import ExtensionCommandResult
    from xdog.agent.extensions.tool_result import make_result
    ui = ChatApp('/tmp/unused-rich-command.sock')
    artifact = ExtensionCommandResult('rich', 'ext-rich-show', make_result(structured=[False, None]))
    try:
        ui._gateway_session_id = 'current'
        ui._editor.set_text('parent draft')
        ui._extension_pending = 'request', 'old-session'
        ui._handle_response({'type': 'extension_result', 'request_id': 'request', 'group_id': 'main',
            'success': True, 'session_id': 'old-session', 'command_content': artifact.to_dict()})
        assert not any(child.__class__.__name__ == 'ExtensionCommand' for child in ui._chat_log.children)
        ui._extension_pending = 'next', 'current'
        ui._extension_command_results_supported = False
        ui._handle_response({'type': 'extension_result', 'request_id': 'next', 'group_id': 'main',
            'success': True, 'session_id': 'current', 'command_content': artifact.to_dict()})
        assert not any(child.__class__.__name__ == 'ExtensionCommand' for child in ui._chat_log.children)
        assert ui._editor.get_text() == 'parent draft' and ui._send_queue.empty()
    finally:
        ui._request_exit()
