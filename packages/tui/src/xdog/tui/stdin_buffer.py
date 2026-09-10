"""Incremental framing for non-blocking terminal input."""

from __future__ import annotations

import os
import select
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    import termios
    import tty
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

_ESCAPE = 0x1B
_PASTE_START = b"\x1b[200~"
_PASTE_END = b"\x1b[201~"
_DEFAULT_MAX_PASTE_BYTES = 1024 * 1024


def _utf8_token_length(data: bytes, index: int) -> int:
    first = data[index]
    if first < 0x80:
        return 1
    if 0xC2 <= first <= 0xDF:
        expected = 2
    elif 0xE0 <= first <= 0xEF:
        expected = 3
    elif 0xF0 <= first <= 0xF4:
        expected = 4
    else:
        return 1
    if index + expected > len(data):
        return 0
    token = data[index : index + expected]
    try:
        token.decode("utf-8")
    except UnicodeDecodeError:
        return 1
    return expected


def _string_sequence_length(data: bytes, index: int, *, allow_bel: bool) -> int:
    cursor = index + 2
    while cursor < len(data):
        if allow_bel and data[cursor] == 0x07:
            return cursor - index + 1
        if data[cursor : cursor + 2] == b"\x1b\\":
            return cursor - index + 2
        cursor += 1
    return 0


def _escape_sequence_length(data: bytes, index: int) -> int:
    if index + 1 >= len(data):
        return 0
    introducer = data[index + 1]
    if introducer == 0x5B:
        cursor = index + 2
        while cursor < len(data):
            if 0x40 <= data[cursor] <= 0x7E:
                return cursor - index + 1
            cursor += 1
        return 0
    if introducer in (0x4E, 0x4F):
        cursor = index + 2
        while cursor < len(data):
            if 0x40 <= data[cursor] <= 0x7E:
                return cursor - index + 1
            cursor += 1
        return 0
    if introducer == 0x5D:
        return _string_sequence_length(data, index, allow_bel=True)
    if introducer == 0x50:
        return _string_sequence_length(data, index, allow_bel=False)
    if introducer == 0x5F:
        return _string_sequence_length(data, index, allow_bel=True)
    return 2


def complete_prefix_length(data: bytes) -> int:
    """Return the byte length ending immediately before a partial token."""
    index = 0
    while index < len(data):
        token_length = (
            _escape_sequence_length(data, index)
            if data[index] == _ESCAPE
            else _utf8_token_length(data, index)
        )
        if token_length == 0:
            break
        index += token_length
    return index


def is_complete_sequence(data: bytes) -> bool:
    """Return whether *data* consists entirely of whole input tokens."""
    return complete_prefix_length(data) == len(data)


@dataclass(frozen=True, slots=True)
class KeyBytes:
    data: bytes


@dataclass(frozen=True, slots=True)
class Paste:
    text: str


@dataclass(frozen=True, slots=True)
class RejectedPaste:
    reason: str
    bytes_received: int


@dataclass(frozen=True, slots=True)
class EndOfInput:
    pass


InputFrame = KeyBytes | Paste | RejectedPaste | EndOfInput


@dataclass
class StdinBuffer:
    """Non-blocking stdin reader and incremental input framer."""

    _original_termios: Any = field(default=None, repr=False)
    _buffer: bytearray = field(default_factory=bytearray)
    _fd: int = field(default=-1)
    on_data: Callable[[bytes], None] | None = field(default=None, repr=False)
    max_paste_bytes: int = _DEFAULT_MAX_PASTE_BYTES
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _paste: bytearray | None = field(default=None, init=False, repr=False)
    _paste_bytes_received: int = field(default=0, init=False, repr=False)
    _paste_rejected: bool = field(default=False, init=False, repr=False)
    _pending_since: float | None = field(default=None, init=False, repr=False)
    _eof: bool = field(default=False, init=False, repr=False)

    def feed(self, data: bytes) -> list[InputFrame]:
        """Frame complete key bytes and atomic bracketed paste payloads."""
        if self._eof or not data:
            return []
        self._buffer.extend(data)
        if self._pending_since is None:
            self._pending_since = self.clock()
        frames: list[InputFrame] = []

        while self._buffer:
            if self._paste is not None:
                if not self._consume_paste(frames):
                    break
                continue

            start = self._buffer.find(_PASTE_START)
            if start >= 0:
                if start:
                    emitted = self._emit_complete_prefix(start)
                    frames.extend(emitted)
                    if not emitted:
                        break
                    continue
                del self._buffer[: len(_PASTE_START)]
                self._paste = bytearray()
                self._paste_bytes_received = 0
                self._paste_rejected = False
                continue

            if _PASTE_START.startswith(self._buffer):
                break
            frames.extend(self._emit_complete_prefix(len(self._buffer)))
            break

        self._refresh_pending_time()
        return frames

    def _consume_paste(self, frames: list[InputFrame]) -> bool:
        paste = self._paste
        if paste is None:
            return True
        end = self._buffer.find(_PASTE_END)
        if end < 0:
            keep = self._possible_suffix_length(self._buffer, _PASTE_END)
            consumed = bytes(self._buffer[:-keep]) if keep else bytes(self._buffer)
            self._append_paste(consumed)
            del self._buffer[: len(self._buffer) - keep]
            return False

        self._append_paste(bytes(self._buffer[:end]))
        del self._buffer[: end + len(_PASTE_END)]
        if self._paste_rejected:
            frames.append(
                RejectedPaste(
                    reason=f"paste exceeds {self.max_paste_bytes} bytes",
                    bytes_received=self._paste_bytes_received,
                )
            )
        else:
            frames.append(Paste(bytes(paste).decode("utf-8", errors="replace")))
        self._paste = None
        return True

    @staticmethod
    def _possible_suffix_length(data: bytearray, marker: bytes) -> int:
        maximum = min(len(data), len(marker) - 1)
        for length in range(maximum, 0, -1):
            if bytes(data[-length:]) == marker[:length]:
                return length
        return 0

    def _append_paste(self, data: bytes) -> None:
        paste = self._paste
        if paste is None:
            return
        self._paste_bytes_received += len(data)
        if self._paste_rejected:
            return
        if self._paste_bytes_received > self.max_paste_bytes:
            self._paste_rejected = True
            paste.clear()
            return
        paste.extend(data)

    def flush_expired(
        self,
        *,
        now: float | None = None,
        escape_timeout: float | None = None,
        sequence_timeout: float = 0.05,
    ) -> list[InputFrame]:
        """Resolve a stalled bare Escape or discard a partial control token."""
        if self._paste is not None or not self._buffer or self._pending_since is None:
            return []
        current = self.clock() if now is None else now
        esc_timeout = (
            0.1 if escape_timeout is None and os.environ.get("SSH_CONNECTION")
            else (0.01 if escape_timeout is None else escape_timeout)
        )
        timeout = esc_timeout if self._buffer == b"\x1b" else sequence_timeout
        if current - self._pending_since < timeout:
            return []
        if self._buffer == b"\x1b":
            self._buffer.clear()
            self._pending_since = None
            return [KeyBytes(b"\x1b")]
        self._buffer.clear()
        self._pending_since = None
        return []

    def _emit_complete_prefix(self, limit: int) -> list[InputFrame]:
        prefix_length = complete_prefix_length(bytes(self._buffer[:limit]))
        if prefix_length == 0:
            return []
        data = bytes(self._buffer[:prefix_length])
        del self._buffer[:prefix_length]
        return [KeyBytes(data)]

    def _refresh_pending_time(self) -> None:
        if not self._buffer:
            self._pending_since = None
        elif self._paste is None:
            self._pending_since = self.clock()

    def close(self) -> list[InputFrame]:
        """Close the input stream and discard any unfinished token or paste."""
        if self._eof:
            return []
        self._eof = True
        self._buffer.clear()
        self._paste = None
        self._pending_since = None
        return [EndOfInput()]

    @property
    def eof(self) -> bool:
        return self._eof

    def enter_raw(self) -> None:
        """Switch stdin to raw mode, saving the original terminal settings."""
        self._fd = sys.stdin.fileno()
        if termios is None or tty is None:
            return
        self._original_termios = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)

    def restore(self) -> None:
        """Restore the original terminal settings."""
        if self._original_termios is not None and self._fd >= 0 and termios is not None:
            termios.tcsetattr(self._fd, termios.TCSAFLUSH, self._original_termios)
            self._original_termios = None

    def read(self, timeout: float = 0.0) -> bytes:
        """Read available bytes, returning empty bytes for no data or EOF."""
        fd = self._fd if self._fd >= 0 else sys.stdin.fileno()
        select_timeout = None if timeout < 0 else timeout
        ready, _, _ = select.select([fd], [], [], select_timeout)
        if not ready:
            return b""
        data = os.read(fd, 4096)
        if not data:
            self._eof = True
        return data

    def read_buffered(self, timeout: float = 0.0) -> bytes:
        """Read bytes and return them with the legacy internal byte buffer."""
        new_data = self.read(timeout)
        if new_data:
            self._buffer.extend(new_data)
        result = bytes(self._buffer)
        self._buffer.clear()
        return result

    def read_complete(self, timeout: float = 0.0, wait: float = 0.005) -> bytes:
        """Read bytes, briefly waiting for a complete terminal sequence."""
        data = self.read(timeout)
        if not data:
            return b""
        self._buffer.extend(data)
        while not is_complete_sequence(bytes(self._buffer)):
            more = self.read(wait)
            if not more:
                break
            self._buffer.extend(more)
        result = bytes(self._buffer)
        self._buffer.clear()
        if self.on_data is not None:
            self.on_data(result)
        return result

    def has_data(self, timeout: float = 0.0) -> bool:
        """Return whether stdin is readable."""
        fd = self._fd if self._fd >= 0 else sys.stdin.fileno()
        ready, _, _ = select.select([fd], [], [], timeout)
        return bool(ready)

    @property
    def is_raw(self) -> bool:
        """Return whether raw mode was entered and can be restored."""
        return self._original_termios is not None
