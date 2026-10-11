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


async def test_resource_watch_notices_are_visible_over_preview_without_replacing_the_snapshot():
    from xdog.tui.components.mcp_panel import McpPanel
    from xdog.tui.utils import strip_ansi
    panel = McpPanel(lambda *args: None, lambda: None, lambda: None, lambda: None)
    panel.publish('read', text='original resource snapshot')
    panel.publish_watch({'server': 'peer', 'uri': 'test://watched', 'state': 'active', 'changes': 2})
    import asyncio
    panel.render(120)  # Width establishes the transcript indexing request.
    async with asyncio.timeout(3):
        while not panel._preview.settled:
            panel.poll()
            await asyncio.sleep(0.003)
    rendered = strip_ansi('\n'.join(panel.render(120)))
    assert '2 change notice' in rendered and 'original resource snapshot' in rendered
    assert 'no automatic read/prompt insertion' in rendered


def test_resource_watch_state_survives_catalog_refresh_and_unrelated_notice():
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    try:
        panel.publish('list', rows=({'server': 'peer', 'state': 'connected'},))
        panel.handle_input(KeyEvent('u'))
        panel.publish('resources', rows=({'name': 'test://one'}, {'name': 'test://two'}))
        panel.publish_watch({'server': 'peer', 'uri': 'test://one', 'state': 'ended', 'changes': 3})
        panel.publish_watch({'server': 'peer', 'uri': 'test://two', 'state': 'active', 'changes': 0})
        panel.publish('resources', rows=({'name': 'test://one'}, {'name': 'test://two'}))
        rows = panel.render(120)
        assert any('test://one' in row and 'watch ended' in row and '3 change notice' in row for row in rows)
        assert any('test://two' in row and 'watch active' in row for row in rows)
        panel.handle_input(KeyEvent('w'))
        assert requests[-1] == ('watch_resource', 'peer', 'test://one', {})
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
        assert requests[-1] == ('watch_catalog', 'peer', 'tools', {})
    finally:
        panel.close()


def test_combined_watch_form_validates_and_toggles_exact_canonical_filters():
    import json

    from xdog.tui.mcp_subscription import filter_text
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    try:
        panel.publish('list', rows=({'server': 'peer', 'state': 'connected'},))
        panel.handle_input(KeyEvent('w', alt=True))
        assert panel._mode == 'subscriptions'
        for invalid in ('{}', '{"toolsListChanged":1}', '{"resourceSubscriptions":["x","x"]}'):
            panel._arguments.set_text(invalid)
            panel.handle_input(KeyEvent('enter'))
            assert len(requests) == 1 and panel._arguments.get_text() == invalid
        selected = {'toolsListChanged': True, 'resourceSubscriptions': ['two', 'one']}
        draft = json.dumps(selected)
        panel._arguments.set_text(draft)
        panel.handle_input(KeyEvent('enter'))
        assert requests[-1] == ('watch_notifications', 'peer', filter_text(selected), {})
        assert panel._arguments.get_text() == draft
        panel.publish_watch({'server': 'peer', 'uri': '', 'state': 'active', 'changes': 2,
                             'notifications': selected}, receipt=True)
        assert 'no automatic read/registration/prompt insertion' in panel._message
        panel.handle_input(KeyEvent('enter'))
        assert requests[-1] == ('unwatch_notifications', 'peer', filter_text(selected), {})
        panel.publish_watch({'server': 'peer', 'uri': '', 'state': 'stopped', 'changes': 2,
                             'notifications': selected}, receipt=True)
        for height in (0, 1, 4, 12):
            panel.set_render_budget(height)
            for width in (1, 20, 80):
                rows = panel.render(width)
                assert len(rows) <= height and all(visible_width(row) <= width for row in rows)
        panel.handle_input(KeyEvent('escape'))
        assert requests[-1] == ('list', '', '', {})
    finally:
        panel.close()


def test_task_family_arm_is_separate_from_ordinary_arm_and_uses_existing_off():
    from xdog.tui.keys import KeyEventType
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    try:
        panel.publish('list', rows=({'server': 'owned', 'state': 'disconnected'},))
        for key, action in [('b', 'sampling_on'), ('f', 'elicitation_on'),
                            ('j', 'sampling_tasks_on'), ('k', 'elicitation_tasks_on')]:
            panel._busy = False
            panel.handle_input(KeyEvent(key))
            assert requests[-1] == (action, 'owned', '', {})
        count = len(requests)
        panel.handle_input(KeyEvent('j'))  # Busy gate, not duplicate enable.
        assert len(requests) == count
        panel._busy = False
        panel.handle_input(KeyEvent('j', event_type=KeyEventType.RELEASE))
        panel.handle_input(KeyEvent('k', event_type=KeyEventType.REPEAT))
        assert len(requests) == count
        panel.handle_input(KeyEvent('b', alt=True))
        assert requests[-1][0] == 'sampling_off'
        panel._busy = False
        panel.handle_input(KeyEvent('f', alt=True))
        assert requests[-1][0] == 'elicitation_off'
        assert 'j sampling tasks/k form tasks' in '\n'.join(panel.render(160))
    finally:
        panel.close()


def test_late_connection_catalog_cannot_erase_early_peer_cancel_notice():
    panel = McpPanel(lambda *args: None, lambda: None, lambda: None, lambda: None)
    try:
        panel.elicitation_closed('early-cancel')
        panel.publish('connect', rows=({'server': 'owned', 'state': 'connected'},))
        assert 'Elicitation ended' in '\n'.join(panel.render(160)) and not panel._busy
        panel.sampling_closed('early-sampling')
        panel.publish('list', rows=({'server': 'owned', 'state': 'connected'},))
        assert 'Sampling request ended' in '\n'.join(panel.render(160))
        panel.handle_input(KeyEvent('u'))
        assert not panel._review_ended_notice  # New explicit user operation supersedes notice.
    finally:
        panel.close()
