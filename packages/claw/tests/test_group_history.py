"""The TUI timeline spans one group; execution and usage keep the live owner."""
from __future__ import annotations

import json

from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.core.persistence.session_status import read
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.tui.utils import strip_ansi


def rendered(app):
    return '\n'.join(strip_ansi(line) for line in app._chat_log.render(160))


def test_group_history_contains_sessions_handoff_and_tools_without_restoring_agents(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path)
    other = store.create_session('other')
    store.replace_transcript(other.session_id, [{'role': 'user', 'content': 'OTHER-GROUP-PRIVATE'}])
    old = store.create_session('main')
    store.replace_transcript(old.session_id, [
        {'role': 'user', 'content': 'OLD-USER token=private-value'},
        {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'reused', 'name': 'bash', 'arguments': {}}],
         'usage': {'input': 80, 'output': 20}},
        {'role': 'toolResult', 'tool_call_id': 'reused', 'tool_name': 'bash',
         'content': [{'type': 'text', 'text': 'OLD-RESULT'}]},
    ])
    current = store.reset_session('main', reason='daily_reset')
    rows = store.load_transcript(current.session_id)
    store.replace_transcript(current.session_id, [*rows,
        {'role': 'user', 'content': 'NEW-USER'},
        {'role': 'assistant', 'content': [{'type': 'toolCall', 'id': 'reused', 'name': 'filesystem', 'arguments': {}}],
         'usage': {'input': 3, 'output': 2}},
        {'role': 'toolResult', 'tool_call_id': 'reused', 'tool_name': 'filesystem',
         'content': [{'type': 'text', 'text': 'NEW-RESULT'}]},
    ])
    def no_restore(*args, **kwargs):
        raise AssertionError('Display must never restore tools/access/skills')
    monkeypatch.setattr(store, 'get_active_tools', no_restore)
    monkeypatch.setattr(store, 'get_active_skills', no_restore)
    data = read(store, 'main')
    history = json.loads(data.history_json)
    assert data.meta.session_id == current.session_id
    assert dict(data.usage)['input'] == 3 and dict(data.usage)['output'] == 2
    assert [row['session_id'] for row in history if row.get('type') == 'session_boundary'] == [
        old.session_id, current.session_id]
    assert 'OTHER-GROUP-PRIVATE' not in data.history_json and 'private-value' not in data.history_json
    assert 'daily_reset' in data.history_json and 'Context handoff' in data.history_json
    app = ChatApp('/tmp/unused-history')
    try:
        app._gateway_session_id = current.session_id
        app._replay_history_entries(history)
        text = rendered(app)
        assert text.index('OLD-RESULT') < text.index('Context handoff') < text.index('NEW-RESULT')
        handoff = next(row for row in app._chat_log.transcript_records() if 'Context handoff' in row.body)
        assert handoff.role == 'system'
        tools = list(app._history_tools.values())
        assert len(tools) == 2 and tools[0] is not tools[1]
        assert 'OLD-RESULT' in rendered_tool(tools[0]) and 'NEW-RESULT' not in rendered_tool(tools[0])
        assert app._gateway_session_id == current.session_id and app._active_run_id is None
        assert app._send_queue.empty()
    finally:
        app._request_exit()


def rendered_tool(tool):
    return '\n'.join(strip_ansi(line) for line in tool.render(160))


def test_rotation_completion_refreshes_authoritative_group_history_not_notice_owner():
    app = ChatApp('/tmp/unused-history')
    try:
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'old'})
        app._editor.set_text('unsent draft')
        notice = {'type': 'channel_input_completed', 'group_id': 'main', 'session_id': 'new',
                  'message_id': 'comment', 'source': 'xhs', 'instruction': 'NEW-COMMENT',
                  'response': 'NEW-ANSWER', 'error': '', 'status': 'succeeded'}
        app._handle_response(notice)
        app._handle_response(notice)
        request = app._send_queue.get_nowait()
        assert request['type'] == 'ping' and app._send_queue.empty()
        assert app._gateway_session_id == 'old' and app._active_run_id is None
        assert 'NEW-ANSWER' not in rendered(app)
        # Actual handshake, not the event, establishes the current execution owner.
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'current',
                              'history_count': 3, 'history_format': 2})
        app._handle_response({'type': 'history_chunk', 'entries': [
            {'role': 'system', 'content': 'Context handoff · daily_reset'},
            {'role': 'user', 'content': 'NEW-COMMENT', 'message_metadata': {'input_id': 'comment'}},
            {'role': 'assistant', 'content': 'NEW-ANSWER', 'message_metadata': {'input_id': 'comment'}},
        ]})
        app._handle_response({'type': 'history_end'})
        assert app._gateway_session_id == 'current' and app._connection_ready
        assert rendered(app).count('NEW-ANSWER') == 1 and 'Context handoff' in rendered(app)
        assert app._editor.get_text() == 'unsent draft'
    finally:
        app._request_exit()


def test_rotation_refresh_waits_for_live_run_and_reconnect_reloads_same_session():
    app = ChatApp('/tmp/unused-history')
    try:
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'old'})
        app._active_run_id = 'live'
        app._handle_response({'type': 'channel_input_completed', 'group_id': 'main', 'session_id': 'new',
                              'message_id': 'c', 'source': 'xhs', 'instruction': 'comment',
                              'response': 'answer', 'error': ''})
        app._poll()
        assert app._send_queue.empty() and app._gateway_session_id == 'old'
        app._active_run_id = None
        app._poll()
        assert app._send_queue.get_nowait()['type'] == 'ping'
        app._group_history_dirty = False
        app._handle_response({'type': '_connection_opened', 'generation': 1})
        app._chat_log.add_system('stale-view')
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'old',
                              'history_count': 1, 'history_format': 2})
        app._handle_response({'type': 'history_chunk', 'entries': [{'role': 'user', 'content': 'WHILE-OFFLINE'}]})
        app._handle_response({'type': 'history_end'})
        assert 'stale-view' not in rendered(app) and rendered(app).count('WHILE-OFFLINE') == 1
    finally:
        app._request_exit()


def test_native_turn_rotating_after_admission_refreshes_handoff_after_finishing():
    app = ChatApp('/tmp/unused-history')
    try:
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'old'})
        app._active_run_id = 'run'
        app._handle_response({'type': 'run_ack', 'run_id': 'run', 'session_id': 'old'})
        app._handle_response({'type': 'final', 'run_id': 'run', 'current_session_id': 'new'})
        assert app._gateway_session_id == 'old' and app._active_run_id is None
        app._poll()
        assert app._send_queue.get_nowait()['type'] == 'ping'
    finally:
        app._request_exit()


def test_failed_history_refresh_retains_draft_and_view_without_blind_retry():
    app = ChatApp('/tmp/unused-history')
    try:
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'old'})
        app._chat_log.add_assistant('retained answer')
        app._editor.set_text('unsent draft')
        app._group_history_dirty = True
        app._refresh_group_history()
        assert app._send_queue.get_nowait()['type'] == 'ping'
        app._handle_response({'type': 'error', 'message': 'Status unavailable: archived file missing'})
        app._poll()
        assert app._send_queue.empty() and app._connection_ready and app._session_context_stale
        assert app._gateway_session_id == 'old' and app._editor.get_text() == 'unsent draft'
        assert 'retained answer' in rendered(app) and 'Group history unavailable' in rendered(app)
    finally:
        app._request_exit()


def test_reset_ack_reloads_group_history_instead_of_leaving_a_blank_timeline():
    app = ChatApp('/tmp/unused-history')
    try:
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'old'})
        app._pending_command = 'reset'
        app._handle_response({'type': 'reset_ack', 'session_id': 'new'})
        assert app._gateway_session_id == 'new'
        assert app._send_queue.get_nowait()['type'] == 'ping'
        assert not app._connection_ready
    finally:
        app._request_exit()


def test_handoff_is_compact_by_default_but_keeps_full_details_and_context(tmp_path):
    store = TranscriptStore(tmp_path, owner_group='main')
    old = store.create_session('main')
    store.replace_transcript(old.session_id, [{'role': 'user', 'content': 'ORIGINAL-SOURCE'}])
    current = store.reset_session('main', reason='daily_reset')
    rows = store.load_transcript(current.session_id)
    rows[0]['data']['summary'] = 'LONG-HANDOFF-BODY\n' * 100
    store.replace_transcript(current.session_id, rows)
    history = json.loads(read(store, 'main').history_json)
    handoff = next(row for row in history if row.get('type') == 'handoff')
    app = ChatApp('/tmp/unused-history')
    try:
        app._replay_history_entries([handoff])
        compact = rendered(app)
        assert 'Context handoff' in compact and 'daily_reset' in compact
        assert 'Ctrl+O' in compact and 'LONG-HANDOFF-BODY' not in compact
        assert len(compact.splitlines()) < 10
        assert 'LONG-HANDOFF-BODY' in app._chat_log.detail_records()[0].body
        assert 'LONG-HANDOFF-BODY' in app._chat_log.transcript_records()[0].body
        app._chat_log.set_details_expanded(True)
        assert 'LONG-HANDOFF-BODY' in rendered(app)
        app._chat_log.set_details_expanded(False)
        assert 'LONG-HANDOFF-BODY' not in rendered(app)
        assert store.load_transcript(current.session_id) == rows
    finally:
        app._request_exit()
