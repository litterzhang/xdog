from __future__ import annotations

from xdog.tui.components.extension_command import ExtensionCommand
from xdog.tui.components.messages import Transcript
from xdog.tui.utils import visible_width

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


def test_command_artifact_is_system_source_not_tool_or_model_input_and_images_are_not_base64_text():
    result = {'content': [{'type': 'text', 'text': 'READ-ONLY-COMMAND\x1b]52;bad\a'},
                          {'type': 'image', 'data': PNG, 'mimeType': 'image/png'}],
              'structuredContent': [False, 0, None], 'details': False}
    component = ExtensionCommand('ext-rich-show', result, 'READ-ONLY-COMMAND\n[false,0,null]')
    transcript = Transcript()
    transcript.add_child(component)
    records = transcript.transcript_records()
    assert records[0].role == 'system' and '[false,0,null]' in records[0].body
    assert component.result['details'] is False and len(component._images) == 1
    for width in (1, 24, 80):
        rows = component.render(width)
        assert all(visible_width(row) <= width for row in rows)
        assert PNG not in '\n'.join(rows) and '\x1b]52' not in '\n'.join(rows)
    component.set_expanded(True)
    assert '[false,0,null]' in '\n'.join(component.render(80))
    result['details'] = True
    assert component.result['details'] is False
