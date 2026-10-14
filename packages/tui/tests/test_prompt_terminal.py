from __future__ import annotations

import pytest
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.prompt_keymap import PromptKeymap
from xdog.tui.prompt_terminal import PromptTerminalInterceptor, prompt_key_policy
from xdog.tui.stdin_buffer import KeyBytes, Paste, StdinBuffer, key_tokens
from xdog.tui.terminal_protocol import TerminalProtocol
from xdog.tui.tui import TUI


class Theme:
    accent = bold = dim = border = staticmethod(lambda value: value)


def create():
    tui, editor, sent, notices, canceled, submitted = TUI(), InputPanel(Theme()), [], [], [], []
    tui.add_child(editor)
    tui.set_focus(editor)
    editor.on_submit = submitted.append
    owner, available = ['session'], [True]
    keymap = PromptKeymap()
    hooks = PromptTerminalInterceptor(tui, lambda: tui._focused is editor and not tui.has_overlay(),
        lambda: owner[0], lambda: available[0], lambda event: prompt_key_policy(event, keymap.resolve(event)),
        lambda item: sent.append(item) or True, lambda: canceled.append(True), notices.append)
    return tui, editor, hooks, sent, notices, canceled, submitted, owner


def test_coalesced_typing_enter_and_atomic_paste_keep_fifo_without_hooking_or_synthesizing_submit():
    tui, editor, hooks, sent, _, _, submitted, _ = create()
    assert tui._handle_frame(KeyBytes(b'ab\r'))
    assert hooks.queue.pending == 2 and not sent and not submitted
    hooks.poll()
    assert sent[-1].data == 'ab'
    hooks.queue.accept(sent[-1].id, {'data': 'AB'})
    hooks.poll()
    assert editor.get_text() == 'AB' and not submitted
    hooks.poll()
    assert submitted == ['AB'] and len(sent) == 1
    tui._handle_frame(Paste('original\npaste'))
    hooks.poll()
    hooks.queue.accept(sent[-1].id, {'data': 'line\n/exit\x1b[200~\r'})
    hooks.poll()
    assert '/exit' in editor.get_text() and len(submitted) == 1
    assert len(sent) == 2


@pytest.mark.parametrize('data', ['\r', '\x03', '\x04', '\x0c', '\x1a', '\x1b', '\x1bg',
                                  '\x1b[200~oops\x1b[201~', '\x1b]52;c;Zm9v\x07', '\x1b[2;3R',
                                  '\x1b[68;5u', '\x1b[999~', '\ud800'])
def test_transformed_host_protocol_controls_and_unknown_sequences_are_rejected_without_partial_submit(data):
    tui, editor, hooks, sent, notices, _, submitted, _ = create()
    tui._handle_frame(KeyBytes(b'x\r'))
    hooks.poll()
    hooks.queue.accept(sent[-1].id, {'data': data})
    hooks.poll()
    assert not editor.get_text() and not submitted and hooks.queue.pending == 0
    assert [item.data for item in hooks.queue.recovery] == ['x', '\r']
    assert notices


@pytest.mark.parametrize('control', [b'\x1b', b'\x03', b'\x04', b'\x0c', b'\x1a', b'\x1bg'])
def test_reserved_interrupts_and_host_handoffs_bypass_missing_hook_replies(control):
    tui, editor, hooks, sent, _, canceled, _, _ = create()
    tui._handle_frame(KeyBytes(b'x'))
    hooks.poll()
    old = sent[0].id
    tui._handle_frame(KeyBytes(control))
    assert canceled and hooks.queue.pending == 0 and not editor.get_text()
    assert not hooks.queue.accept(old, {'data': 'late'})


def test_focus_round_trip_session_and_physical_resize_leases_never_adopt_stale_input(monkeypatch):
    import xdog.tui.tui as module
    tui, editor, hooks, sent, _, _, _, owner = create()
    for change in ['focus', 'session', 'resize']:
        hooks.set_paused(False)
        tui._handle_frame(KeyBytes(b'x'))
        hooks.poll()
        old = sent[-1].id
        if change == 'focus':
            tui.set_focus(None)
            tui.set_focus(editor)
        elif change == 'session':
            owner[0] = 'new-session'
        else:
            monkeypatch.setattr(module, '_terminal_width', lambda: 81)
        hooks.queue.accept(old, {'data': 'stale'})
        hooks.poll()
        assert not editor.get_text() and not hooks.queue.pending
    assert len(hooks.queue.recovery) == 3


def test_terminal_reports_and_paste_boundaries_do_not_enter_frame_interception():
    tui, _, hooks, sent, _, _, _, _ = create()
    protocol, stdin = TerminalProtocol(), StdinBuffer()
    for frame in stdin.feed(b'\x1b[2;3R\x1b[200~a\rb\x1b[201~'):
        for filtered in protocol.filter_frame(frame):
            tui._handle_frame(filtered)
    hooks.poll()
    assert len(sent) == 1 and sent[0].kind == 'paste' and sent[0].data == 'a\rb'
    assert len(key_tokens('界'.encode() + b'\x1b[65;1:2u')) == 2


def test_failed_hook_discards_ordered_submit_and_recovery_is_explicit_draft_only():
    tui, editor, hooks, sent, notices, _, submitted, _ = create()
    tui._handle_frame(KeyBytes(b'x\r'))
    hooks.poll()
    hooks.queue.accept(sent[-1].id, {'error': True})
    hooks.poll()
    hooks.recovery_command('list', editor.handle_paste)
    assert 'never automatically replayed' in notices[-1]
    hooks.recovery_command('restore 1', editor.handle_paste)
    assert editor.get_text() == 'x' and not submitted and len(sent) == 1
    hooks.recovery_command('restore 2', editor.handle_paste)
    assert 'cannot be restored' in notices[-1]
    hooks.recovery_command('discard 2', editor.handle_paste)
    assert not hooks.queue.recovery
    assert prompt_key_policy(KeyEvent('x', ctrl=True), None) == 'immediate'


def test_pause_has_host_only_resume_and_recovery_rejects_malformed_ids_or_refused_draft_insertion():
    tui, editor, hooks, sent, notices, _, submitted, _ = create()
    tui._handle_frame(KeyBytes(b'x'))
    hooks.poll()
    hooks.queue.accept(sent[0].id, {'error': True})
    hooks.poll()
    assert hooks.paused
    tui._handle_frame(KeyBytes(b'plain'))
    assert editor.get_text() == 'plain' and len(sent) == 1
    hooks.recovery_command('restore ²', editor.handle_paste)
    assert 'Usage:' in notices[-1]
    hooks.recovery_command('restore 1', lambda _: False)
    assert len(hooks.queue.recovery) == 1
    hooks.recovery_command('resume', editor.handle_paste)
    assert not hooks.paused
    tui._handle_frame(KeyBytes(b'y'))
    hooks.poll()
    assert len(sent) == 2 and not submitted


def test_remapped_host_action_cannot_be_synthesized_and_original_submit_stays_ordered():
    tui, editor, hooks, sent, _, _, submitted, _ = create()
    keymap = PromptKeymap.from_overrides({'send': 'ctrl+s', 'terminal_input': 'f2'})
    hooks.policy = lambda event: prompt_key_policy(event, keymap.resolve(event))
    editor.set_keymap(keymap)
    tui._handle_frame(KeyBytes(b'x\x13'))
    hooks.poll()
    hooks.queue.accept(sent[0].id, {'data': '\x13'})
    hooks.poll()
    assert not submitted and hooks.paused
    assert [item.data for item in hooks.queue.recovery] == ['x', '\x13']
    hooks.set_paused(False)
    tui._handle_frame(KeyBytes(b'y\x13'))
    hooks.poll()
    hooks.queue.accept(sent[-1].id, {'data': 'Y'})
    hooks.poll()
    hooks.poll()
    assert submitted == ['Y']


@pytest.mark.parametrize('transition', ['same-size', 'noncapturing-overlay'])
def test_observed_geometry_and_overlay_round_trips_fence_delayed_prompt_keys(monkeypatch, transition):
    import xdog.tui.tui as module
    from xdog.tui.components.text import Text
    from xdog.tui.tui import OverlayOptions
    tui, editor, hooks, sent, _, _, _, _ = create()
    width = [80]
    monkeypatch.setattr(module, '_terminal_width', lambda: width[0])
    tui._handle_frame(KeyBytes(b'x'))
    hooks.poll()
    if transition == 'same-size':
        width[0] = 81
        tui.input_lease  # The physical loop observed the changed geometry.
        width[0] = 80
    else:
        overlay = tui.show_overlay(Text('notification'), OverlayOptions(non_capturing=True))
        overlay.hide()
    hooks.queue.accept(sent[0].id, {'data': 'late'})
    hooks.poll()
    assert not editor.get_text() and hooks.paused and hooks.queue.recovery[0].data == 'x'


def test_typing_burst_uses_one_bounded_hook_and_original_submit_stays_fifo():
    tui, editor, hooks, sent, _, _, submitted, _ = create()
    text = 'burst-' * 256
    tui._handle_frame(KeyBytes((text + '\r').encode()))
    assert hooks.queue.pending == 2 and not hooks.paused and not hooks.queue.recovery
    hooks.poll()
    assert len(sent) == 1 and sent[0].data == text
    hooks.queue.accept(sent[0].id, {})
    hooks.poll()
    hooks.poll()
    assert submitted == [text] and not editor.get_text() and len(sent) == 1


def test_burst_chunks_preserve_whole_utf8_and_enhanced_key_tokens_and_byte_limits(monkeypatch):
    tui, editor, hooks, sent, _, _, _, _ = create()
    delivered = []
    # Small tests above cover real editing; capture parsed keys here so a
    # 16 KiB framing boundary does not require 6000 repeated grapheme edits.
    def capture(event):
        delivered.append(event.key)
        return True
    monkeypatch.setattr(editor, "handle_input", capture)
    text = '界' * 6000
    tui._handle_frame(KeyBytes(text.encode() + b'\x1b[97u'))
    assert hooks.queue.pending == 2
    while hooks.queue.pending:
        hooks.poll()
        if sent and hooks.queue.held and hooks.queue.held[0].id == sent[-1].id:
            hooks.queue.accept(sent[-1].id, {})
    assert all(len(item.data.encode()) <= 16000 for item in sent)
    assert ''.join(item.data for item in sent) == text + '\x1b[97u'
    assert ''.join(delivered) == text + 'a' and not hooks.paused


def test_last_unsubscribe_drains_already_held_input_once_without_reinvoking_a_hook():
    tui, editor, hooks, sent, _, _, submitted, _ = create()
    live = [True]
    hooks.available = lambda: live[0]
    tui._handle_frame(KeyBytes(b'quiet'))
    tui._handle_frame(KeyBytes(b'after\r'))
    hooks.poll()
    live[0] = False
    hooks.queue.accept(sent[0].id, {'consume': True})
    while hooks.queue.pending:
        hooks.poll()
    assert submitted == ['after'] and len(sent) == 1 and not hooks.queue.recovery and not hooks.paused


def test_oversized_encoded_key_is_refused_instead_of_dropped_or_replayed():
    tui, editor, hooks, sent, notices, _, submitted, _ = create()
    tui._handle_frame(KeyBytes(b'\x1b[1;' + b'9' * 16000 + b'A'))
    assert hooks.paused and hooks.queue.pending == 0 and not sent and not submitted
    assert not editor.get_text() and 'no raw fallback' in notices[-1]


def test_prompt_release_never_calls_hook_or_interrupts_pending_edit_and_repeat_stays_encoded():
    tui, editor, hooks, sent, _, _, submitted, _ = create()
    tui._handle_frame(KeyBytes(b'a\x1b[97;1:3u\x1b[97;1:2u'))
    hooks.poll()
    assert len(sent) == 1 and sent[0].data == 'a\x1b[97;1:2u'
    tui._handle_frame(KeyBytes(b'\x1b[97;1:3u'))
    assert hooks.queue.pending == 1 and len(sent) == 1
    hooks.queue.accept(sent[0].id, {'data': 'aa'})
    hooks.poll()
    assert editor.get_text() == 'aa' and not submitted and not hooks.paused


def test_hook_cannot_replace_editing_text_with_release_event():
    tui, editor, hooks, sent, notices, _, submitted, _ = create()
    tui._handle_frame(KeyBytes(b'a\r'))
    hooks.poll()
    hooks.queue.accept(sent[0].id, {'data': '\x1b[97;1:3u'})
    hooks.poll()
    assert not editor.get_text() and not submitted and notices
    assert [item.data for item in hooks.queue.recovery] == ['a', '\r']


def test_opted_in_release_is_fifo_observation_only_and_protected_releases_stay_host_owned():
    tui, editor, hooks, sent, _, _, submitted, _ = create()
    hooks.release_available = lambda: True
    tui._handle_frame(KeyBytes(b'a\x1b[97;1:3u\x1b[97;1:2u\r'))
    while hooks.queue.pending:
        hooks.poll()
        if sent and hooks.queue.held and hooks.queue.held[0].id == sent[-1].id:
            hooks.queue.accept(sent[-1].id, {})
    assert [(item.kind, item.data) for item in sent] == [
        ('key', 'a'), ('release', '\x1b[97;1:3u'), ('key', '\x1b[97;1:2u')]
    assert submitted == ['aa'] and not editor.get_text()
    for encoded in (b'\x1b[13;1:3u', b'\x1b[99;5:3u', b'\x1b[27;1:3u'):
        tui._handle_frame(KeyBytes(encoded))
    assert hooks.queue.pending == 0 and len(sent) == 3


@pytest.mark.parametrize('change', ['withdraw', 'focus', 'resize', 'cancel', 'transform'])
def test_release_observation_is_revoked_without_editor_replay_or_recovery_insertion(change, monkeypatch):
    import xdog.tui.tui as module
    tui, editor, hooks, sent, notices, _, submitted, _ = create()
    enabled = [True]
    hooks.release_available = lambda: enabled[0]
    tui._handle_frame(KeyBytes(b'\x1b[97;1:3u'))
    hooks.poll()
    old = sent[-1].id
    if change == 'withdraw':
        enabled[0] = False
    elif change == 'focus':
        tui.set_focus(None)
        tui.set_focus(editor)
    elif change == 'resize':
        monkeypatch.setattr(module, '_terminal_width', lambda: 91)
    elif change == 'cancel':
        tui._handle_frame(KeyBytes(b'\x1b'))
    else:
        hooks.queue.accept(old, {'data': 'forged', 'consume': True})
    hooks.poll()
    assert not editor.get_text() and not submitted and hooks.paused
    assert len(hooks.queue.recovery) == 1
    assert not hooks.queue.accept(old, {})
    hooks.recovery_command(f'restore {old}', editor.handle_paste)
    assert 'cannot be restored' in notices[-1] and len(hooks.queue.recovery) == 1
