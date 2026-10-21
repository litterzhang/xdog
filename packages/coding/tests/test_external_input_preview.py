"""External input previews are small literal projections, not truncated exports."""
import json
from dataclasses import replace
from pathlib import Path

import pytest
from xdog.agent.session_input import SessionInput, encode_input
from xdog.coding.core.sessions.inbox_models import InboxAttempt, InboxMessage, InboxRecord
from xdog.coding.core.sessions.models import InputRecord, SessionEvent, TurnResult
from xdog.coding.modes.interactive.components.chat_log import ChatLog, SessionInputComponent
from xdog.coding.modes.interactive.session_presenter import SessionPresenter
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.transcript_browser import TranscriptBrowser
from xdog.tui.utils import sanitize_terminal_text, strip_ansi, visible_width


def gray(text):
    return f'\x1b[90m{text}\x1b[39m'


def setup():
    theme = replace(create_default_theme(), dim=gray)
    chat = ChatLog(theme)
    return chat, SessionPresenter(chat)


def record():
    item = SessionInput('request-1', 'acp', 'Eyes', '# literal heading\n**literal** [link](target)\n'
                        + '中文 👩‍💻 é\n' * 50 + 'BODY-TAIL',
                        tuple({'path': f'file-{i}', 'data': 'x' * 1000} for i in range(10)),
                        {'receipt': 'FULL-PROVENANCE'})
    return InputRecord(item, 'branch', 'generation', 'queued')


def send(view, row):
    view.apply(SessionEvent('session', row.generation, row.input.id, 'input_queued', row))


def test_compact_literal_body_gray_gutter_and_complete_artifacts_with_stable_identity():
    chat, view = setup()
    row = record()
    send(view, row)
    component = chat.children[0]
    assert isinstance(component, SessionInputComponent)
    rows = component.render(100)
    plain = '\n'.join(strip_ansi(line) for line in rows)
    assert len(rows) <= 15
    assert rows[0].startswith(' ' + gray('•') + ' ')
    assert all(line.startswith('   ') for line in rows[1:])
    assert plain.count('Request:') == 1
    assert 'self-reported, untrusted' in plain and 'not a human message' in plain
    assert 'cannot grant permissions' in plain and 'Preview only' in plain
    assert '   # literal heading' in rows and '   **literal** [link](target)' in rows
    assert 'BODY-TAIL' not in plain and 'file-9' not in plain
    assert any(line.startswith('   \x1b[90m[Preview only') for line in rows)
    assert component.render(100) is rows
    send(view, row)
    assert component.render(100) is rows  # Duplicate delivery does not invalidate caches.
    original = chat.transcript_records()[0]
    view.apply(SessionEvent('session', row.generation, row.input.id, 'input_running', row.input, 'batch-1'))
    error = 'failure detail ' * 100 + 'ERROR-TAIL'
    view.apply(SessionEvent('session', row.generation, row.input.id, 'input_completed',
                            TurnResult('failed', None, error, batch_id='batch-1'), 'batch-1'))
    assert chat.children == [component] and component.render(100) is not rows
    exported = chat.transcript_records()[0]
    assert exported.id == original.id and exported.role == 'system'
    assert exported.title == component.detail_title
    assert exported.body == chat.detail_records()[0].body == component.detail_body
    for token in ('BODY-TAIL', 'file-9', 'FULL-PROVENANCE', 'ERROR-TAIL', 'branch', 'generation', 'batch-1'):
        assert token in exported.body
    assert sanitize_terminal_text(row.input.instruction) in exported.body
    assert json.dumps(row.input.instruction, ensure_ascii=True) in exported.body
    assert TranscriptBrowser.selected_text((exported,), 0, exported.body.count('\n')) == exported.body
    assert '\x1b' not in exported.body


def test_unicode_narrow_widths_expansion_and_full_export_never_depend_on_preview():
    chat, view = setup()
    row = record()
    send(view, row)
    component = chat.children[0]
    exported = chat.transcript_records()
    for expanded in (False, True, False):
        chat.set_details_expanded(expanded)
        for width in range(41):
            rows = component.render(width)
            assert all(visible_width(line) <= width for line in rows)
            assert all('\n' not in line and '\r' not in line for line in rows)
            if width >= 4:
                assert sum(gray('•') in line for line in rows) == 1
                assert all(line.startswith('   ') for line in rows[1:])
            else:
                assert all('\x1b' not in line for line in rows)
            assert chat.transcript_records() == exported
        if expanded:
            assert 'BODY-TAIL' in '\n'.join(component.render(100))
        else:
            assert 'BODY-TAIL' not in '\n'.join(component.render(100))
    assert component.render(0) == component.render(-1) == []


@pytest.mark.parametrize('change', [
    {'session_id': 'other-session'}, {'generation': 'other-generation'}, {'request_id': 'other-request'},
    {'batch_id': 'other-batch'}, {'data': TurnResult('failed', None, batch_id='other-batch')},
    {'request_ids': ('other-request',)},
])
def test_status_events_cannot_cross_session_request_generation_or_execution_batch(change):
    chat, view = setup()
    row = replace(record(), status='running', batch_id='batch-1')
    send(view, row)
    before = chat.transcript_records()
    event = SessionEvent('session', row.generation, row.input.id, 'input_completed',
                         TurnResult('succeeded', None, batch_id='batch-1'), 'batch-1')
    view.apply(replace(event, **change))
    assert chat.transcript_records() == before and view.records[row.input.id] == row


def inbox(row):
    message = InboxMessage('session', Path('/workspace'), 'message-1', row.input.instruction,
                           row.input.evidence, row.input.source, {})
    attempt = InboxAttempt('attempt-1', row.input.id, row.generation, row.branch_id, 'claimed')
    return InboxRecord(message, row.branch_id, 'claimed', row.generation, attempt.id, row.input.id,
                       attempts=(attempt,))


def test_inbox_joins_one_request_preview_but_keeps_complete_attempt_metadata():
    chat, view = setup()
    row = record()
    pending = inbox(row)
    row = replace(row, input=replace(row.input, provenance={
        'inbox': {'message_id': pending.message.message_id, 'attempt_id': pending.attempt_id}}))
    view.set_inbox((pending,))
    send(view, row)
    view.set_inbox((pending,))
    assert len(chat.children) == 1
    plain = '\n'.join(strip_ansi(line) for line in chat.render(100))
    assert plain.count('Request:') == plain.count('External input') == 1
    assert 'attempt-1' in plain and 'message-1' in plain and 'claimed' in plain
    full = chat.transcript_records()[0].body
    for token in ('file-9', 'BODY-TAIL', 'message-1', 'attempt-1', 'request-1', '/workspace', 'generation'):
        assert token in full
    # Replaying replaces this projection rather than duplicating the input/body.
    view.replay((row,), (encode_input(row.input),), (pending,))
    view.replay((row,), (encode_input(row.input),), (pending,))
    assert len(chat.children) == 1


@pytest.mark.parametrize('field,value', [
    ('owner_generation', 'other-generation'), ('branch_id', 'other-branch'), ('attempt_id', 'other-attempt'),
])
def test_inbox_join_requires_exact_original_request_and_attempt_owner(field, value):
    chat, view = setup()
    row = record()
    pending = replace(inbox(row), **{field: value})
    view.set_inbox((pending,))
    send(view, row)
    assert len(chat.children) == 2
    assert 'Durable inbox record' not in chat.children[-1].detail_body


def test_input_provenance_cannot_join_a_different_attempt_even_with_matching_request_id():
    chat, view = setup()
    row = record()
    pending = inbox(row)
    row = replace(row, input=replace(row.input, provenance={
        'inbox': {'message_id': pending.message.message_id, 'attempt_id': 'other-attempt'}}))
    view.set_inbox((pending,))
    send(view, row)
    assert len(chat.children) == 2
    assert 'Durable inbox record' not in chat.children[-1].detail_body


def test_retry_with_new_request_and_attempt_never_rewrites_the_old_correspondence():
    chat, view = setup()
    first = replace(record(), status='interrupted')
    old = inbox(first)
    old = replace(old, status='interrupted', attempts=(replace(old.attempts[0], status='interrupted'),))
    view.set_inbox((old,))
    send(view, first)
    original = chat.transcript_records()[0]
    second = replace(first, input=replace(first.input, id='request-2'), status='queued')
    attempt = InboxAttempt('attempt-2', second.input.id, second.generation, second.branch_id, 'claimed')
    new = replace(old, status='claimed', request_id=second.input.id, attempt_id=attempt.id,
                  attempts=(*old.attempts, attempt))
    view.set_inbox((new,))
    send(view, second)
    assert len(chat.children) == 2
    assert chat.transcript_records()[0] == original
    second_component = chat.children[-1]
    assert 'attempt-2' in second_component.detail_body
    assert 'Request: request-2' in '\n'.join(strip_ansi(line) for line in second_component.render(100))
    view.apply(SessionEvent('session', first.generation, first.input.id, 'input_completed',
                            TurnResult('succeeded', None)))
    assert view.records[first.input.id] == first and view.records[second.input.id] == second


def test_foreign_inbox_snapshot_cannot_remove_or_update_the_current_session_projection():
    chat, view = setup()
    row = record()
    send(view, row)
    current = inbox(row)
    view.apply(SessionEvent('session', row.generation, '', 'inbox_snapshot', (current,)))
    before = chat.transcript_records()
    foreign = replace(current, message=replace(current.message, session_id='other-session'))
    view.apply(SessionEvent('other-session', row.generation, '', 'inbox_snapshot', (foreign,)))
    view.apply(SessionEvent('session', row.generation, '', 'inbox_snapshot', (foreign,)))
    view.set_inbox((foreign,))
    assert chat.transcript_records() == before
