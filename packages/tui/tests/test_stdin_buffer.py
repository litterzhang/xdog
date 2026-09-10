from __future__ import annotations

from dataclasses import dataclass

import pytest
from xdog.tui.stdin_buffer import (
    EndOfInput,
    KeyBytes,
    Paste,
    RejectedPaste,
    StdinBuffer,
    is_complete_sequence,
)


@dataclass
class FakeClock:
    value: float = 10.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.mark.parametrize(
    "sequence",
    [
        b"\x1b[A",
        b"\x1b[1;2A",
        b"\x1bOP",
        b"\x1bO1;2P",
        b"\x1b]0;title\x07",
        b"\x1b]0;title\x1b\\",
        "界".encode("utf-8"),
    ],
)
def test_feed_preserves_every_split_until_whole(sequence: bytes) -> None:
    for split in range(1, len(sequence)):
        buffer = StdinBuffer()
        assert buffer.feed(sequence[:split]) == []
        assert buffer.feed(sequence[split:]) == [KeyBytes(sequence)]


@pytest.mark.parametrize("partial", [b"\x1b", b"\x1b[", b"\x1b[1;2", b"\x1bO", b"\x1b]title", b"\xe7\x95"])
def test_incomplete_sequences_are_not_complete(partial: bytes) -> None:
    assert not is_complete_sequence(partial)


def test_feed_emits_complete_prefix_before_partial_sequence() -> None:
    buffer = StdinBuffer()

    assert buffer.feed(b"hello\x1b[") == [KeyBytes(b"hello")]
    assert buffer.feed(b"A!") == [KeyBytes(b"\x1b[A!")]


def test_feed_emits_bracketed_paste_atomically_after_long_delay() -> None:
    clock = FakeClock()
    buffer = StdinBuffer(clock=clock)

    assert buffer.feed(b"before\x1b[200~first\n") == [KeyBytes(b"before")]
    clock.advance(60.0)
    assert buffer.flush_expired() == []
    assert buffer.feed("二\nthird".encode("utf-8")) == []
    clock.advance(60.0)
    assert buffer.feed(b"\x1b[201~after") == [
        Paste("first\n二\nthird"),
        KeyBytes(b"after"),
    ]


def test_paste_payload_keeps_apparent_terminal_reports_as_data() -> None:
    buffer = StdinBuffer()
    payload = b"x\x1b[?7u\x1b[?62;22c\x1b[12;4Ry"

    assert buffer.feed(b"\x1b[200~" + payload + b"\x1b[201~") == [
        Paste(payload.decode("utf-8")),
    ]


def test_paste_overflow_is_one_explicit_rejection_not_keystrokes() -> None:
    buffer = StdinBuffer(max_paste_bytes=4)

    assert buffer.feed(b"\x1b[200~abc") == []
    assert buffer.feed(b"def\x1b[201~z") == [
        RejectedPaste(reason="paste exceeds 4 bytes", bytes_received=6),
        KeyBytes(b"z"),
    ]


def test_expired_partial_control_sequence_is_discarded_not_submitted() -> None:
    clock = FakeClock()
    buffer = StdinBuffer(clock=clock)

    assert buffer.feed(b"\x1b[13;") == []
    clock.advance(1.0)
    assert buffer.flush_expired(sequence_timeout=0.05) == []
    assert buffer.feed(b"x") == [KeyBytes(b"x")]


def test_bare_escape_expires_as_escape_and_ctrl_c_is_immediate() -> None:
    clock = FakeClock()
    buffer = StdinBuffer(clock=clock)

    assert buffer.feed(b"\x03") == [KeyBytes(b"\x03")]
    assert buffer.feed(b"\x1b") == []
    clock.advance(0.02)
    assert buffer.flush_expired(escape_timeout=0.01) == [KeyBytes(b"\x1b")]


def test_close_exposes_eof_without_replaying_partial_control_payload() -> None:
    buffer = StdinBuffer()

    assert buffer.feed(b"ok\x1b[") == [KeyBytes(b"ok")]
    assert buffer.close() == [EndOfInput()]
    assert buffer.eof
    assert buffer.feed(b"later") == []
