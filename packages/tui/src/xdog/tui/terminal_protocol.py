"""Terminal input protocol negotiation and reply filtering."""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from xdog.tui.keys import set_kitty_protocol_active
from xdog.tui.stdin_buffer import InputFrame, KeyBytes

_KITTY_RESPONSE = re.compile(rb"\x1b\[\?(\d+)u")
_DA_RESPONSE = re.compile(rb"\x1b\[\?[0-9;]*c")
_CPR_RESPONSE = re.compile(rb"\x1b\[(\d+);(\d+)R")
_RESPONSE_PREFIX = re.compile(rb"\x1b\[(?:\?[0-9;]*|[0-9;]*)$")


class TerminalProtocol:
    """Negotiate keyboard reporting and consume terminal replies from key frames."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        negotiation_timeout: float = 0.1,
    ) -> None:
        if negotiation_timeout <= 0:
            raise ValueError("negotiation_timeout must be positive")
        self._clock = clock
        self._negotiation_timeout = negotiation_timeout
        self._kitty_pushed = False
        self._modify_other_keys = False
        self._pending = ""
        self._negotiating = False
        self._negotiation_started: float | None = None
        self._input = bytearray()
        self._input_started: float | None = None
        self._cursor_position: tuple[int, int] | None = None

    def startup(self) -> str:
        """Enable paste, query keyboard/terminal support, and request cursor origin."""
        self._kitty_pushed = True
        self._negotiating = True
        self._negotiation_started = self._clock()
        self._cursor_position = None
        set_kitty_protocol_active(False)
        return "\x1b[?2004h\x1b[>7u\x1b[?u\x1b[c\x1b[6n"

    @property
    def cursor_position(self) -> tuple[int, int] | None:
        """Return the latest one-based row and column reported by CPR."""
        return self._cursor_position

    def request_cursor_position(self) -> str:
        self._cursor_position = None
        return "\x1b[6n"

    def filter_frame(self, frame: InputFrame) -> list[InputFrame]:
        """Filter terminal replies from a complete key frame only."""
        if not isinstance(frame, KeyBytes):
            return [frame]
        data = self.filter_input(frame.data)
        return [KeyBytes(data)] if data else []

    def filter_input(self, data: bytes) -> bytes:
        """Consume Kitty, device-attribute, and cursor-position reports."""
        candidate = bytes(self._input) + data
        self._input.clear()
        self._input_started = None
        output = bytearray()
        index = 0

        while index < len(candidate):
            if candidate[index] != 0x1B:
                output.append(candidate[index])
                index += 1
                continue

            response = self._match_response(candidate, index)
            if response is not None:
                end, kind, match = response
                self._accept_response(kind, match)
                index = end
                continue

            suffix = candidate[index:]
            if self._is_response_prefix(suffix):
                self._input.extend(suffix)
                self._input_started = self._clock()
                break

            output.append(candidate[index])
            index += 1

        return bytes(output)

    @staticmethod
    def _match_response(
        candidate: bytes,
        index: int,
    ) -> tuple[int, str, re.Match[bytes]] | None:
        for kind, pattern in (
            ("kitty", _KITTY_RESPONSE),
            ("da", _DA_RESPONSE),
            ("cpr", _CPR_RESPONSE),
        ):
            match = pattern.match(candidate, index)
            if match is not None:
                return match.end(), kind, match
        return None

    @staticmethod
    def _is_response_prefix(suffix: bytes) -> bool:
        if not suffix.startswith(b"\x1b["):
            return suffix in (b"\x1b",)
        return _RESPONSE_PREFIX.fullmatch(suffix) is not None

    def _accept_response(self, kind: str, match: re.Match[bytes]) -> None:
        if kind == "kitty":
            self._negotiating = False
            self._negotiation_started = None
            if int(match.group(1)):
                set_kitty_protocol_active(True)
                self._disable_fallback()
            else:
                self._enable_fallback()
            return
        if kind == "da":
            if self._negotiating:
                self._negotiating = False
                self._negotiation_started = None
                self._enable_fallback()
            return
        self._cursor_position = (int(match.group(1)), int(match.group(2)))

    def _enable_fallback(self) -> None:
        if self._modify_other_keys:
            return
        self._modify_other_keys = True
        self._pending += "\x1b[>4;2m"

    def _disable_fallback(self) -> None:
        if not self._modify_other_keys:
            return
        self._modify_other_keys = False
        self._pending += "\x1b[>4;0m"

    def expire_negotiation(self, *, now: float | None = None) -> str:
        """Select modifyOtherKeys when the Kitty query reaches its deadline."""
        if not self._negotiating or self._negotiation_started is None:
            return ""
        current = self._clock() if now is None else now
        if current - self._negotiation_started < self._negotiation_timeout:
            return ""
        self._negotiating = False
        self._negotiation_started = None
        self._enable_fallback()
        return self.pending_output()

    def expire_input(self, *, now: float | None = None) -> bytes:
        """Release a report-like prefix that never completed."""
        if not self._input or self._input_started is None:
            return b""
        current = self._clock() if now is None else now
        if current - self._input_started < self._negotiation_timeout:
            return b""
        data = bytes(self._input)
        self._input.clear()
        self._input_started = None
        return data

    def pending_output(self) -> str:
        output = self._pending
        self._pending = ""
        return output

    def cleanup(self) -> str:
        output = "\x1b[?2004l"
        if self._kitty_pushed:
            output += "\x1b[<u"
        if self._modify_other_keys:
            output += "\x1b[>4;0m"
        self._kitty_pushed = False
        self._modify_other_keys = False
        self._negotiating = False
        self._negotiation_started = None
        self._pending = ""
        self._input.clear()
        self._input_started = None
        set_kitty_protocol_active(False)
        return output
