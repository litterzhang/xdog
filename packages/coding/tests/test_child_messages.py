"""Live correspondence is a UI projection, never another input/execution loop."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillRepo
from xdog.ai.types import AssistantMessage, TextContent, ThinkingContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionRequest
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.child_messages import ChildMessage, ChildMessagePresenter
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.keys import KeyEvent
from xdog.tui.transcript_browser import TranscriptBrowser
from xdog.tui.utils import strip_ansi, visible_width


class Supervisor:
    def __init__(self):
        self.listeners = []
        self.removed = []

    def subscribe(self, receive):
        self.listeners.append(receive)
        def remove():
            self.removed.append(receive)
            self.listeners.remove(receive)
        return remove


def fact(**changes):
    return {'type': 'child_message', 'child_id': 'child-1', 'message_id': 'stable-id',
            'direction': 'parent_to_child', 'text': 'Task knowledge', 'status': 'admitted', **changes}


def presenter():
    chat, queue, child_runtime = ChatLog(create_default_theme()), [], Supervisor()
    view = ChildMessagePresenter(chat, queue.append)
    owner = ('session', 'branch', 'original-child-token')
    view.bind(child_runtime, owner)
    return view, chat, queue, child_runtime, owner


def test_event_only_deduplication_and_complete_details_even_after_many_polls():
    view, chat, queue, child_runtime, owner = presenter()
    for _ in range(10):
        view.bind(child_runtime, owner)
        child_runtime.listeners[0]({'type': 'child_status', 'snapshot': {'messages': ['NOT A REPLY']}})
        child_runtime.listeners[0](fact())
    assert len(child_runtime.listeners) == 1
    assert sum(view.display(event, owner) for event in queue) == 1
    message = chat.children[0]
    assert isinstance(message, ChildMessage)
    assert 'Parent → child child-1' in message.detail_title and 'admitted' in message.detail_title
    assert chat.detail_records()[0].body == 'Task knowledge'
    assert 'Task knowledge' in chat.transcript_records()[0].body
    view.close()
    assert not child_runtime.listeners


@pytest.mark.parametrize('status', ['received', 'completed', 'error', 'aborted'])
def test_child_reply_preview_discloses_bound_and_keeps_tail_and_terminal_safety(status):
    view, chat, queue, child_runtime, owner = presenter()
    body = 'First line\n' + 'findings\n' * 100 + 'FULL-TAIL\x1b]0;bad\x07'
    child_runtime.listeners[0](fact(direction='child_to_parent', status=status, text=body, label='Reviewer'))
    assert view.display(queue.pop(), owner)
    message = chat.children[0]
    preview = '\n'.join(message.render(44))
    assert 'Preview only' in preview and 'FULL-TAIL' not in preview
    assert 'Reviewer' in message.detail_title and '→ Parent' in message.detail_title
    assert 'FULL-TAIL' in chat.detail_records()[0].body
    assert '\x1b' not in chat.detail_records()[0].body
    assert 'FULL-TAIL' in chat.transcript_records()[0].body
    message.set_expanded(True)
    assert 'FULL-TAIL' in '\n'.join(message.render(44))
    view.close()


def test_original_owner_branch_and_subscription_lease_fence_late_events_without_cancellation():
    view, chat, queue, child_runtime, owner = presenter()
    old_receive = child_runtime.listeners[0]
    old_receive(fact())
    successor = (*owner[:1], 'new-branch', owner[2])
    assert not view.display(queue[0], successor)
    view.bind(child_runtime, successor)
    old_receive(fact(message_id='late'))
    assert all(not view.display(event, successor) for event in queue)
    assert not chat.children and len(child_runtime.removed) == 1
    child_runtime.listeners[0](fact(message_id='new'))
    assert view.display(queue[-1], successor)
    view.close()
    old_receive(fact(message_id='after-close'))
    assert not view.display(queue[-1], successor)
    assert len(chat.children) == 1 and not child_runtime.listeners


@pytest.mark.parametrize('change', [
    {'status': 'queued'}, {'status': 'rejected'}, {'direction': 'toolResult'}, {'message_id': ''},
    {'text': {'thinking': 'NO'}}, {'child_id': None}, {'direction': 'child_to_parent', 'status': 'streaming'},
])
def test_non_admitted_malformed_and_partial_messages_are_not_correspondence(change):
    view, chat, queue, child_runtime, owner = presenter()
    child_runtime.listeners[0](fact(**change))
    assert not view.display(queue[0], owner)
    assert not chat.children
    view.close()



@pytest.mark.parametrize('direction,status', [('parent_to_child', 'admitted'), ('child_to_parent', 'received')])
def test_correspondence_shares_columns_with_gray_metadata_and_accent_direction(direction, status):
    def gray(text):
        return f'\x1b[90m{text}\x1b[39m'

    def accent(text):
        return f'\x1b[36m{text}\x1b[39m'

    theme = replace(create_default_theme(), dim=gray, accent=accent)
    chat, queue, runtime = ChatLog(theme), [], Supervisor()
    view = ChildMessagePresenter(chat, queue.append)
    owner = ('session', 'branch', 'token')
    view.bind(runtime, owner)
    try:
        chat.add_user('question')
        chat.add_assistant('answer')
        chat.add_tool('bash', {'command': 'pwd'})
        runtime.listeners[0](fact(direction=direction, status=status, text='first line\nsecond line'))
        assert view.display(queue.pop(), owner)
        message = chat.children[-1]
        rows = message.render(160)
        assert [strip_ansi(row) for row in rows] == [' • ' + message.detail_title,
                                                    '   first line', '   second line']
        assert rows[1:] == ['   first line', '   second line']
        assert gray(' · ' + status + ' · message stable-id') in rows[0]
        assert gray(' child-1' if direction == 'parent_to_child' else 'child child-1') in rows[0]
        assert accent('Parent → child' if direction == 'parent_to_child' else ' → Parent') in rows[0]
        assert sum(gray('•') in row for row in rows) == 1
        # All roles share the existing gutter; no parent/child runtime resources change.
        headers = [strip_ansi(row) for row in chat.render(160) if strip_ansi(row).startswith((' › ', ' • '))]
        assert len(headers) == 4
        assert all(row[0] == row[2] == ' ' and row[3] != ' ' for row in headers)
    finally:
        view.close()


@pytest.mark.parametrize('mode', ['native', 'dark', 'light', 'plain'])
def test_presenter_uses_current_chat_theme_including_plain_and_no_color(monkeypatch, mode):
    monkeypatch.setenv('XDOG_TUI_THEME', mode)
    monkeypatch.setenv('TERM', 'xterm-256color')
    monkeypatch.delenv('NO_COLOR', raising=False)
    view, chat, queue, runtime, owner = presenter()
    try:
        runtime.listeners[0](fact())
        assert view.display(queue.pop(), owner)
        row = chat.children[0].render(160)[0]
        assert row.startswith(' ' + chat._theme.dim('•') + ' ' + chat._theme.accent('Parent → child'))
        assert ('\x1b' in row) == (mode != 'plain')
    finally:
        view.close()
    monkeypatch.setenv('NO_COLOR', '1')
    view, chat, queue, runtime, owner = presenter()
    try:
        runtime.listeners[0](fact())
        assert view.display(queue.pop(), owner)
        assert chat.children[0].render(160)[0].startswith(' • Parent')
    finally:
        view.close()


@pytest.mark.parametrize('expanded', [False, True])
def test_wrapped_long_titles_body_and_preview_notice_share_the_gutter(expanded):
    label = 'Reviewer-' + 'LONG-TITLE-' * 12
    message = ChildMessage(fact(label=label, text='abcdefghijklmnop\n' + 'findings\n' * 10), 'owner')
    message.set_expanded(expanded)
    rows = message.render(10)
    assert rows[0].startswith(' • ')
    assert all(row.startswith('   ') for row in rows[1:])
    text = ''.join(row[3:] for row in rows)
    # Wrapped headers retain every identity/status character, not an ellipsis.
    def compact(value):
        return ''.join(value.split())

    assert compact(message.detail_title) in compact(text)
    assert '   abcdefg' in rows and '   hijklmn' in rows
    assert ('Previewonly' in compact(text)) == (not expanded)
    assert all(visible_width(row) <= 10 for row in rows)


def test_tiny_unicode_widths_have_no_gutter_overflow_or_ansi_fragments():
    def gray(text):
        return f'\x1b[90m{text}\x1b[39m'

    message = ChildMessage(fact(label='审查 👩‍💻', text='中文 👩‍💻 é\n\nnext line'), 'owner',
                           marker_style=gray, metadata_style=gray, direction_style=gray)
    for expanded in (False, True):
        message.set_expanded(expanded)
        for width in range(41):
            rows = message.render(width)
            assert all(visible_width(row) <= width for row in rows)
            assert all('\n' not in row and '\r' not in row for row in rows)
            if width >= 4:
                assert sum(gray('•') in row for row in rows) == 1
                assert all(row.startswith('   ') for row in rows[1:])
            else:
                assert all('\x1b' not in row for row in rows)
    assert message.render(0) == message.render(-1) == []


def test_literal_markdown_and_complete_source_exports_ignore_rendering_and_expansion():
    body = '# literal heading\n**not bold** _not italic_ [link](target)\n```python\n  print("raw")\n```\n'
    body += '\n'.join(f'full finding {index}' for index in range(30)) + '\nFULL-TAIL'
    view, chat, queue, runtime, owner = presenter()
    try:
        runtime.listeners[0](fact(text=body, label='**literal reviewer**'))
        assert view.display(queue.pop(), owner)
        message = chat.children[0]
        for expanded in (False, True):
            message.set_expanded(expanded)
            for width in (4, 10, 80, 160):
                message.render(width)
                assert message.detail_body == body
                assert chat.detail_records()[0].body == body
                records = chat.transcript_records()
                assert records[0].body == body and records[0].title == message.detail_title
                assert TranscriptBrowser.selected_text(records, 0, body.count('\n')) == body
        rows = [strip_ansi(row) for row in message.render(160)]
        assert rows[0].endswith(message.detail_title)
        assert rows[1:] == ['   ' + line for line in body.split('\n')]
        assert '**literal reviewer**' in rows[0]
    finally:
        view.close()


def test_child_and_direction_identity_deduplicate_independently_with_stable_export_ids():
    view, chat, queue, runtime, owner = presenter()
    try:
        events = [fact(), fact(direction='child_to_parent', status='received'), fact(child_id='child-2')]
        for event in events * 2:
            runtime.listeners[0](event)
        assert sum(view.display(event, owner) for event in queue) == 3
        messages = chat.children[:]
        records = chat.transcript_records()
        assert len({record.id for record in records}) == 3
        assert [(m.child_id, m.direction, m.message_id) for m in messages] == [
            (event['child_id'], event['direction'], event['message_id']) for event in events
        ]
        for message in messages:
            message.render(5)
            message.set_expanded(True)
            message.render(80)
        assert chat.children == messages and chat.transcript_records() == records
    finally:
        view.close()

def session_for(tmp_path, stream):
    store = SessionManager(tmp_path / 'sessions')
    return AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/test'),
                                    skills=SkillRepo().register(directory=tmp_path / 'skills', name="shared")),
        session_data=store.create_session(model='offline/test', working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_repo=None, bash=None)


async def poll_until(ui, predicate):
    async with asyncio.timeout(5):
        while True:
            ui._poll()
            if predicate():
                return
            await asyncio.sleep(.003)


def close_ui(ui):
    ui._child_messages.close()
    if ui._child_panel is not None:
        ui._close_children()
    ui._outputs.close()
    ui._saved_worker.close()
    ui._file_completions.close()
    ui._composer.close()


async def test_actual_three_children_log_each_direction_once_without_opening_agents_panel(tmp_path):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(ThinkingContent(thinking='PRIVATE THOUGHT'),
                                                           TextContent(text='Actual child reply'))))
    session = session_for(tmp_path, stream)
    ui = InteractiveMode(session)
    try:
        for index in range(3):
            identifier = await session.children.runtime.start(f'Task knowledge {index}',
                config={'label': f'Reviewer {index}', 'system_prompt': 'CHILD-ONLY-PROMPT', 'skills': []})
            await session.children.runtime.control('wait', identifier)
        ui._poll()
        messages = [item for item in ui._chat_log.children if isinstance(item, ChildMessage)]
        assert len(messages) == 6 and len(calls) == 3
        assert len({(m.child_id, m.message_id) for m in messages}) == 6
        assert all('Reviewer' in m.detail_title for m in messages)
        assert not any('PRIVATE THOUGHT' in m.detail_body for m in messages)
        assert ui._child_panel is None
        for _ in range(5):
            ui._poll()
        assert len([m for m in ui._chat_log.children if isinstance(m, ChildMessage)]) == 6
        before = len(calls)
        ui._open_children(identifier)
        await poll_until(ui, lambda: ui._child_panel.preview is not None and ui._child_panel.preview.settled)
        assert 'Actual child reply' in str(ui._child_panel.preview.records)
        ui._close_children()
        assert len(calls) == before  # Neither message display nor transcript viewing starts a model.
    finally:
        close_ui(ui)
        await session.children.runtime.aclose()
        session.dispose()


async def test_real_configured_start_keeps_task_user_prompt_skills_and_action_subset_separate(tmp_path):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    session = session_for(tmp_path, stream)
    session.agent.set_system_prompt('PARENT-SECRET-PROMPT')
    ui = InteractiveMode(session)
    try:
        ui._open_children()
        config = ui._child_resolve_config({'system_prompt': 'CHILD-CUSTOM-PROMPT', 'skills': [],
                                           'tools': [], 'tool_actions': {}, 'label': 'Configured'})
        ui._child_configured_start('Exact user task knowledge', 'inherit', config)
        await poll_until(ui, lambda: bool(calls) and not ui._child_jobs and not session.children.runtime.active)
        assert 'CHILD-CUSTOM-PROMPT' in str(calls[0].system_prompt)
        assert 'PARENT-SECRET-PROMPT' not in str(calls[0].system_prompt)
        assert 'Exact user task knowledge' not in str(calls[0].system_prompt)
        assert 'Exact user task knowledge' in str(calls[0].messages)
        record = session.children.runtime.snapshot()['children'][0]
        assert record['access'] == 'inherit'
        saved = session.children.store.load()[0]
        assert saved['config']['skills'] == [] and saved['config']['tool_actions'] == {}
        identifier = record['id']
        ui._child_panel._selected_id = identifier
        ui._child_panel.edit_system_prompt()
        await poll_until(ui, lambda: ui._child_panel._mode == 'form')
        assert ui._child_panel._input.get_text() == 'CHILD-CUSTOM-PROMPT'
        panel = ui._child_panel
        panel._input.set_text('REPLACEMENT-PROMPT')
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        for _ in range(40):
            panel.render(80)
            panel.handle_input(KeyEvent('pagedown'))
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        await poll_until(ui, lambda: not ui._child_jobs)
        record = await session.children.runtime.control('inspect', identifier)
        assert record['config']['system_prompt'] == 'REPLACEMENT-PROMPT'
        assert len(calls) == 1  # Idle-only config edit is not an execution/replay.
    finally:
        close_ui(ui)
        await session.children.runtime.aclose()
        session.dispose()


async def test_approval_alive_is_original_ui_lease_and_child_owner_not_successor(tmp_path):
    session = session_for(tmp_path, lambda *args: EventStream.empty(AssistantMessage()))
    ui = InteractiveMode(session)
    try:
        owner = session.children
        alive = owner.approval_alive
        assert alive is not None and alive()
        ui._bind_child_approvals()
        assert owner.approval_alive is alive  # Polling never replaces a pending review's lease.
        request = PermissionRequest('original-request', 'filesystem', {}, 'summary')
        original = owner.approver
        assert original is not None
        original('original-child', request)
        # Invalidate/rebind the UI lease. The captured old handler must not publish.
        ui._child_ui_lease = 'successor-ui-lease'
        ui._bind_child_approvals()
        assert not alive() and owner.approval_alive()
        original('foreign-child', request)
        assert ui._event_queue.drain(lambda event: None) == 1
        ui._exit_requested = True
        assert not owner.approval_alive()
    finally:
        close_ui(ui)
        await session.children.runtime.aclose()
        session.dispose()


def test_correspondence_layout_reuses_rows_but_refreshes_width_expansion_and_invalidation(monkeypatch):
    from xdog.coding.modes.interactive import child_messages

    calls = []
    original = child_messages.wrap_text_with_ansi

    def counted(text, width):
        calls.append((text, width))
        return original(text, width)

    monkeypatch.setattr(child_messages, 'wrap_text_with_ansi', counted)
    message = ChildMessage(fact(text='first\n' + 'line\n' * 20 + 'TAIL'), 'owner')
    first = message.render(40)
    before = len(calls)
    for _ in range(20):
        assert message.render(40) is first
    assert len(calls) == before
    assert 'TAIL' not in '\n'.join(first)
    message.set_expanded(True)
    assert 'TAIL' in '\n'.join(message.render(40))
    assert len(calls) > before
    expanded = len(calls)
    message.render(20)
    assert len(calls) > expanded
    message.invalidate()
    before = len(calls)
    message.render(20)
    assert len(calls) > before
    assert message.detail_body.endswith('TAIL')


@pytest.mark.parametrize('width', [18, 60, 160])
def test_child_preview_notice_and_all_wrapped_rows_are_dim_but_body_is_not(width):
    def gray(text):
        return f'\x1b[90m{text}\x1b[39m'

    body = 'first\nsecond\nthird\nfourth\nFULL-TAIL'
    message = ChildMessage(fact(text=body), 'owner', marker_style=gray)
    rows = message.render(width)
    # Header may wrap; preview body has exactly four rows, then the dim notice.
    first = next(index for index, row in enumerate(rows) if strip_ansi(row).strip() == 'first')
    assert all('\x1b[90m' not in row for row in rows[first:first + 4])
    notice_rows = rows[first + 4:]
    assert notice_rows
    assert all(row.startswith('   \x1b[90m') and row.endswith('\x1b[39m') for row in notice_rows)
    assert all(visible_width(row) <= width for row in rows)
    assert message.detail_body == body
    message.set_expanded(True)
    expanded = '\n'.join(message.render(width))
    assert 'FULL-TAIL' in expanded and '[Preview only' not in expanded


def test_child_preview_notice_respects_no_color_theme():
    message = ChildMessage(fact(text='line\n' * 8), 'owner', marker_style=lambda text: text)
    rows = message.render(60)
    assert '[Preview only' in '\n'.join(rows)
    assert '\x1b' not in '\n'.join(rows)


@pytest.mark.parametrize('status', ['received', 'completed', 'error', 'aborted'])
def test_child_metadata_uses_dim_and_accent_never_error_colors_or_body_markup(status):
    def dim(text):
        return f'\x1b[90m{text}\x1b[39m'

    def accent(text):
        return f'\x1b[36m{text}\x1b[39m'

    def forbidden(text):
        raise AssertionError('Correspondence metadata is not an error/permission indication')

    theme = replace(create_default_theme(), dim=dim, accent=accent, error=forbidden, success=forbidden)
    chat, queue, runtime = ChatLog(theme), [], Supervisor()
    view = ChildMessagePresenter(chat, queue.append)
    owner = ('session', 'branch', 'token')
    view.bind(runtime, owner)
    body = '# literal **body** [link](target)'
    try:
        runtime.listeners[0](fact(direction='child_to_parent', status=status, text=body, label='**审查员**'))
        assert view.display(queue.pop(), owner)
        message = chat.children[0]
        exports = chat.transcript_records()
        rows = message.render(160)
        assert dim('child child-1 (**审查员**)') in rows[0]
        assert accent(' → Parent') in rows[0]
        assert dim(f' · {status} · message stable-id') in rows[0]
        assert rows[1] == '   ' + body
        assert message.render(160) is rows
        for width in range(4, 41):
            wrapped = message.render(width)
            assert all(visible_width(row) <= width for row in wrapped)
            # Styling/wrapping does not drop any title character, including Unicode.
            plain = ''.join(strip_ansi(row)[3:] for row in wrapped)
            if width >= 5:  # A three-cell gutter plus a two-cell CJK glyph must fit.
                assert ''.join(message.detail_title.split()) in ''.join(plain.split())
        assert chat.transcript_records() == exports
    finally:
        view.close()
