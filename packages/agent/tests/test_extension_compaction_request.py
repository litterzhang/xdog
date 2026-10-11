"""Compaction requests are explicit host operations, not arbitrary callback authority."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import validate_dialog
from xdog.agent.extensions.runtime import ExtensionRuntime


def dialog(**changes):
    return {'id': 'request', 'kind': 'session', 'operation': 'compact', 'title': 'Review compaction',
            'instructions': 'Keep context', **changes}


def test_compaction_request_canonical_instructions_cannot_borrow_navigation_fields():
    assert validate_dialog(dialog())['instructions'] == 'Keep context'
    for fields in ({'instructions': None}, {'instructions': ' x '}, {'instructions': '\x00'},
                   {'instructions': 'x' * 16385}, {'target': {}}, {'session_id': 'other'},
                   {'branch_id': 'other'}, {'index': 0}, {'scope': 'parent'}, {'revision': 0},
                   {'kind': 'confirm'}):
        with pytest.raises(ValueError):
            validate_dialog(dialog(**fields))


async def test_tool_and_headless_worker_cannot_borrow_command_compaction_authority(tmp_path):
    api = ExtensionAPI({})
    assert await api.request_compaction() is False
    with pytest.raises(ValueError):
        await api.request_compaction('\x00')
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/compactionrequest', root / 'compactionrequest')
    runtime = ExtensionRuntime(['compactionrequest'], root, tmp_path)
    try:
        await runtime.initialize()
        assert '"queued": false' in await runtime.command('ext-compactionrequest-request', '{}')
        result = await runtime.tools()[0].execute('call', {})
        assert result.content[0].text == 'COMPACTION-TOOL-False'
        runtime.dialog_handler = lambda *_: pytest.fail('Tool callback borrowed command authority')
        result = await runtime.tools()[0].execute('another', {})
        assert result.content[0].text == 'COMPACTION-TOOL-False'
    finally:
        await runtime.aclose()
