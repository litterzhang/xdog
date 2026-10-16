from __future__ import annotations

import time

from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import visible_width


def test_management_catalog_preview_and_draft_action_require_explicit_gestures():
    requests, closed, inserted, exported = [], [], [], []
    panel = McpPanel(lambda *args: requests.append(args), lambda: closed.append(True),
                     lambda: inserted.append(True), lambda: exported.append(True))
    try:
        assert requests == [("list", "", "", {})]
        panel.publish("list", rows=({"server": "fixture", "state": "connected"},))
        panel.handle_input(KeyEvent("u"))
        assert requests[-1] == ("resources", "fixture", "", {})
        panel.publish("resources", rows=({"name": "fixture://text", "title": "document"},))
        panel.handle_input(KeyEvent("enter"))
        assert requests[-1] == ("read", "fixture", "fixture://text", {})
        panel.publish("read", text="untrusted payload\n" + "x" * 200000 + "TAIL")
        panel.handle_paste("ignored outside arguments/search")
        assert not inserted and not exported
        deadline = time.monotonic() + 3
        while panel._preview.browser.snapshot().busy and time.monotonic() < deadline:
            panel.poll()
            time.sleep(0.002)
        for height in (0, 1, 2, 6, 12):
            panel.set_render_budget(height)
            rows = panel.render(32)
            assert len(rows) <= height and all(visible_width(row) <= 32 for row in rows)
        panel.handle_input(KeyEvent("i"))
        assert inserted == [True]
        panel.handle_input(KeyEvent("e"))
        assert exported == [True]
        panel.handle_input(KeyEvent("escape"))
        assert requests[-1][0] == "list" and not closed
        panel.handle_input(KeyEvent("escape"))
        assert closed == [True]
    finally:
        panel.close()


def test_prompt_argument_paste_retrieves_preview_never_sends_model_input():
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    try:
        panel.publish("list", rows=({"server": "fixture", "state": "connected"},))
        panel.handle_input(KeyEvent("p"))
        panel.publish("prompts", rows=({"name": "review", "title": "review", "arguments": (("topic", "", True),)},))
        panel.handle_input(KeyEvent("enter"))
        panel._arguments.set_text('{"topic":"user-selected"}')
        panel.handle_input(KeyEvent("enter"))
        assert requests[-1] == ("prompt", "fixture", "review", {"topic": "user-selected"})
    finally:
        panel.close()


def test_template_requires_explicit_resolved_uri_and_argument_submit_is_busy_gated():
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    try:
        panel.publish("list", rows=({"server": "fixture", "state": "connected"},))
        panel.handle_input(KeyEvent("t"))
        panel.publish("templates", rows=({"server": "fixture", "name": "fixture://document/{name}"},))
        assert "fixture://document/{name}" in "\n".join(panel.render(120))
        panel.handle_input(KeyEvent("enter"))
        assert panel._mode == "uri"
        panel.handle_input(KeyEvent("enter"))
        assert requests[-1][0] == "templates"  # Unresolved expressions aren't fetched.
        panel._arguments.set_text("fixture://document/user-selected")
        panel.handle_input(KeyEvent("enter"))
        assert requests[-1] == ("read", "fixture", "fixture://document/user-selected", {})
        count = len(requests)
        panel.handle_input(KeyEvent("enter"))
        assert len(requests) == count
    finally:
        panel.close()


def test_retired_watch_controls_are_absent_and_manual_catalog_refresh_is_explicit():
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    try:
        assert not hasattr(panel, 'publish_watch') and not hasattr(panel, '_subscription_form')
        panel.publish('list', rows=({'server': 'peer', 'state': 'connected'},))
        for event in (KeyEvent('w'), KeyEvent('w', alt=True), KeyEvent('t', alt=True),
                      KeyEvent('p', alt=True), KeyEvent('u', alt=True)):
            panel.handle_input(event)
        assert requests == [('list', '', '', {})]
        panel.handle_input(KeyEvent('u'))
        panel.publish('resources', rows=({'name': 'test://one'}, {'name': 'test://two'}))
        panel.handle_input(KeyEvent('r'))
        assert requests[-1] == ('resources', 'peer', '', {})
        panel.publish('resources', rows=({'name': 'test://one'}, {'name': 'test://two'}))
        for height in (0, 1, 2, 6):
            panel.set_render_budget(height)
            for width in (1, 16, 32):
                rows = panel.render(width)
                assert len(rows) <= height and all(visible_width(row) <= width for row in rows)
    finally:
        panel.close()


def test_server_filter_does_not_hide_catalog_entries_or_preview_controls():
    requests, inserted = [], []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None,
                     lambda: inserted.append(True), lambda: None)
    try:
        panel.publish('list', rows=({'server': 'peer', 'state': 'connected'},))
        panel.handle_input(KeyEvent('/'))
        panel.handle_paste('peer')
        panel.handle_input(KeyEvent('enter'))
        panel.handle_input(KeyEvent('u'))
        panel.publish('resources', rows=({'name': 'test://document'},))
        assert panel._query == '' and 'test://document' in '\n'.join(panel.render(120))
        panel.handle_input(KeyEvent('/'))
        panel.handle_paste('document')
        panel.handle_input(KeyEvent('enter'))
        panel.handle_input(KeyEvent('enter'))
        assert requests[-1] == ('read', 'peer', 'test://document', {})
        panel.publish('read', text='payload')
        assert panel._query == '' and not panel._filtering
        panel.handle_input(KeyEvent('i'))
        assert inserted == [True]
    finally:
        panel.close()


def test_refresh_retains_filtered_selection_by_identity_not_position():
    panel = McpPanel(lambda *args: None, lambda: None, lambda: None, lambda: None)
    try:
        rows = tuple({'server': name, 'state': 'connected'} for name in ('peer-a', 'peer-b', 'other'))
        panel.publish('list', rows=rows)
        panel.handle_input(KeyEvent('/'))
        panel.handle_paste('peer')
        panel.handle_input(KeyEvent('enter'))
        panel.handle_input(KeyEvent('down'))
        panel.publish('refresh', rows=(rows[1], rows[2], rows[0]))
        assert panel._query == 'peer'
        assert panel._visible()[panel._selected]['server'] == 'peer-b'
        panel.publish('refresh', rows=(rows[0],))
        assert panel._selected == 0
        panel.publish('refresh', rows=())
        assert panel._selected == 0
    finally:
        panel.close()


def test_modified_keys_do_not_trigger_plain_management_actions():
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    try:
        panel.publish('list', rows=({'server': 'peer', 'state': 'connected'},))
        for key in ('r', 'd', 'p', 'u', 'v'):
            panel.handle_input(KeyEvent(key, ctrl=True))
        panel.handle_input(KeyEvent('r', alt=True))
        panel.handle_input(KeyEvent('t', alt=True, ctrl=True))
        assert requests == [('list', '', '', {})]
        panel.handle_input(KeyEvent('t', alt=True))
        assert requests == [('list', '', '', {})]
    finally:
        panel.close()


def test_subscription_codec_is_removed_and_stale_controls_preserve_successor_state():
    import importlib

    import pytest
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module('xdog.tui.mcp_subscription')
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    try:
        panel.publish('list', rows=({'server': 'peer', 'state': 'connected'},))
        panel.handle_input(KeyEvent('p'))
        panel._message = 'current prompt catalog pending'
        for action in ('watch_resource', 'unwatch_resource', 'watch_catalog', 'unwatch_catalog',
                       'watch_notifications', 'unwatch_notifications'):
            panel.request(action)
            panel.publish(action, text='stale preview', error='obsolete result')
            assert panel._busy and panel._mode == 'servers' and panel._preview is None
            assert panel._message == 'current prompt catalog pending'
        assert requests == [('list', '', '', {}), ('prompts', 'peer', '', {})]
        panel.publish('prompts', rows=({'name': 'review'},))
        assert not panel._busy and panel._mode == 'catalog'
    finally:
        panel.close()


def test_retired_task_form_and_root_shortcuts_are_inert_and_review_apis_are_absent():
    from xdog.tui.keys import KeyEventType
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    try:
        panel.publish('list', rows=({'server': 'owned', 'state': 'disconnected'},))
        for attribute in ('publish_elicitation', 'elicitation_closed', 'elicitation_review', 'publish_roots',
                          'roots_review', '_root_form', 'publish_sampling', 'sampling_closed'):
            assert not hasattr(panel, attribute)
        for event_type in KeyEventType:
            for event in (KeyEvent('f', event_type=event_type), KeyEvent('k', event_type=event_type),
                          KeyEvent('o', event_type=event_type), KeyEvent('f', alt=True, event_type=event_type)):
                panel.handle_input(event)
                assert not panel._busy and panel._mode == 'servers' and panel._preview is None
        assert requests == [('list', '', '', {})]
        rendered = '\n'.join(panel.render(160))
        assert 'elicitation' not in rendered.lower() and 'tasks' not in rendered.lower()
        panel.handle_input(KeyEvent('p'))
        assert requests[-1] == ('prompts', 'owned', '', {})
    finally:
        panel.close()


def test_retired_results_cannot_unlock_or_replace_successor_mcp_operation():
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    try:
        panel.publish('list', rows=({'server': 'owned', 'state': 'connected'},))
        panel.handle_input(KeyEvent('p'))
        assert panel._busy and panel._mode == 'servers'
        panel._message = 'successor prompt catalog pending'
        for action in ('elicitation_answer', 'elicitation_on', 'elicitation_tasks_on', 'sampling_answer',
                       'roots_prepare', 'roots_commit'):
            panel.publish(action, text='stale untrusted preview', error='retired capability unsupported')
            assert panel._busy and panel._mode == 'servers' and panel._preview is None
            assert panel._message == 'successor prompt catalog pending'
        assert requests == [('list', '', '', {}), ('prompts', 'owned', '', {})]
        panel.publish('prompts', rows=({'name': 'review', 'arguments': (('topic', '', True),)},))
        assert not panel._busy and panel._mode == 'catalog'
        panel.handle_input(KeyEvent('enter'))
        panel._arguments.set_text('{"topic":"explicit successor"}')
        panel.handle_input(KeyEvent('enter'))
        assert requests[-1] == ('prompt', 'owned', 'review', {'topic': 'explicit successor'})
    finally:
        panel.close()
