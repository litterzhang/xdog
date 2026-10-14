from __future__ import annotations

import json

import pytest
from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.mcp_resource import resource_request


def envelope(uri='opaque://known/ref', argument='opaque-field', value='pre', context=None):
    return json.dumps({'uri': uri, 'argument': argument, 'value': value, 'context': context or {}})


def form(catalog=False):
    sent = []
    panel = McpPanel(lambda *args: sent.append(args), lambda: None, lambda: None, lambda: None)
    panel.focused = True
    panel.publish('list', rows=({'server': 'peer', 'state': 'connected'},))
    if catalog:
        panel.handle_input(KeyEvent('u'))
        panel.publish('resources', rows=({'name': 'opaque://known/ref', 'title': 'Opaque resource'},))
    panel.handle_input(KeyEvent('v'))
    return panel, sent


def reply(argument='opaque-field', value='pre', context=()):
    return {'server': 'peer', 'kind': 'resource', 'name': 'opaque://known/ref', 'argument': argument,
            'value': value, 'context': context, 'values': ('not-a-uri value', '第二项'), 'total': 2, 'has_more': False}


@pytest.mark.parametrize('catalog', [False, True])
def test_operator_must_supply_opaque_argument_and_selection_is_not_uri_read(catalog):
    panel, sent = form(catalog)
    try:
        assert panel._mode == 'resource_arguments'
        original = panel._arguments.get_text()
        assert json.loads(original)['argument'] == ''  # No guessed/schema-invented name.
        panel.handle_input(KeyEvent('space', ctrl=True))
        assert panel.completion is None and panel._arguments.get_text() == original
        panel._arguments.set_text(envelope())
        panel.handle_input(KeyEvent('space', ctrl=True))
        assert panel.completion.kind == 'resource'
        panel.handle_input(KeyEvent('tab'))
        assert sent[-1] == ('complete_resource', 'peer', 'opaque://known/ref',
                            {'argument': 'opaque-field', 'value': 'pre', 'context': '{}'})
        panel.publish('complete_resource', rows=(reply(),))
        panel.handle_input(KeyEvent('enter'))
        assert panel.completion is not None  # Not painted, no acceptance.
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert json.loads(panel._arguments.get_text()) == json.loads(envelope(value='not-a-uri value'))
        panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
        assert sent[-1][0] == 'complete_resource'
        panel.handle_input(KeyEvent('enter'))
        assert sent[-1] == ('resource_completion_preview', 'peer', 'opaque://known/ref',
                            {'argument': 'opaque-field', 'value': 'not-a-uri value', 'context': '{}'})
        assert not any(item[0] in ('read', 'prompt', 'connect') for item in sent)
    finally:
        panel.close()


def test_context_variable_edits_scope_cancellation_and_parent_json_are_preserved():
    panel, sent = form()
    try:
        panel._arguments.set_text(envelope(context={'language': 'python'}))
        original = panel._arguments.get_text()
        panel.handle_input(KeyEvent('space', ctrl=True))
        panel.handle_input(KeyEvent('tab'))
        panel.handle_paste('different')
        assert sent[-1][0] == 'completion_cancel'
        panel.publish('complete_resource', rows=(reply(context=(('language', 'python'),)),))
        assert not panel.completion._values
        panel.handle_input(KeyEvent('escape'))
        assert panel._arguments.get_text() == original
        panel.handle_input(KeyEvent('space', ctrl=True))
        panel._arguments.set_text(envelope(uri='opaque://changed/ref'))
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert json.loads(panel._arguments.get_text())['uri'] == 'opaque://changed/ref'
    finally:
        panel.close()


def test_invalid_manual_resource_envelope_preserves_input_without_network() -> None:

    def _check_case(data):
        panel, sent = form()
        try:
            draft = json.dumps(data)
            panel._arguments.set_text(draft)
            panel.handle_input(KeyEvent("enter"))
            assert panel._arguments.get_text() == draft
            panel.handle_input(KeyEvent("space", ctrl=True))
            assert panel.completion is None and sent == [("list", "", "", {})]
            with pytest.raises(ValueError):
                resource_request(draft)
        finally:
            panel.close()

    for data in [
        [],
        {},
        {"uri": "", "argument": "x", "value": "", "context": {}},
        {"uri": "opaque:ref", "argument": "", "value": "", "context": {}},
        {"uri": "opaque:ref", "argument": "x", "value": "", "context": {"x": "duplicate"}},
        {"uri": "opaque:ref", "argument": "x", "value": False, "context": {}},
        {"uri": "opaque:ref", "argument": "x", "value": "", "context": []},
        {"uri": "opaque:ref", "argument": "x", "value": "", "context": {"y": True}},
        {"uri": "opaque:ref", "argument": "x", "value": "", "context": {}, "extra": "bad"},
        {"uri": "opaque:ref", "argument": "x", "value": "x" * 8193, "context": {}},
    ]:
        _check_case(data)


def test_manual_unicode_context_and_opaque_uri_are_data_not_templates_or_paths():
    uri, argument, value, context = resource_request(envelope(uri='opaque://{not-expanded}/ref',
                                                           value='../../文件', context={'language': '中文'}))
    assert uri == 'opaque://{not-expanded}/ref' and argument == 'opaque-field'
    assert value == '../../文件' and context == {'language': '中文'}


def test_long_opaque_argument_json_is_visible_in_multiline_bounded_editor():
    from xdog.tui.utils import visible_width
    panel, sent = form()
    try:
        panel._arguments.set_text(envelope(value='OPAQUE-ARGUMENT-VALUE', context={'language': 'python'}))
        panel.set_render_budget(12)
        rows = panel.render(80)
        editor = ''.join(row[2:] for row in rows if row.startswith(('> ', '  ')))
        assert 'OPAQUE-ARGUMENT-VALUE' in editor
        assert 'opaque-field' in '\n'.join(rows) and 'opaque://known/ref' in '\n'.join(rows)
        for height in (0, 1, 3, 5, 8, 12):
            panel.set_render_budget(height)
            rows = panel.render(32)
            assert len(rows) <= height and all(visible_width(row) <= 32 for row in rows)
        assert sent == [('list', '', '', {})]
    finally:
        panel.close()
