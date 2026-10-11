"""Reviewed message text is provenance-labelled, bounded and not command authority."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import validate_dialog
from xdog.agent.extensions.message_request import host_message
from xdog.agent.extensions.runtime import ExtensionRuntime


def dialog(**changes):
    return {'id': 'request', 'kind': 'session', 'operation': 'message', 'title': 'Review message',
            'text': '  /quit\n', **changes}


def test_message_request_preserves_literal_text_without_borrowing_scope_or_images():
    assert validate_dialog(dialog())['text'] == '  /quit\n'
    assert host_message('sample', '  /quit\n') == '[User-approved message requested by extension sample]\n\n  /quit\n'
    for fields in ({'text': None}, {'text': '  '}, {'text': '\x00'}, {'text': 'x' * 16385},
                   {'target': {}}, {'session_id': 'other'}, {'branch_id': 'other'}, {'index': 0},
                   {'scope': 'parent'}, {'revision': 0}, {'images': None}, {'image_refs': []},
                   {'origin': 'user'}, {'delivery': 'steer'}, {'kind': 'confirm'}):
        with pytest.raises(ValueError):
            validate_dialog(dialog(**fields))


async def test_tool_and_headless_worker_cannot_borrow_parent_command_message_authority(tmp_path):
    api = ExtensionAPI({})
    assert await api.request_message('/quit') is False
    with pytest.raises(ValueError):
        await api.request_message('')
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/messagerequest', root / 'messagerequest')
    runtime = ExtensionRuntime(['messagerequest'], root, tmp_path)
    try:
        await runtime.initialize()
        assert '"queued": false' in await runtime.command('ext-messagerequest-request', '{}')
        runtime.dialog_handler = lambda *_: pytest.fail('Tool callback borrowed command authority')
        result = await runtime.tools()[0].execute('another', {})
        assert result.content[0].text == 'MESSAGE-TOOL-False'
    finally:
        await runtime.aclose()
