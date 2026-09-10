from xdog.tui.keys import (
    KeyEvent,
    KeyEventType,
    is_key_release,
    is_key_repeat,
    parse_key_events,
)


def test_kitty_navigation_event_types_and_modifiers():
    for sequence, key in [(b"1;5:3A", "up"), (b"6;5:3~", "pagedown")]:
        assert parse_key_events(b"\x1b[" + sequence) == [
            KeyEvent(key=key, ctrl=True, event_type=KeyEventType.RELEASE),
        ]
    assert parse_key_events(b"\x1b[1;2:2B") == [
        KeyEvent(key="down", shift=True, event_type=KeyEventType.REPEAT),
    ]


def test_escape():
    events = parse_key_events(b'\x1b')
    assert len(events) == 1
    assert events[0].matches("escape")

def test_arrows():
    events = parse_key_events(b'\x1b[A')
    assert len(events) == 1
    assert events[0].matches("up")

    events = parse_key_events(b'\x1b[B')
    assert len(events) == 1
    assert events[0].matches("down")

def test_multiple_keys():
    events = parse_key_events(b'ab\x03')
    assert len(events) == 3
    assert events[0].matches("a")
    assert events[1].matches("b")
    assert events[2].matches("ctrl+c")

def test_tilde_sequences():
    events = parse_key_events(b'\x1b[15~')
    assert len(events) == 1
    assert events[0].matches("f5")

    events = parse_key_events(b'\x1b[1;2A')
    assert len(events) == 1
    assert events[0].matches("shift+up")

def test_backtab_csi_z():
    """CSI Z is Shift+Tab (backtab) on legacy xterm-style terminals."""
    events = parse_key_events(b'\x1b[Z')
    assert len(events) == 1
    assert events[0].key == "tab"
    assert events[0].shift is True
    assert events[0].matches("shift+tab")

def test_modify_other_keys_ctrl_enter():
    events = parse_key_events(b"\x1b[27;5;13~")
    assert events == [KeyEvent(key="enter", ctrl=True)]


# ---------- Kitty keyboard protocol tests ----------


def test_kitty_simple_letter():
    """CSI 97 u  →  'a' key press (Kitty protocol)."""
    events = parse_key_events(b"\x1b[97u")
    assert len(events) == 1
    assert events[0].key == "a"
    assert events[0].event_type == KeyEventType.PRESS

def test_kitty_shifted_alternate_codepoint():
    events = parse_key_events(b"\x1b[97:65;2u")
    assert events == [KeyEvent(key="A", shift=True)]


def test_kitty_release_event():
    """CSI 97;1:3u produces an ``a`` key release."""
    events = parse_key_events(b"\x1b[97;1:3u")
    assert len(events) == 1
    assert events[0].key == "a"
    assert events[0].event_type == KeyEventType.RELEASE
    assert is_key_release(events[0])
    assert not is_key_repeat(events[0])


def test_complete_frames_do_not_turn_unknown_control_reports_into_keys() -> None:
    assert parse_key_events(b"\x1b[12;4R") == []
    assert parse_key_events(b"\x1b[?62;22c") == []


def test_ctrl_c_and_escape_parse_without_sticking() -> None:
    assert parse_key_events(b"\x03") == [KeyEvent(key="c", ctrl=True)]
    assert parse_key_events(b"\x1b") == [KeyEvent(key="escape")]


def test_invalid_utf8_is_not_replaced_with_a_key_event() -> None:
    assert parse_key_events(b"\xe7\x95") == []
