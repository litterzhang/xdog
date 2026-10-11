from __future__ import annotations

import pytest
from xdog.tui.components.extension_custom import ExtensionCustom, safe_custom_line
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import CURSOR_MARKER, TUI
from xdog.tui.utils import string_width


def make():
    requests, answers = [], []
    def request(value):
        requests.append(value)
        return True
    panel = ExtensionCustom('fixture', {'id': 'owned', 'title': 'Custom'}, answers.append, request)
    panel.focused = True
    return panel, requests, answers


def frame(panel, revision=1, **options):
    return {'type': 'extension_custom_frame', 'id': 'owned', 'revision': revision,
            'generation': panel._generation, 'width': panel._geometry[0], 'height': panel._geometry[1],
            'lines': ['\x1b[31m界e\u0301\x1b[0m'], 'cursor': [0, 1], **options}


@pytest.mark.parametrize('escape', [
    '\x1b]52;c;clipboard\a', '\x1b]8;;https://private\x1b\\', '\x1b_Ggraphics\x1b\\',
    '\x1bPprivate\x1b\\', '\x1b[2J', '\x1b[?1049h', '\x1b[?25l', '\x1b[1;2H',
    '\x1b[999m', '\x1b[38;2;999;0;0m', '\x1b[38;5;256m', '\x1b[38:5:1m', CURSOR_MARKER,
])
def test_only_vetted_sgr_is_retained(escape):
    assert safe_custom_line('before' + escape + 'after') == 'beforeafter'
    assert safe_custom_line('\x1b[1;31mred\x1b[0m') == '\x1b[1;31mred\x1b[0m'
    assert safe_custom_line('\x1b[38;2;12;0;255mRGB\x1b[48;5;128m') == '\x1b[38;2;12;0;255mRGB\x1b[48;5;128m'


def test_unterminated_controls_and_bidi_cannot_inject_terminal_commands():
    assert safe_custom_line('before\x1b]52;unterminated') == 'before'
    assert safe_custom_line('a\r\b\x00\u202eb') == 'ab'
    assert safe_custom_line('👩\u200d💻\t') == '👩\u200d💻   '


def test_render_is_pure_poll_requests_viewport_and_only_painted_input_can_complete():
    panel, requests, answers = make()
    panel.handle_input(KeyEvent('enter'))
    panel.handle_paste('not yet')
    assert not requests
    panel.render(80)
    assert not requests and panel.poll() and len(requests) == 1
    assert not panel.poll()
    assert requests[0]['kind'] == 'resize' and requests[0]['height'] == 5
    assert panel.accept(frame(panel))
    panel.handle_input(KeyEvent('enter'))
    assert len(requests) == 1
    lines = panel.render(80)
    assert panel._painted == 1 and CURSOR_MARKER in lines[2]
    # Cursor within the second cell of a wide glyph rounds to its left boundary.
    assert lines[2].index(CURSOR_MARKER) < lines[2].index('界')
    panel.handle_paste('line1\nline2')
    panel.handle_input(KeyEvent('enter'))
    assert [value['kind'] for value in requests] == ['resize', 'paste', 'key']
    assert requests[-1]['painted'] == 1 and not answers
    panel.handle_input(KeyEvent('escape'))
    panel.handle_input(KeyEvent('c', ctrl=True))
    assert answers == [None]


def test_focus_and_zero_budget_fence_old_frames_even_at_same_geometry():
    panel, requests, _ = make()
    panel.render(80)
    panel.poll()
    old = frame(panel)
    panel.focused = False
    assert panel.poll() and requests[-1]['height'] == 0
    panel.focused = True
    panel.poll()
    assert not panel.accept(old)
    assert panel._painted == -1
    panel.handle_input(KeyEvent('enter'))
    panel.accept(frame(panel, 2))
    panel.render(80)
    assert panel._painted == 2
    panel.set_render_budget(0)
    assert panel.render(80) == []
    panel.poll()
    assert requests[-1]['height'] == 0
    panel.handle_paste('\n')
    assert requests[-1]['kind'] == 'resize'


def test_fifo_quota_refusal_ack_and_invalid_frames_are_visible_not_auto_replayed():
    panel, requests, _ = make()
    panel.render(80)
    panel.poll()
    panel.accept(frame(panel))
    panel.render(80)
    for _ in range(33):
        panel.handle_input(KeyEvent('x'))
    assert len(requests) == 33 and panel._inflight == 32
    assert 'busy' in '\n'.join(panel.render(80))
    panel.accept({'id': 'owned', 'type': 'extension_custom_ack', 'accepted': True})
    assert panel._inflight == 32
    panel.accept({'id': 'owned', 'type': 'extension_custom_ack', 'accepted': False})
    assert panel._inflight == 31 and panel._painted == -1
    assert not panel.accept(frame(panel, 0))
    panel.accept(frame(panel, 2, lines=['bad\nline']))
    assert 'Invalid' in '\n'.join(panel.render(80))


@pytest.mark.parametrize('width', [0, 1, 23, 24, 80])
@pytest.mark.parametrize('budget', [0, 1, 2, 3, 4, 12, 203])
def test_all_lines_and_host_cursor_fit_viewport(width, budget):
    panel, _, _ = make()
    panel.set_render_budget(budget)
    panel.render(width)
    panel.accept(frame(panel, lines=['x' * 1000] * 200, cursor=[0, 999]))
    lines = panel.render(width)
    assert len(lines) <= budget and all(string_width(line) <= width for line in lines)
    assert sum(line.count(CURSOR_MARKER) for line in lines) == 0


def test_reserved_keys_release_and_repeat_do_not_reach_worker():
    panel, requests, _ = make()
    panel.render(80)
    panel.poll()
    panel.accept(frame(panel))
    panel.render(80)
    for key in ('d', 'l', 'z'):
        assert not panel.handle_input(KeyEvent(key, ctrl=True))
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.RELEASE))
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_paste('x' * 16001)
    assert len(requests) == 1


def test_tui_same_focus_is_idempotent_and_resize_fences_arbitrary_key_and_paste(monkeypatch):
    import xdog.tui.tui as module
    panel, requests, answers = make()
    tui = TUI()
    tui.set_focus(panel)
    panel.render(80)
    panel.poll()
    panel.accept(frame(panel))
    panel.render(80)
    generation = panel._generation
    tui.set_focus(panel)
    assert panel._generation == generation and panel._painted == 1
    tui._previous_width, tui._previous_height = 80, 24
    monkeypatch.setattr(module, '_terminal_width', lambda: 79)
    monkeypatch.setattr(module, '_terminal_height', lambda: 24)
    tui._dispatch_input(KeyEvent('y'))
    tui._dispatch_paste('arbitrary completion paste')
    assert len(requests) == 1 and panel._painted == -1
    assert tui._dispatch_input(KeyEvent('escape')) and answers == [None]


def test_malformed_kitty_keys_and_host_enqueue_failures_do_not_crash_ui():
    from xdog.tui.keys import parse_key_events
    panel, requests, _ = make()
    panel.render(80)
    panel.poll()
    panel.accept(frame(panel))
    panel.render(80)
    keys = parse_key_events(b'\x1b[3u')
    assert keys and keys[0].key == '\x03'
    for key in keys:
        panel.handle_input(key)
    panel.handle_input(KeyEvent('\ud800'))
    panel.handle_input(KeyEvent('x' * 129))
    assert len(requests) == 1 and 'Unsupported' in '\n'.join(panel.render(80))
    def refuse(value):
        raise ValueError('Private host/owner error')
    panel.request = refuse
    panel.handle_input(KeyEvent('x'))
    assert 'refused' in '\n'.join(panel.render(80)) and 'Private' not in '\n'.join(panel.render(80))


@pytest.mark.parametrize('line,column,cluster', [
    ('👩\x1b[31m\u200d💻\x1b[0m', 1, '👩\u200d💻'),
    ('e\x1b[31m\u0301\x1b[0m', 0, 'e\u0301'),
])
def test_interleaved_styles_never_split_graphemes_for_cursor_or_clipping(line, column, cluster):
    from xdog.tui.components.extension_custom import _clip, _cursor
    from xdog.tui.utils import strip_ansi
    marked = _cursor(line, column)
    assert strip_ansi(marked) == cluster and marked.index(CURSOR_MARKER) <= marked.index(cluster[0])
    if string_width(cluster) == 2:
        assert strip_ansi(_clip(line, 1)).strip() == ''  # Not a partial person/ZWJ emoji.
    assert strip_ansi(_clip(line, 2)) == cluster


def test_custom_release_and_repeat_are_opt_in_and_host_controls_do_not_repeat():
    panel, requests, answers = make()
    panel._key_events = frozenset(('press', 'repeat', 'release'))
    panel.wants_key_release = True
    panel.render(80)
    panel.poll()
    panel.accept(frame(panel))
    panel.render(80)
    for kind in (KeyEventType.PRESS, KeyEventType.REPEAT, KeyEventType.RELEASE):
        panel.handle_input(KeyEvent('q', event_type=kind))
    assert [value['event_type'] for value in requests[1:]] == ['press', 'repeat', 'release']
    panel.handle_input(KeyEvent('escape', event_type=KeyEventType.RELEASE))
    panel.handle_input(KeyEvent('c', ctrl=True, event_type=KeyEventType.REPEAT))
    assert not answers and len(requests) == 4
    panel.handle_input(KeyEvent('escape'))
    assert answers == [None]


def test_message_details_are_paginated_sanitized_and_do_not_send_callback_input():
    panel, requests, _ = make()
    panel.dialog['message'] = 'Intro\x1b]52;c;unsafe\a\n' + '\n'.join(f'Explain {i}' for i in range(30)) + '\nTAIL'
    panel._details = True
    panel.set_render_budget(8)
    text = '\n'.join(panel.render(40))
    assert 'Intro' in text and 'F1 view' in text and '\x1b]52' not in text
    panel.poll()
    assert requests[-1]['height'] == 0
    panel.handle_input(KeyEvent('enter'))
    panel.handle_paste('not renderer input')
    assert len(requests) == 1
    panel.handle_input(KeyEvent('end'))
    assert 'TAIL' in '\n'.join(panel.render(40))
    panel.handle_input(KeyEvent('f1'))
    panel.render(40)
    panel.poll()
    assert requests[-1]['height'] == 5
    panel.accept(frame(panel))
    panel.render(40)
    panel.handle_input(KeyEvent('f1'))
    assert panel._painted == -1


def test_fullscreen_presentation_is_temporary_preemptible_and_recovers_after_resume():
    from types import SimpleNamespace

    from xdog.tui.components.extension_custom import ExtensionCustomPresentation
    panel, _, _ = make()
    panel.dialog['fullscreen'] = True
    calls = []
    def change(value):
        calls.append(value)
        tui._screen_change = value if value != tui.fullscreen else None
        return True
    tui = SimpleNamespace(fullscreen=False, _screen_change=None, request_fullscreen=change)
    layout = SimpleNamespace(fullscreen_view=None)
    owner = ExtensionCustomPresentation(tui, layout)
    owner.sync(panel, True)
    assert calls == [True] and layout.fullscreen_view is panel and panel.fullscreen
    owner.sync(panel, True)
    assert calls == [True]  # Reassertion cannot endlessly invalidate the frame.
    panel.set_height(40)
    panel.render(80)
    assert panel._geometry == (80, 37)
    tui.fullscreen, tui._screen_change = True, None
    owner.sync(panel, False)
    assert calls == [True, False] and layout.fullscreen_view is None and not panel.fullscreen
    tui.fullscreen, tui._screen_change = False, None
    owner.sync(panel, True)
    assert calls[-1] is True
    # Terminal suspension may restore its painter even though the view object survives.
    tui._screen_change = None
    owner.sync(panel, True)
    assert calls[-2:] == [True, True]
    owner.sync(None, False)
    assert layout.fullscreen_view is None and not panel.fullscreen


def test_basic_press_wire_is_backward_compatible_with_custom_v1():
    panel, requests, _ = make()
    panel.render(80)
    panel.poll()
    panel.accept(frame(panel))
    panel.render(80)
    panel.handle_input(KeyEvent('q'))
    assert 'event_type' not in requests[-1]  # A v1 server has an exact field schema.


def test_tui_parses_and_delivers_opted_in_kitty_repeat_and_release(monkeypatch):
    import xdog.tui.tui as module
    from xdog.tui.stdin_buffer import KeyBytes
    panel, requests, _ = make()
    panel._key_events = frozenset(('press', 'repeat', 'release'))
    panel.wants_key_release = True
    tui = TUI()
    tui.set_focus(panel)
    tui._previous_width, tui._previous_height = 80, 24
    monkeypatch.setattr(module, '_terminal_width', lambda: 80)
    monkeypatch.setattr(module, '_terminal_height', lambda: 24)
    panel.render(80)
    panel.poll()
    panel.accept(frame(panel))
    panel.render(80)
    assert tui._handle_frame(KeyBytes(b'\x1b[113;1:2u\x1b[113;1:3u'))
    assert [item['event_type'] for item in requests[1:]] == ['repeat', 'release']


def test_owned_overlay_placement_geometry_external_preemption_and_removal_are_handle_scoped():
    from types import SimpleNamespace

    from xdog.tui.components.extension_custom import ExtensionCustomPresentation
    from xdog.tui.components.text import Text
    panel, requests, _ = make()
    panel.dialog['overlay'] = {'width': '75%', 'max_height': 12, 'anchor': 'top-right', 'margin': {'right': 2}}
    tui = TUI()
    editor = Text('parent draft')
    tui.set_focus(editor)
    owner = ExtensionCustomPresentation(tui, SimpleNamespace(fullscreen_view=None))
    owner.sync(panel, True)
    handle = owner.overlay_handle
    assert panel.overlay and handle.is_focused() and tui.has_overlay() and not owner.blocked()
    assert not tui.has_overlay(exclude=panel) and not tui.fullscreen
    generation = panel._generation
    owner.sync(panel, True)
    assert owner.overlay_handle is handle and panel._generation == generation
    tui._composite_overlays(['parent draft'], 80, 24)
    panel.poll()
    assert requests[-1]['width'] == 60 and requests[-1]['height'] == 9
    panel.accept(frame(panel))
    tui._composite_overlays(['parent draft'], 80, 24)
    assert panel._painted == 1
    foreign = Text('Host permission surface')
    foreign_handle = tui.show_overlay(foreign)
    assert owner.blocked()
    owner.sync(panel, False)
    assert not panel.overlay and panel._frame is None and owner.overlay_handle is None
    assert foreign_handle.is_focused() and tui.has_overlay() and len(tui._overlay_stack) == 1
    foreign_handle.hide()
    owner.sync(panel, True)
    assert panel.overlay and owner.overlay_handle is not handle
    tui._composite_overlays(['parent draft'], 40, 10)
    panel.poll()
    assert requests[-1]['width'] == 30 and requests[-1]['height'] == 7
    owner.sync(None, False)
    assert not tui.has_overlay() and not panel.overlay and tui._focused is editor
    assert not tui.fullscreen


def test_live_noncapturing_overlay_frames_cursor_controls_and_protected_focus():
    from types import SimpleNamespace

    from xdog.tui.components.extension_custom import ExtensionCustomPresentation
    from xdog.tui.components.text import Text
    requests = []
    panel = ExtensionCustom('fixture', {'id': 'owned', 'title': 'Live', 'live_overlay': True,
        'overlay': {'width': 40, 'max_height': 8, 'non_capturing': True}}, lambda _: None,
        lambda value: requests.append(value) or True)
    tui, editor = TUI(), Text('parent')
    tui.set_focus(editor)
    owner = ExtensionCustomPresentation(tui, SimpleNamespace(fullscreen_view=None))
    owner.sync(panel, True)
    assert tui._focused is editor and not panel.focused and panel.frame_visible
    tui._composite_overlays(['parent'], 80, 24)
    panel.poll()
    assert requests[-1]['height'] == 5
    assert panel.accept(frame(panel))
    assert CURSOR_MARKER not in '\n'.join(panel.render(40)) and panel._painted == 1
    panel.handle_input(KeyEvent('x'))
    panel.handle_paste('not directed to background view')
    assert len(requests) == 1
    for sequence, hidden, capturing in [(1, True, False), (2, False, False), (3, False, True), (4, False, False)]:
        assert panel.accept_control({'sequence': sequence, 'hidden': hidden, 'capturing': capturing})
        owner.sync(panel, True)
        assert owner.overlay_handle.is_hidden() is hidden
        assert tui._focused is (panel if capturing and not hidden else editor)
        assert panel.frame_visible is (not hidden)
    assert not panel.accept_control({'sequence': 4, 'hidden': False, 'capturing': True})
    assert panel.accept_control({'sequence': 5, 'hidden': False, 'capturing': True})
    foreign = Text('Protected host review')
    handle = tui.show_overlay(foreign)
    owner.sync(panel, False)
    assert tui._focused is foreign and owner.overlay_handle is None
    owner.sync(panel, False)
    assert tui._focused is foreign
    handle.hide()
    owner.sync(panel, True)
    assert tui._focused is panel
    owner.sync(None, False)
    assert tui._focused is editor and not tui.has_overlay() and not panel.frame_visible


@pytest.mark.parametrize('value', [None, {}, {'sequence': True, 'hidden': False, 'capturing': True},
    {'sequence': 1, 'hidden': 1, 'capturing': True}, {'sequence': 2**53, 'hidden': False, 'capturing': True}])
def test_invalid_initial_live_overlay_state_does_not_poison_later_owner_controls(value):
    panel = ExtensionCustom('fixture', {'id': 'owned', 'title': 'Live', 'live_overlay': True,
        'overlay': {'non_capturing': True}, 'overlay_state': value}, lambda _: None, lambda _: True)
    assert 'overlay_state' not in panel.dialog and panel._control_sequence == 0
    assert panel.accept_control({'sequence': 1, 'hidden': False, 'capturing': True})
