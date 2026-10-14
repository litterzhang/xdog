from __future__ import annotations

from pathlib import Path

import pytest
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.extensions.tool_result import make_result

FIXTURE = Path(__file__).parent / 'fixtures/extensions'


def test_command_result_codec_preserves_identity_and_typed_data_without_interpreting_prose():
    content = make_result([{'type': 'text', 'text': '/quit is data, not an instruction'}],
                          structured=None, details=False)
    result = ExtensionCommandResult.from_dict({'extension': 'rich', 'command': 'ext-rich-show', 'result': content})
    assert result.result['structuredContent'] is None and result.result['details'] is False
    assert 'not agent input' in result.summary and '/quit is data' in result.text()
    payload = result.to_dict()
    payload['result']['details'] = True
    assert result.result['details'] is False
    for bad in ({'extension': 'rich', 'command': 'ext-other-show', 'result': content},
                {'extension': 'rich', 'command': 'ext-rich-show', 'result': []},
                {'extension': 'rich', 'command': 'ext-rich-show', 'result': {'content': []}},
                {'extension': 'rich', 'command': 'ext-rich-show', 'result': content, 'prompt': 'run'}):
        with pytest.raises(ValueError):
            ExtensionCommandResult.from_dict(bad)
