"""Canonical custom-context display is explicit and never opaque metadata-as-user."""
from __future__ import annotations

from xdog.tui.child_browser import message_records


def test_context_source_is_custom_labeled_content_only_and_hidden_is_absent():
    source = {'role': 'custom', 'type': 'xdog.extension_context.v1', 'data': {
        'extension': 'fixture', 'custom_type': 'note', 'display': True,
        'content': [{'type': 'text', 'text': 'VISIBLE CONTENT'}], 'details': {'raw': 'OPAQUE NOT PROSE'}}}
    records = message_records([source])
    assert len(records) == 1 and records[0].role == 'custom' and 'fixture / note' in records[0].title
    assert records[0].body == 'VISIBLE CONTENT'
    source['data']['display'] = False
    assert message_records([source]) == ()


def test_live_extension_context_control_renderer_is_removed_but_stored_history_codec_remains():
    import importlib.util

    from xdog.tui.components.extension_stack import ExtensionDialogStack
    assert importlib.util.find_spec('xdog.tui.components.extension_context_message') is None
    assert not ExtensionDialogStack.accepts({'id': 'old', 'kind': 'session', 'operation': 'context_message'}, [])
