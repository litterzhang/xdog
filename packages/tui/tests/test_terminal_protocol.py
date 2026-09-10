from __future__ import annotations

from dataclasses import dataclass

import pytest
from xdog.tui.keys import is_kitty_protocol_active
from xdog.tui.stdin_buffer import KeyBytes, Paste, RejectedPaste
from xdog.tui.terminal_protocol import TerminalProtocol


@dataclass
class FakeClock:
    value: float = 10.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_protocol_starts_with_paste_keyboard_da_and_origin_queries() -> None:
    protocol = TerminalProtocol()
    assert protocol.startup() == "\x1b[?2004h\x1b[>7u\x1b[?u\x1b[c\x1b[6n"


def test_protocol_consumes_split_coalesced_and_late_replies() -> None:
    protocol = TerminalProtocol()
    protocol.startup()

    assert protocol.filter_input(b"\x1b[?") == b""
    assert protocol.filter_input(b"7u\x1b[?62;22c\x1b[12;") == b""
    assert protocol.filter_input(b"4Rhello") == b"hello"
    assert is_kitty_protocol_active()
    assert protocol.cursor_position == (12, 4)

    assert protocol.filter_input(b"\x1b[?62;22c!") == b"!"


def test_protocol_filters_only_key_frames_never_paste() -> None:
    protocol = TerminalProtocol()
    protocol.startup()
    paste = Paste("x\x1b[?7u\x1b[2;3Ry")
    rejected = RejectedPaste("paste exceeds 4 bytes", 8)

    assert protocol.filter_frame(paste) == [paste]
    assert protocol.filter_frame(rejected) == [rejected]
    assert protocol.filter_frame(KeyBytes(b"\x1b[?7uz")) == [KeyBytes(b"z")]


def test_protocol_negotiation_timeout_selects_fallback_once() -> None:
    clock = FakeClock()
    protocol = TerminalProtocol(clock=clock, negotiation_timeout=0.1)
    protocol.startup()

    clock.advance(0.09)
    assert protocol.expire_negotiation() == ""
    clock.advance(0.02)
    assert protocol.expire_negotiation() == "\x1b[>4;2m"
    assert protocol.expire_negotiation() == ""
    assert not is_kitty_protocol_active()


def test_da_fallback_does_not_override_late_kitty_success() -> None:
    protocol = TerminalProtocol()
    protocol.startup()

    assert protocol.filter_input(b"\x1b[?1;2c") == b""
    assert protocol.pending_output() == "\x1b[>4;2m"
    assert protocol.filter_input(b"\x1b[?7u") == b""
    assert is_kitty_protocol_active()


def test_false_report_prefix_is_released_by_expiry() -> None:
    clock = FakeClock()
    protocol = TerminalProtocol(clock=clock, negotiation_timeout=0.1)
    protocol.startup()

    assert protocol.filter_input(b"\x1b[?not-a-report") == b"\x1b[?not-a-report"
    assert protocol.filter_input(b"\x1b[?") == b""
    clock.advance(0.11)
    assert protocol.expire_input() == b"\x1b[?"


def test_cleanup_matches_selected_protocol() -> None:
    protocol = TerminalProtocol()
    protocol.startup()
    assert protocol.filter_input(b"\x1b[?7u") == b""
    assert protocol.cleanup() == "\x1b[?2004l\x1b[<u"


@pytest.mark.parametrize("replies", [
    b"a\x1b[?7ub\x1b[?62;22cc\x1b[12;4Rd",
    b"a\x1b[?62;22cb\x1b[12;4Rc\x1b[?7ud",
])
def test_every_reply_split_preserves_interleaved_typing(replies: bytes) -> None:
    for split in range(len(replies) + 1):
        protocol = TerminalProtocol()
        protocol.startup()
        assert protocol.filter_input(replies[:split]) + protocol.filter_input(replies[split:]) == b"abcd"
        assert protocol.cursor_position == (12, 4)
        protocol.cleanup()
