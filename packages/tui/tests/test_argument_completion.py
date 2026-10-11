from __future__ import annotations

from xdog.tui.argument_completion import ArgumentCompletionFeed, argument_span
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent


def test_argument_span_replaces_only_cursor_token_suffix_and_keeps_other_arguments():
    catalog = {'ext-test-run': 'a' * 32}
    text = '/ext-test-run first alphatail last'
    cursor = text.index('alphatail') + 2
    command, identity, start, end, prefix = argument_span(text, cursor, catalog)
    assert text[start:end] == 'alphatail' and prefix == 'al'
    assert command == 'ext-test-run' and identity == 'a' * 32
    assert argument_span('/unknown argument', 17, catalog) is None
    assert argument_span('/ext-test-run', 13, catalog) is None


def test_feed_coalesces_latest_draft_and_refuses_old_owner_registration_or_cursor():
    sent = []
    catalog = {'ext-test-run': 'a' * 32}
    owner = ['original']
    feed = ArgumentCompletionFeed(lambda: owner[0], lambda: catalog, sent.append)
    text = '/ext-test-run a'
    feed.lookup(text, len(text))
    first = sent[-1]
    feed.lookup(text + 'l', len(text) + 1)
    feed.lookup(text + 'lp', len(text) + 2)
    assert len(sent) == 1
    feed.publish(first, [{'value': 'OLD'}])
    assert len(sent) == 2 and sent[-1].prefix == 'alp'
    latest = sent[-1]
    owner[0] = 'other'
    feed.publish(latest, [{'value': 'FOREIGN'}])
    assert not feed._rows and not feed.valid(latest.text, latest.cursor)
    feed.reset()
    feed.lookup(text, len(text))
    pending = sent[-1]
    catalog['ext-test-run'] = 'b' * 32
    feed.publish(pending, [{'value': 'RETIRED'}])
    assert not feed._rows


def test_editor_argument_enter_tab_do_not_submit_and_undo_preserves_other_text():
    submitted, sent = [], []
    catalog = {'ext-test-run': 'a' * 32}
    feed = ArgumentCompletionFeed(lambda: 'owner', lambda: catalog, sent.append)
    class Theme:
        accent = inverse = bold = dim = border = staticmethod(lambda text: text)
    editor = InputPanel(Theme(), command_provider=lambda: {'ext-test-run': 'Registered command'})
    editor.on_submit = submitted.append
    editor.set_argument_completion_provider(feed)
    editor.set_text('/ext-test-run first al last')
    editor._cursor = editor.get_text().index('al last') + 2
    editor.poll_autocomplete()
    feed.publish(sent[0], [])  # Initial end-of-line query is superseded by cursor-local request.
    request = sent[-1]
    editor.handle_input(KeyEvent('enter'))  # Pending metadata is not a submitted command.
    assert not submitted
    feed.publish(request, [{'value': 'alpha'}])
    editor.poll_autocomplete()
    editor.handle_input(KeyEvent('enter'))
    assert editor.get_text() == '/ext-test-run first alpha last' and not submitted
    editor.handle_input(KeyEvent('z', alt=True))
    assert editor.get_text() == '/ext-test-run first al last'
    editor.handle_input(KeyEvent('escape'))
    editor.handle_input(KeyEvent('tab'))
    assert feed._blocked is None and not submitted


def test_stale_selected_argument_is_not_inserted_or_submitted_after_registration_withdrawal():
    class Theme:
        accent = inverse = bold = dim = border = staticmethod(lambda text: text)
    catalog, sent, submitted = {'ext-test-run': 'a' * 32}, [], []
    feed = ArgumentCompletionFeed(lambda: 'owner', lambda: catalog, sent.append)
    editor = InputPanel(Theme(), command_provider=lambda: {'ext-test-run': 'Metadata'})
    editor.on_submit = submitted.append
    editor.set_argument_completion_provider(feed)
    editor.set_text('/ext-test-run a')
    feed.publish(sent[-1], [{'value': 'alpha'}])
    editor.poll_autocomplete()
    catalog.clear()
    editor.handle_input(KeyEvent('enter'))
    assert editor.get_text() == '/ext-test-run a' and not submitted


def test_escape_and_connection_reset_quarantine_late_argument_data_and_allow_new_request():
    sent = []
    feed = ArgumentCompletionFeed(lambda: 'owner', lambda: {'ext-test-run': 'a' * 32}, sent.append)
    text = '/ext-test-run a'
    feed.lookup(text, len(text))
    old = sent[-1]
    feed.dismiss(text, len(text))
    feed.publish(old, [{'value': 'late'}])
    assert feed.lookup(text, len(text)) is None
    feed.reset()
    feed.reopen()
    feed.lookup(text, len(text))
    assert len(sent) == 2
    feed.publish(old, [{'value': 'old connection'}])
    assert not feed._rows
    feed.publish(sent[-1], [{'value': 'alpha'}])
    assert [row.text for row in feed._rows] == ['alpha']


def test_quoted_argument_span_preserves_spaces_quotes_other_tokens_and_multiline_refusal():
    catalog = {'ext-test-run': 'a' * 32}
    text = '/ext-test-run first "release candidTAIL" last'
    cursor = text.index('candidTAIL') + 3
    _, _, start, end, prefix = argument_span(text, cursor, catalog)
    assert text[start:end] == '"release candidTAIL"' and prefix == '"release can'
    assert argument_span(text + '\nlater', cursor, catalog) is None


def test_dismiss_invalid_close_cancel_once_and_hold_slot_until_settlement_not_editor_guess():
    sent, canceled = [], []
    feed = ArgumentCompletionFeed(lambda: 'owner', lambda: {'ext-test-run': 'a' * 32}, sent.append, canceled.append)
    text = '/ext-test-run hold'
    feed.lookup(text, len(text))
    pending = sent[-1]
    feed.dismiss(text, len(text))
    feed.dismiss(text, len(text))
    assert canceled == [pending] and feed._inflight == pending
    feed.reopen()
    feed.lookup('/ext-test-run a', len('/ext-test-run a'))
    assert len(sent) == 1  # Never launch a successor before actual canceled-owner settlement.
    feed.publish(pending, None, 'canceled')
    assert len(sent) == 2 and not feed._rows
    next_request = sent[-1]
    feed.close()
    feed.close()
    assert canceled == [pending, next_request]
    feed.publish(next_request, [{'value': 'ignored'}])
    assert not feed._rows and feed._inflight is None


def test_unvalidated_catalog_cannot_dispatch_a_callback_with_nonopaque_completion_identity():
    sent = []
    for identity in (None, True, 'forged', 'z' * 32, {'callback': 'not data'}):
        feed = ArgumentCompletionFeed(lambda: 'owner', lambda: {'ext-test-run': identity}, sent.append)
        assert feed.lookup('/ext-test-run a', len('/ext-test-run a')) is None
    assert not sent


def test_argument_labels_render_unicode_and_duplicate_labels_select_actual_value_not_presentation():
    from xdog.tui.utils import visible_width

    class Theme:
        accent = inverse = bold = dim = border = staticmethod(lambda text: text)
    sent, submitted = [], []
    feed = ArgumentCompletionFeed(lambda: 'owner', lambda: {'ext-test-run': 'a' * 32}, sent.append)
    editor = InputPanel(Theme(), command_provider=lambda: {'ext-test-run': 'Metadata'})
    editor.on_submit = submitted.append
    editor.set_argument_completion_provider(feed)
    editor.set_text('/ext-test-run label')
    feed.publish(sent[-1], [
        {'value': 'release-1', 'label': '🚀 发布候选', 'description': 'First data'},
        {'value': 'release-2', 'label': '🚀 发布候选', 'description': 'Second data'}])
    assert editor.poll_autocomplete()
    text = '\n'.join(editor.render(100))
    assert '🚀 发布候选 [release-1]' in text and '🚀 发布候选 [release-2]' in text
    editor.handle_input(KeyEvent('down'))
    for width in (5, 15, 30, 80):
        assert all(visible_width(row) <= width for row in editor.render(width))
    editor.handle_input(KeyEvent('tab'))
    assert editor.get_text() == '/ext-test-run release-2' and not submitted


def test_presentation_only_changes_preserve_selected_value_but_request_repaint():
    from xdog.tui.autocomplete import CompletionItem

    class Theme:
        accent = inverse = bold = dim = border = staticmethod(lambda text: text)
    sent = []
    feed = ArgumentCompletionFeed(lambda: 'owner', lambda: {'ext-test-run': 'a' * 32}, sent.append)
    editor = InputPanel(Theme(), command_provider=lambda: {'ext-test-run': 'Metadata'})
    editor.set_argument_completion_provider(feed)
    editor.set_text('/ext-test-run a')
    feed.publish(sent[-1], [{'value': 'alpha', 'label': 'old'}, {'value': 'another', 'label': 'old'}])
    editor.poll_autocomplete()
    editor.handle_input(KeyEvent('down'))
    assert editor._select_list.selected_value == 'another'
    # Feed's frozen row snapshot changes presentation only; preserve selection by insertion identity.
    feed._rows = (CompletionItem('alpha', label='new'), CompletionItem('another', label='new'))
    assert editor.poll_autocomplete()
    assert editor._select_list.selected_value == 'another'
    assert 'new [another]' in '\n'.join(editor.render(100))
    assert not editor.poll_autocomplete()


def test_unsafe_label_description_or_duplicate_insertion_value_refuses_whole_result():
    sent = []
    feed = ArgumentCompletionFeed(lambda: 'owner', lambda: {'ext-test-run': 'a' * 32}, sent.append)
    for data in ([{'value': 'safe', 'label': '\x1b[2Jbad'}], [{'value': 'safe', 'label': '\u202eunsafe'}],
                 [{'value': 'safe', 'description': 'x' * 4097}], [{'value': 'safe', 'label': False}],
                 [{'value': 'same', 'label': 'one'}, {'value': 'same', 'label': 'two'}],
                 [{'value': 'safe'}, 'not metadata']):
        feed.reset()
        feed.lookup('/ext-test-run a', len('/ext-test-run a'))
        feed.publish(sent[-1], data)
        assert not feed._rows and feed._status == 'Invalid argument suggestions refused'


def test_captured_argument_body_coordinates_are_relative_codepoints_and_immutable_editor_data():
    sent = []
    feed = ArgumentCompletionFeed(lambda: 'owner', lambda: {'ext-test-run': 'a' * 32}, sent.append)
    text = '/ext-test-run 开发 enTAIL later'
    cursor = text.index('enTAIL') + 2
    feed.lookup(text, cursor)
    request = sent[-1]
    data = request.context()
    assert data == {'arguments': '开发 enTAIL later', 'cursor': 5, 'start': 3, 'end': 9}
    data['arguments'] = 'LOCAL-MUTATION'
    assert request.text == text and request.context()['arguments'] == '开发 enTAIL later'


def test_escaped_whitespace_quotes_and_backslashes_preserve_literal_full_token_boundaries():
    catalog = {'ext-test-run': 'a' * 32}
    for token in (r'release\ canTAIL', '"release canTAIL"', "'release\\ canTAIL'",
                  '"release\\" canTAIL"', r'release\\', "'release\\'", '"unfinished canTAIL'):
        text = '/ext-test-run first ' + token + ' later'
        cursor = text.index('canTAIL') + 3 if 'canTAIL' in token else text.index(token) + len(token)
        _, _, start, end, prefix = argument_span(text, cursor, catalog)
        # An unfinished quote intentionally includes the rest of the line.
        assert text[start:end] == (token + ' later' if token.startswith('"unfinished') else token)
        assert prefix == text[start:cursor]
    # Escaped backslash does not escape the subsequent separating space.
    text = r'/ext-test-run release\\ later'
    cursor = text.index('later') + 2
    _, _, start, end, prefix = argument_span(text, cursor, catalog)
    assert text[start:end] == 'later' and prefix == 'la'


def test_completion_can_insert_in_interior_argument_whitespace_without_replacing_next_token():
    text = '/ext-test-run first   later'
    cursor = text.index('   ') + 1
    assert argument_span(text, cursor, {'ext-test-run': 'a' * 32})[2:] == (cursor, cursor, '')


def test_literal_completion_replaces_entire_quoted_or_escaped_suffix_without_requoting():
    class Theme:
        accent = inverse = bold = dim = border = staticmethod(lambda text: text)

    for token, replacement in ((r'release\ canTAIL', r'release\ candidate'),
                               ('"release canTAIL"', '"release candidate"'),
                               ("'release\\ canTAIL'", "'release\\ candidate'")):
        sent, submitted = [], []
        feed = ArgumentCompletionFeed(lambda: 'owner', lambda: {'ext-test-run': 'a' * 32}, sent.append)
        editor = InputPanel(Theme(), command_provider=lambda: {'ext-test-run': 'Metadata'})
        editor.on_submit = submitted.append
        editor.set_argument_completion_provider(feed)
        original = '/ext-test-run first ' + token + ' later'
        editor.set_text(original)
        editor._cursor = original.index('canTAIL') + 3
        editor.poll_autocomplete()
        feed.publish(sent[0], [])  # Settle superseded end-of-line request.
        request = sent[-1]
        assert request.text[request.start:request.end] == token
        assert request.text[request.cursor:request.end] == ('TAIL' + token[-1] if token[-1] in "'\"" else 'TAIL')
        feed.publish(request, [{'value': replacement}])
        editor.poll_autocomplete()
        editor.handle_input(KeyEvent('tab'))
        assert editor.get_text() == '/ext-test-run first ' + replacement + ' later'
        assert not submitted
        editor.handle_input(KeyEvent('z', alt=True))
        assert editor.get_text() == original
