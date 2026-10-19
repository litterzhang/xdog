from __future__ import annotations

import time

import pytest
from xdog.tui.child_browser import child_entries, child_records
from xdog.tui.components.child_panel import ChildPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import strip_ansi


def entry(identifier="0123456789ab", task="Inspect files"):
    return {"id": identifier, "task": task, "status": "running", "access": "read-only", "calls": 1,
            "tokens": 123, "revision": 1, "settling": True}


def make():
    actions, reads, closed, output = [], [], [], []
    panel = ChildPanel(lambda *args: actions.append(args), reads.append, lambda: closed.append(True),
                       lambda *args: output.append(args), lambda: None)
    panel.focused = True
    panel.publish(child_entries({"children": [entry()]}))
    return panel, actions, reads, closed, output


def text(panel):
    return "\n".join(strip_ansi(row) for row in panel.render(80))


def test_child_form_and_cancel_require_painted_specific_confirmation():
    panel, actions, reads, closed, _ = make()
    panel.handle_input(KeyEvent("c"))
    panel.handle_input(KeyEvent("enter"))
    assert not actions
    assert "cancel · 0123456789ab" in text(panel)
    panel.handle_paste("\n")
    panel.handle_input(KeyEvent("enter", event_type=KeyEventType.REPEAT))
    assert not actions
    panel.set_render_budget(3)
    panel.render(80)
    panel.handle_input(KeyEvent("enter"))
    assert not actions
    panel.set_render_budget(8)
    text(panel)
    panel.handle_input(KeyEvent("enter"))
    assert actions == [("cancel", "0123456789ab", "", "read-only")]
    panel.publish(child_entries({"children": [entry()]}))
    panel.handle_input(KeyEvent("n"))
    panel.handle_paste("typed task")
    panel.handle_input(KeyEvent("tab"))
    panel.handle_input(KeyEvent("enter"))
    assert len(actions) == 1  # New access choice wasn't painted.
    assert "Access: inherit" in text(panel)
    panel.handle_input(KeyEvent("enter"))
    assert len(actions) == 1
    assert "Confirm child action" in text(panel)
    panel.handle_input(KeyEvent("enter"))
    assert actions[-1] == ("start", "", "typed task", "inherit")
    panel.close()


def test_roster_filter_rebinds_selection_and_removed_child_cannot_be_confirmed():
    panel, actions, reads, _, _ = make()
    panel.publish(child_entries({"children": [entry(), entry("abcdef012345", "Different task")]}))
    panel.handle_input(KeyEvent("/"))
    panel.handle_paste("Different")
    text(panel)
    panel.handle_input(KeyEvent("enter"))
    text(panel)
    panel.handle_input(KeyEvent("enter"))
    assert reads == ["abcdef012345"]
    panel.back()
    panel.handle_input(KeyEvent("x"))
    text(panel)
    panel.publish(child_entries({"children": [entry()]}))
    panel.handle_input(KeyEvent("enter"))
    assert not actions
    panel.close()


def test_preview_search_full_source_selection_and_wait_does_not_disable_cancel():
    panel, actions, reads, _, output = make()
    text(panel)
    panel.handle_input(KeyEvent("enter"))
    assert panel.preview is not None and reads == ["0123456789ab"]
    data = entry()
    data["messages"] = [{"role": "assistant", "content": [{"type": "text", "text": "needle late\n" * 80}]}]
    panel.source("0123456789ab", child_records(data))
    panel.preview.search("needle")
    deadline = time.monotonic() + 3
    while not panel.preview.settled and time.monotonic() < deadline:
        panel.poll()
        panel.render(80)
        time.sleep(0.002)
    assert panel.preview._view.matches == 80
    panel.preview.handle_input(KeyEvent("a"))
    panel.close()
    panel, actions, *_ = make()
    panel.handle_input(KeyEvent("w"))
    panel.handle_input(KeyEvent("c"))
    text(panel)
    panel.handle_input(KeyEvent("enter"))
    assert [value[0] for value in actions] == ["wait", "cancel"]
    panel.close()


def test_source_has_full_reasoning_tools_errors_and_no_binary_or_secrets():
    value = entry()
    value["messages"] = [
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "reasoning", "thinking_signature": "SIGNATURE"},
            {"type": "toolCall", "id": "call", "name": "read", "arguments": {"api_key": "SECRET", "path": "file"}}]},
        {"role": "toolResult", "tool_call_id": "call", "tool_name": "read", "is_error": True,
         "content": [{"type": "text", "text": "TAIL-MARKER"}, {"type": "image", "data": "BINARY"}]},
    ]
    value["partial"] = {"role": "assistant", "content": [{"type": "text", "text": "partial text"}]}
    records = child_records(value)
    source = "\n".join(record.body for record in records)
    assert "reasoning" in source and "TAIL-MARKER" in source and "[Tool error]" in source and "partial text" in source
    assert all(secret not in source for secret in ("SECRET", "BINARY", "SIGNATURE"))


@pytest.mark.parametrize("bad", [{"children": [{}]}, {"children": [entry(), entry()]},
                                 {"children": [{**entry(), "calls": True}]}, {"children": [{**entry(), "status": []}]}])
def test_malformed_roster_cannot_select_foreign_or_duplicate_identity(bad):
    with pytest.raises(ValueError):
        child_entries(bad)


def test_source_worker_coalesces_and_closing_never_publishes_into_successor():
    import threading

    from xdog.tui.child_browser import ChildSourceWorker
    entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
    calls, events = [], []
    def load():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return {**entry(), "messages": []}
    worker = ChildSourceWorker(lambda event: (events.append(event), delivered.set()))
    worker.request("first", "0123456789ab", "session", load)
    assert entered.wait(3)
    for index in range(20):
        worker.request(str(index), "0123456789ab", "session", load)
    release.set()
    assert delivered.wait(3)
    assert [event["id"] for event in events] == ["19"] and len(calls) == 2
    worker.close()
    worker._thread.join(3)
    assert not worker._thread.is_alive()
    worker.request("late", "0123456789ab", "successor", load)
    assert len(events) == 1


def test_live_tool_progress_is_searchable_redacted_and_never_overrides_completed_result():
    value = entry()
    value["messages"] = [{"role": "assistant", "content": [
        {"type": "toolCall", "id": "call", "name": "run", "arguments": {}}]}]
    value["live_tools"] = [{"id": "m0:p0", "name": "run", "text": "LIVE-MARKER\nAuthorization: Bearer secret-value",
                            "limited": True}]
    records = child_records(value)
    body = next(record.body for record in records if record.role == "tool")
    assert "LIVE-MARKER" in body and "final result pending" in body and "clipped" in body
    assert "secret-value" not in body
    value["messages"].append({"role": "toolResult", "tool_call_id": "call", "tool_name": "run",
                              "content": [{"type": "text", "text": "FINAL-MARKER"}]})
    body = next(record.body for record in child_records(value) if record.role == "tool")
    assert "FINAL-MARKER" in body and "LIVE-MARKER" not in body
    value["live_tools"][0]["id"] = "foreign"
    with pytest.raises(ValueError, match="canonical child"):
        child_records(value)


def test_configured_start_requires_every_settings_page_and_resize_invalidates_review():
    import json
    configured = []
    panel, actions, *_ = make()
    panel.configure = lambda value: value
    panel.configured_start = lambda *values: configured.append(values)
    panel.handle_input(KeyEvent("n"))
    panel.handle_paste("review task")
    panel.render(80)
    panel.handle_input(KeyEvent("g", ctrl=True))
    assert panel._mode == "config"
    panel.render(80)
    panel._input.set_text(json.dumps({"label": "Reviewer", "instructions": "long settings " * 50,
                                      "tools": ["filesystem"]}))
    panel.handle_input(KeyEvent("enter"))
    assert panel._mode == "form" and panel._input.get_text() == "review task"
    panel.render(80)
    panel.handle_input(KeyEvent("enter"))
    panel.render(80)
    panel.handle_input(KeyEvent("enter"))
    assert not configured and not actions
    for _ in range(30):
        panel.handle_input(KeyEvent("pagedown"))
        panel.render(80)
        if panel._visible:
            break
    assert panel._visible
    panel.set_render_budget(3)
    panel.render(32)
    panel.handle_input(KeyEvent("enter"))
    assert not configured
    panel.set_render_budget(8)
    panel.render(80)
    assert not panel._visible  # Width/row change invalidated old layout's review.
    panel.handle_input(KeyEvent("home"))
    panel.render(80)
    for _ in range(30):
        panel.handle_input(KeyEvent("pagedown"))
        panel.render(80)
        if panel._visible:
            break
    panel.handle_input(KeyEvent("enter"))
    assert configured[0][:2] == ("review task", "read-only")
    assert configured[0][2]["label"] == "Reviewer" and configured[0][2]["tools"] == ["filesystem"]
    panel.close()


def test_preset_picker_only_applies_draft_and_final_review_contains_full_expanded_settings():
    from xdog.agent.supervision_config import ChildPresets
    presets = ChildPresets({"reviewer": {"description": "Review safely", "config": {
        "instructions": "SPECIALIST-PRESET-TEXT", "tools": [], "label": "Reviewer"}}})
    panel, actions, *_ = make()
    panel.configure = lambda value: presets.resolve(value).to_dict()
    chosen = []
    panel.configured_start = lambda *args: chosen.append(args)
    panel.publish_presets(presets.snapshot())
    panel.open_presets()
    panel.handle_input(KeyEvent("enter"))
    assert panel._mode == "presets" and not chosen
    panel.render(80)
    panel.handle_input(KeyEvent("enter"))
    assert panel._mode == "form" and panel._config["preset"] == "reviewer" and not actions
    panel.handle_paste("task")
    panel.render(80)
    panel.handle_input(KeyEvent("enter"))
    assert panel._mode == "confirm"
    content = text(panel)
    panel.handle_input(KeyEvent("enter"))
    assert not chosen
    for _ in range(20):
        panel.handle_input(KeyEvent("pagedown"))
        content += text(panel)
        if panel._visible:
            break
    assert "SPECIALIST-PRESET-TEXT" in content and '"preset": "reviewer"' in content
    panel.handle_input(KeyEvent("enter"))
    assert chosen[0][0] == "task" and chosen[0][1] == "read-only"
    assert chosen[0][2]["label"] == "Reviewer" and chosen[0][2]["tools"] == []
    panel.close()


def test_pending_roster_reply_cannot_discard_submitted_task_or_bypass_final_gate():
    panel, actions, *_ = make()
    panel.handle_input(KeyEvent("n"))
    panel.handle_paste("preserve this task")
    panel.render(80)
    panel.request_list()  # A reply can still be pending after returning from settings.
    panel.handle_input(KeyEvent("enter"))
    assert panel._mode == "confirm"
    assert panel._confirm == ("start", "", "preserve this task", "read-only")
    assert "preserve this task" in text(panel)
    panel.handle_input(KeyEvent("enter"))
    assert actions == [("list", "", "", "read-only")]
    panel.publish(child_entries({"children": [entry()]}))
    panel.render(80)
    panel.handle_input(KeyEvent("enter"))
    assert actions[-1] == ("start", "", "preserve this task", "read-only")
    panel.close()


def test_canceling_child_review_restores_draft_and_advances_editing_lease():
    from xdog.tui.components.child_panel import ChildPanel
    from xdog.tui.keys import KeyEvent
    panel = ChildPanel(lambda *args: None, lambda *args: None, lambda: None,
                       lambda *args: None, lambda: None)
    panel.focused = True
    panel._selected_id = 'a' * 12
    panel._form('steer')
    old = panel.terminal_lease
    panel._input.set_text('kept\nchild draft')
    panel.render(80)
    panel.handle_input(KeyEvent('enter'))
    assert not panel.terminal_child and panel._mode == 'confirm'
    panel.handle_input(KeyEvent('escape'))
    assert panel._input.get_text() == 'kept\nchild draft' and panel.terminal_child == 'a' * 12
    assert panel.terminal_lease != old


def test_preview_literal_search_owns_l_key_instead_of_toggling_live_updates():
    panel, _actions, _reads, _closed, _outputs = make()
    try:
        panel.inspect('0123456789ab')
        assert panel.preview is not None
        before = panel._live
        panel.handle_input(KeyEvent('/'))
        assert panel.preview.searching
        for character in 'last-response':
            panel.handle_input(KeyEvent(character))
        assert panel.preview._request.query == 'last-response' and panel._live == before
        panel.handle_input(KeyEvent('enter'))
        assert not panel.preview.searching
        panel.handle_input(KeyEvent('l'))
        assert panel._live != before
    finally:
        panel.close()


def test_image_child_receipt_mismatch_preserves_exact_draft_and_closed_panel_cache():
    import base64

    from xdog.tui.attachments import attachment
    from xdog.tui.child_drafts import draft_receipt
    image = attachment(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), 'child.png')
    cache, sent = {}, []
    panel = ChildPanel(lambda *args: None, lambda *args: None, lambda: None, lambda *args: None, lambda: None,
                       image_send=lambda *args: sent.append(args), drafts=cache)
    panel.focused = True
    panel.handle_input(KeyEvent('n'))
    panel._input.set_text('reviewed task')
    panel.composer.restore((image,))
    panel.render(120)
    panel.handle_input(KeyEvent('enter'))
    for _ in range(30):
        panel.render(120)
        if panel._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel._visible
    panel.handle_input(KeyEvent('enter'))
    assert len(sent) == 1 and panel._mode == 'pending'
    panel.handle_input(KeyEvent('enter'))
    assert len(sent) == 1
    panel.complete('start', data={'accepted_input': draft_receipt('different task', (image,))})
    assert panel._mode == 'form' and panel._input.get_text() == 'reviewed task'
    assert tuple(panel.composer.items.values()) == (image,) and 'receipt' in panel._error
    panel.close()
    reopened = ChildPanel(lambda *args: None, lambda *args: None, lambda: None, lambda *args: None, lambda: None,
                          image_send=lambda *args: sent.append(args), drafts=cache)
    try:
        reopened.focused = True
        reopened.handle_input(KeyEvent('n'))
        assert reopened._input.get_text() == 'reviewed task' and tuple(reopened.composer.items.values()) == (image,)
        assert len(sent) == 1  # Reopening a retained draft does not replay uncertain admitted work.
        assert reopened.owner_token != panel.owner_token
        assert 'uncertain' in reopened._error
    finally:
        reopened.close()


def test_stopped_queue_inspection_and_roster_replies_never_adopt_a_recovery_draft():
    import base64

    from xdog.tui.attachments import attachment
    from xdog.tui.child_drafts import ChildDraft
    image = attachment(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), 'saved.png')
    saved = ChildDraft('PREEXISTING CHILD DRAFT', 'inherit', {'label': 'old'}, (image,))
    cache, sent = {('start', ''): saved}, []
    panel = ChildPanel(lambda *args: sent.append(args), lambda *_: None, lambda: None,
                       lambda *_: None, lambda: None, image_send=lambda *_: None, drafts=cache)
    panel.queue_enabled = True
    panel.focused = True
    panel.publish(child_entries({'children': [entry()]}))
    panel.open_queue()
    inspector = panel.queue
    assert inspector._busy
    panel.complete('list')
    assert inspector._busy  # A roster reply cannot settle the distinct queue metadata read.
    row = {'id': 'initial', 'content': 'SAVED BACKEND INPUT', 'delivery': 'follow_up', 'selected': False,
           'image_count': 1, 'source': 'initial-task', 'uncertain': True}
    snapshot = {'child_id': '0123456789ab', 'revision': 'a' * 64, 'stopped': True, 'inputs': [row]}
    try:
        panel.complete('queue', data={'queue': snapshot})
        assert not inspector._busy and inspector._rows == [row]
        for key in ('u', 'x', 'e', 'k', 'j'):
            panel.handle_input(KeyEvent(key))
        panel.handle_paste('CHILD EDIT MUST NOT BE APPLIED\n')
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'queue' and cache == {('start', ''): saved}
        assert sent == [('queue', '0123456789ab', '{"action": "list"}', 'read-only')]
        for receipt in ({'queue': {**snapshot, 'recovered': row}},
                        {'queue': snapshot, 'image_recovery': {'images': []}}):
            panel.complete('queue', data=receipt)
            assert cache['start', ''] is saved and panel._mode == 'queue'
        panel.complete('queue', data={'queue': snapshot}, images=(image,))
        assert 'refuses' in inspector._error and cache['start', ''] is saved
        assert not hasattr(panel, '_returned_input')
        assert not hasattr(panel, 'queue_image_recovery_enabled')
        assert not hasattr(panel, 'queue_task_recovery_enabled')
    finally:
        panel.close()


def test_readonly_child_queue_has_no_live_text_or_image_return_even_with_obsolete_flags():
    panel, actions, *_ = make()
    panel.queue_enabled = True
    panel.queue_image_return_enabled = panel.queue_image_recovery_enabled = panel.queue_task_recovery_enabled = True
    panel.open_queue()
    value = {'child_id': panel._selected_id, 'revision': 'a' * 64, 'stopped': False,
             'inputs': [{'id': 'photo', 'content': 'KEEP PHOTO', 'delivery': 'steer',
                         'selected': False, 'image_count': 1}]}
    try:
        for count in (1, 0):
            panel.queue.publish({**value, 'inputs': [{**value['inputs'][0], 'image_count': count}]})
            panel.set_render_budget(10)
            text = '\n'.join(panel.render(120))
            assert 'READ ONLY' in text and 'u return' not in text and 'e edit' not in text
            before = list(actions)
            for key in ('u', 'x', 'd', 'e', 'j', 'k', 'enter'):
                panel.handle_input(KeyEvent(key))
            assert actions == before and not panel._drafts
        assert not hasattr(panel, '_retain_return_images') and not hasattr(panel, '_returned_input')
    finally:
        panel.close()


def recover_review(panel):
    browser = panel.draft_panel
    browser.handle_input(KeyEvent('home'))
    for _ in range(80):
        panel.render(100)
        if browser._visible:
            return browser
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Transient recovery review was not fully painted')


def test_text_only_current_session_recovery_retains_exact_cursor_settings_and_never_submits(tmp_path):
    panel, actions, *_ = make()
    snapshot_file = tmp_path / 'untouched-saved-snapshot.json'
    snapshot_file.write_bytes(b'EXISTING DISK SNAPSHOT MUST NOT CHANGE')
    try:
        panel._form('start')
        panel._input.set_text('未发送 /quit\nCURRENT TASK')
        panel._input._cursor = 3
        panel._access = 'inherit'
        panel._config = {'instructions': 'local editing settings', 'tools': []}
        panel.back()
        before = panel._drafts['start', '']
        panel.open_drafts()
        browser = panel.draft_panel
        assert len(browser.source()) == 1 and browser.source()[0].current
        assert browser.source()[0].scope == panel.owner_token
        for key in ('s', 'x', 'delete', 'r', '/'):
            panel.render(100)
            panel.handle_input(KeyEvent(key))
            assert browser._review is None and not actions
        panel.render(100)
        panel.handle_input(KeyEvent('enter'))
        panel.handle_input(KeyEvent('enter'))
        panel.handle_paste('\n/quit')
        panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
        panel.handle_input(KeyEvent('enter', event_type=KeyEventType.RELEASE))
        assert not actions and panel._mode == 'drafts'
        recover_review(panel)
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'form' and panel.draft_panel is None
        assert panel._input.get_text() == before.text and panel._input._cursor == before.cursor
        assert panel._access == before.access and panel._config == before.config
        assert set(panel._drafts) == {('start', '')} and not actions
        assert snapshot_file.read_bytes() == b'EXISTING DISK SNAPSHOT MUST NOT CHANGE'
        # Reopening editing state is not a cached approval for execution.
        panel.handle_input(KeyEvent('enter'))
        assert not actions
    finally:
        panel.close()


@pytest.mark.parametrize('transition', ['back', 'new-form', 'blur-roundtrip', 'close'])
def test_transient_recovery_callback_is_owned_by_exact_current_panel_view(transition):
    panel, actions, *_ = make()
    panel._form('start')
    panel._input.set_text('KEEP CURRENT UNSENT')
    panel.open_drafts()
    browser = panel.draft_panel
    value = browser.source()[0]
    if transition == 'back':
        panel.back()
    elif transition == 'new-form':
        panel._form('start')
        panel._input.set_text('NEW EDITOR MUST SURVIVE')
    elif transition == 'blur-roundtrip':
        panel.focused = False
        panel.focused = True
    else:
        panel.close()
    try:
        with pytest.raises(ValueError, match='owner/view changed'):
            browser.act('reopen', value)
        assert not actions
        if transition == 'new-form':
            assert panel._input.get_text() == 'NEW EDITOR MUST SURVIVE'
        for action in ('copy', 'save', 'discard'):
            with pytest.raises(ValueError):
                browser.act(action, value)
    finally:
        panel.close()


def test_same_session_cache_can_reopen_under_new_view_but_old_scope_and_copy_are_refused():
    panel, actions, *_ = make()
    panel._form('start')
    panel._input.set_text('TRANSIENT SAME SESSION')
    panel.open_drafts()
    old = panel.draft_panel.source()[0]
    cache = panel._drafts
    panel.close()
    new = ChildPanel(lambda *args: actions.append(args), lambda _: None, lambda: None,
                     lambda *_: None, lambda: None, drafts=cache)
    new.focused = True
    try:
        new.open_drafts()
        current = new.draft_panel.source()[0]
        assert current.scope != old.scope
        for value, reopen in ((old, True), (current, False)):
            with pytest.raises(ValueError, match='current-session transient'):
                new.adopt_draft(value, reopen=reopen)
        new.draft_panel.render(100)
        new.handle_input(KeyEvent('enter'))
        recover_review(new)
        new.handle_input(KeyEvent('enter'))
        assert new._input.get_text() == 'TRANSIENT SAME SESSION' and not actions
        assert set(cache) == {('start', '')}
    finally:
        new.close()


@pytest.mark.parametrize('status', ['running', 'completed'])
def test_existing_child_input_recovery_requires_original_child_still_running(status):
    from xdog.tui.child_drafts import ChildDraft
    panel, actions, *_ = make()
    identifier = '0123456789ab'
    original = ChildDraft('UNSENT STEERING', cursor=5)
    panel._drafts['steer', identifier] = original
    panel.publish(child_entries({'children': [{**entry(), 'status': status}]}))
    try:
        panel.open_drafts()
        browser = panel.draft_panel
        panel.render(100)
        panel.handle_input(KeyEvent('enter'))
        recover_review(panel)
        panel.handle_input(KeyEvent('enter'))
        if status == 'running':
            assert panel._mode == 'form' and panel._form_child == identifier
            assert panel._input.get_text() == original.text and panel._input._cursor == 5
        else:
            assert 'no longer running' in browser._error and panel._mode == 'drafts'
            assert panel._drafts['steer', identifier] is original
        assert not actions
    finally:
        panel.close()


def test_child_recovery_cannot_accept_saved_or_foreign_entries_and_has_no_catalog_callbacks():
    import inspect
    from dataclasses import replace

    from xdog.tui.child_drafts import ChildDraft
    panel, actions, *_ = make()
    original = ChildDraft('CURRENT PRIVATE INPUT')
    panel._drafts['start', ''] = original
    try:
        panel.open_drafts()
        own = panel.draft_panel.source()[0]
        for value in (replace(own, scope='saved-draft:disk'), replace(own, current=False),
                      replace(own, scope='another-session'), replace(own, original=replace(original))):
            with pytest.raises(ValueError):
                panel.adopt_draft(value, reopen=True)
        assert 'draft_source' not in inspect.signature(ChildPanel).parameters
        assert 'draft_action' not in inspect.signature(ChildPanel).parameters
        assert panel._drafts['start', ''] is original and not actions
    finally:
        panel.close()


def test_queue_inspection_is_not_child_stop_consent_and_stop_still_requires_native_review():
    panel, actions, *_ = make()
    panel.queue_enabled = True
    try:
        panel.open_queue()
        panel.complete('queue', data={'queue': {'child_id': panel._selected_id, 'revision': 'a' * 64, 'inputs': []}})
        panel.render(100)
        panel.handle_input(KeyEvent('c'))
        assert len(actions) == 1 and actions[0][0] == 'queue'
        panel.handle_input(KeyEvent('escape'))
        assert panel._mode == 'list' and panel.queue is None
        panel.handle_input(KeyEvent('c'))
        panel.handle_input(KeyEvent('enter'))
        assert len(actions) == 1  # Read-only queue painting cannot approve stopping a child.
        panel.render(100)
        panel.handle_input(KeyEvent('enter'))
        assert actions[-1] == ('cancel', '0123456789ab', '', 'read-only')
    finally:
        panel.close()


def test_readonly_queue_switch_paste_and_refresh_preserve_unsent_transient_input():
    panel, actions, *_ = make()
    panel.queue_enabled = True
    panel._form('start')
    panel._input.set_text('未发送 /quit\nKEEP UNSENT')
    panel._input._cursor = 4
    panel._access, panel._config = 'inherit', {'tools': []}
    try:
        panel.open_queue()
        saved = panel._drafts['start', '']
        panel.complete('queue', data={'queue': {'child_id': panel._selected_id, 'revision': 'a' * 64, 'inputs': []}})
        for key in ('e', 'u', 'x', 'd', 'j', 'k'):
            panel.handle_input(KeyEvent(key))
        panel.handle_paste('\n/quit')
        panel.paste_clipboard()  # Read-only view cannot initiate a clipboard/image edit either.
        panel.handle_input(KeyEvent('r'))
        assert len(actions) == 2 and all(json_request[0] == 'queue' for json_request in actions)
        assert all(json_request[2] == '{"action": "list"}' for json_request in actions)
        assert panel._drafts['start', ''] is saved
        panel.back()
        panel._form('start')
        assert panel._input.get_text() == saved.text and panel._input._cursor == saved.cursor
        assert panel._access == saved.access and panel._config == saved.config
    finally:
        panel.close()


def test_replaced_queue_owner_cannot_send_or_settle_the_current_inspector():
    panel, actions, *_ = make()
    panel.queue_enabled = True
    try:
        panel.open_queue()
        old = panel.queue
        captured = old._refresh
        panel.open_queue()
        current = panel.queue
        assert old._closed and current is not old and current._busy
        with pytest.raises(ValueError, match='owner changed'):
            captured()
        old.focused = old.enabled = True
        old.refresh()
        old.publish({'child_id': panel._selected_id, 'revision': 'a' * 64, 'inputs': []})
        old.fail('Late old failure must not settle successor')
        assert current._busy and not current._error and panel.queue is current
        assert len(actions) == 2
    finally:
        panel.close()


@pytest.mark.parametrize('transition', ['close', 'blur', 'withdraw'])
def test_captured_queue_read_callback_cannot_borrow_closed_blurred_or_withdrawn_owner(transition):
    panel, actions, *_ = make()
    panel.queue_enabled = True
    panel.open_queue()
    inspector = panel.queue
    captured = inspector._refresh
    try:
        if transition == 'close':
            panel.close()
        elif transition == 'blur':
            panel.focused = False
        else:
            inspector.enabled = False
        with pytest.raises(ValueError, match='owner changed'):
            captured()
        assert len(actions) == 1
    finally:
        panel.close()


def test_child_roster_and_full_paired_history_remain_viewable_beyond_removed_count_limits():
    roster = child_entries({"children": [entry(f"{index:012x}") for index in range(25)]})
    assert len(roster) == 25
    messages = [{"role": "user", "content": "Original task"}]
    for index in range(10005):
        messages.extend([
            {"role": "assistant", "content": [{"type": "toolCall", "id": f"call-{index}",
                                               "name": "read", "arguments": {"path": f"file-{index}"}}]},
            {"role": "toolResult", "tool_call_id": f"call-{index}", "tool_name": "read",
             "content": [{"type": "text", "text": f"RESULT-{index}"}]},
        ])
    records = child_records({**entry(), "messages": messages})
    tools = [record for record in records if record.role == "tool"]
    assert len(tools) == 10005 and "RESULT-0" in tools[0].body and "RESULT-10004" in tools[-1].body
    assert tools[-1].id == "m20009:p0"
    # Removing catalog count limits must not weaken per-row identities.
    with pytest.raises(ValueError, match="identity"):
        child_entries({"children": [entry()] * 25})


@pytest.mark.parametrize("field,value", [("timeout", 120), ("max_model_calls", 16), ("max_tokens", 32000)])
def test_config_json_uses_host_validation_and_removed_budget_fields_cannot_start_work(field, value):
    import json

    from xdog.agent.supervision_config import ChildConfig
    panel, actions, *_ = make()
    configured = []
    panel.configure = lambda config: ChildConfig.from_dict(config).to_dict()
    panel.configured_start = lambda *args: configured.append(args)
    try:
        panel.handle_input(KeyEvent("n"))
        panel.handle_paste("task")
        panel.render(80)
        panel.handle_input(KeyEvent("g", ctrl=True))
        panel.render(80)
        panel._input.set_text(json.dumps({field: value}))
        panel.handle_input(KeyEvent("enter"))
        assert panel._mode == "config" and panel._error and not configured and not actions
        panel._input.set_text(json.dumps({"label": "Reviewer", "tools": []}))
        panel.handle_input(KeyEvent("enter"))
        assert panel._mode == "form" and panel._config["label"] == "Reviewer"
        assert not {"timeout", "max_model_calls", "max_tokens"} & panel._config.keys()
        assert not configured and not actions  # Configuration still cannot bypass the visible final review.
    finally:
        panel.close()
