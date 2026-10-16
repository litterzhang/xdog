"""Native input retains protocol, focus, paste and recovery safety without plugin hooks."""
from __future__ import annotations

import pytest
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.prompt_keymap import PromptKeymap
from xdog.tui.stdin_buffer import KeyBytes, Paste, StdinBuffer, key_tokens
from xdog.tui.terminal_protocol import TerminalProtocol
from xdog.tui.tui import TUI, Component


class Theme:
    accent = bold = dim = border = staticmethod(lambda value: value)


def create():
    tui, editor, submitted = TUI(), InputPanel(Theme()), []
    tui.add_child(editor)
    tui.set_focus(editor)
    editor.on_submit = submitted.append
    return tui, editor, submitted


def test_native_typing_enter_and_atomic_paste_never_synthesize_submit():
    tui, editor, submitted = create()
    assert tui._handle_frame(KeyBytes(b'ab\r'))
    assert submitted == ['ab']
    assert tui._handle_frame(Paste('line\n/exit\x1b[200~\r'))
    assert '/exit' in editor.get_text() and submitted == ['ab']


def test_remapped_native_send_and_retained_internal_shortcut_listener():
    tui, editor, submitted = create()
    keymap = PromptKeymap.from_overrides({'send': 'ctrl+s'})
    editor.set_keymap(keymap)
    seen = []
    def listener(event):
        if event.matches('ctrl+g'):
            seen.append(event.key)
            return {'consume': True}
        return None
    tui.add_input_listener(listener)
    tui._handle_frame(KeyBytes(b'x\x07\x13'))
    assert seen == ['g'] and submitted == ['x']
    tui.remove_input_listener(listener)
    with pytest.raises(ValueError):
        PromptKeymap.from_overrides({'terminal_input': 'f2'})


@pytest.mark.parametrize('encoded', [b'\x1b[13;1:3u', b'\x1b[99;5:3u', b'\x1b[27;1:3u'])
def test_release_does_not_submit_interrupt_or_reach_native_listeners(encoded):
    tui, editor, submitted = create()
    seen = []
    tui.add_input_listener(lambda event: seen.append(event))
    editor.set_text('draft')
    tui._handle_frame(KeyBytes(encoded))
    assert not submitted and not seen and editor.get_text() == 'draft'
    tui._handle_frame(KeyBytes(b'\x1b[97;1:2u'))
    assert editor.get_text() == 'drafta'  # Native repeat remains an edit.


def test_terminal_reports_filtered_before_local_input_and_paste_bounds_reject_without_replay():
    tui, editor, submitted = create()
    protocol, stdin = TerminalProtocol(), StdinBuffer(max_paste_bytes=8)
    for frame in stdin.feed(b'\x1b[2;3R\x1b[200~a\rb\x1b[201~'):
        for filtered in protocol.filter_frame(frame):
            tui._handle_frame(filtered)
    assert 'a' in editor.get_text() and 'b' in editor.get_text() and not submitted
    original = editor.get_text()
    for frame in stdin.feed(b'\x1b[200~too-long\r/exit\x1b[201~'):
        for filtered in protocol.filter_frame(frame):
            tui._handle_frame(filtered)
    assert editor.get_text() == original and not submitted
    # A rejected paste cannot leave bytes queued to submit on a later poll.
    assert not stdin.feed(b'')
    assert len(key_tokens('界'.encode() + b'\x1b[65;1:2u')) == 2


def test_focus_and_overlay_do_not_replay_prompt_input_into_successor():
    tui, editor, submitted = create()
    other = InputPanel(Theme())
    tui._handle_frame(KeyBytes(b'old'))
    overlay = tui.show_overlay(other)
    tui._handle_frame(Paste('new\n/exit'))
    assert editor.get_text() == 'old' and '/exit' in other.get_text() and not submitted
    overlay.hide()
    tui._handle_frame(KeyBytes(b'!'))
    assert editor.get_text() == 'old!' and '/exit' in other.get_text()


@pytest.mark.parametrize('handoff', ['suspend', 'outside', 'exit'])
def test_coalesced_keys_stop_at_native_handoff_without_implicit_send(handoff):
    tui, editor, submitted = create()
    def listener(event):
        if event.matches('ctrl+g'):
            if handoff == 'suspend':
                tui.suspend()
            elif handoff == 'outside':
                tui.run_outside_terminal(lambda: None)
            else:
                tui.stop()
            return {'consume': True}
        return None
    tui.add_input_listener(listener)
    tui._running = True
    tui._handle_frame(KeyBytes(b'draft\x07\r'))
    assert editor.get_text() == 'draft' and not submitted


def test_protected_focus_owns_escape_and_recovery_repaint_before_global_shortcuts(monkeypatch):
    import xdog.tui.tui as module
    tui, editor, submitted = create()
    monkeypatch.setattr(module, '_terminal_width', lambda: 80)
    monkeypatch.setattr(module, '_terminal_height', lambda: 24)
    tui._previous_width, tui._previous_height = 80, 24
    decisions, seen = [], []
    class Approval(Component):
        requires_visible_confirmation = True
        def render(self, width: int) -> list[str]:
            return ['Approve?']
        def handle_input(self, event: KeyEvent) -> bool:
            if event.key == 'escape':
                decisions.append('deny')
                return True
            if event.key == 'enter':
                decisions.append('approve')
                return True
            return False
    approval = Approval()
    tui.set_focus(approval)
    tui.add_input_listener(lambda event: seen.append(event))
    tui._handle_frame(Paste('yes\r'))
    tui._handle_frame(KeyBytes(b'\x1b[13;1:3u'))
    assert not decisions and not submitted and not editor.get_text()
    tui._handle_frame(KeyBytes(b'\x0c'))
    assert not decisions and not seen
    tui._handle_frame(KeyBytes(b'\x1b'))
    assert decisions == ['deny'] and not seen
    monkeypatch.setattr(module, '_terminal_width', lambda: 81)
    tui._handle_frame(KeyBytes(b'\r'))
    assert decisions == ['deny']  # Resize requires a fresh visible confirmation.
