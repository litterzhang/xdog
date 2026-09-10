"""String width calculation, ANSI handling, and text wrapping utilities.

Includes both legacy utilities and new TypeScript-ported ANSI-aware functions.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

import wcwidth
from xdog.tui.editor_layout import grapheme_clusters

# Regex to match ANSI escape sequences (CSI, OSC, APC)
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;]*[A-Za-z]"   # CSI sequences
    r"|\x1b\].*?\x07"          # OSC with BEL
    r"|\x1b\].*?\x1b\\"       # OSC with ST
    r"|\x1b_.*?\x07"           # APC with BEL
    r"|\x1b_.*?\x1b\\"        # APC with ST
)


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


def sanitize_terminal_text(text: str) -> str:
    """Remove terminal commands and unsafe controls from untrusted text."""
    plain = strip_ansi(text)
    return "".join(
        char
        for char in plain
        if char in ("\n", "\t") or not unicodedata.category(char).startswith("C")
    )


_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)((?:api[_-]?key|token|password|passwd|secret|credential)\s*[=:]\s*)"
        r"[^\s,;]+"
    ),
)


def redact_sensitive_text(text: str) -> str:
    """Sanitize terminal text and redact common inline credential forms."""
    redacted = sanitize_terminal_text(text)
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        redacted = pattern.sub(r"\1<redacted>", redacted)
    return redacted


def string_width(text: str) -> int:
    """Return the display width of *text*, accounting for wide characters and ANSI codes."""
    cleaned = strip_ansi(text)
    # Replace tabs with 3 spaces for consistent rendering
    cleaned = cleaned.replace("\t", "   ")
    printable = "".join(ch for ch in cleaned if wcwidth.wcwidth(ch) >= 0)
    return max(0, wcwidth.wcswidth(printable))


# Alias matching TypeScript API name
visible_width = string_width


def char_width(ch: str) -> int:
    """Return the display width of a single character."""
    w = wcwidth.wcwidth(ch)
    return max(w, 0)


@dataclass(frozen=True, slots=True)
class AnsiSegment:
    """A piece of text with an optional leading ANSI escape."""

    escape: str
    text: str


def parse_ansi_segments(text: str) -> list[AnsiSegment]:
    """Split *text* into segments of (ansi_escape, visible_text)."""
    segments: list[AnsiSegment] = []
    pos = 0
    for m in _ANSI_RE.finditer(text):
        start, end = m.span()
        if start > pos:
            segments.append(AnsiSegment(escape="", text=text[pos:start]))
        segments.append(AnsiSegment(escape=m.group(), text=""))
        pos = end
    if pos < len(text):
        segments.append(AnsiSegment(escape="", text=text[pos:]))
    return segments


def truncate_to_width(text: str, max_width: int, ellipsis: str = "...") -> str:
    """Truncate *text* so its display width does not exceed *max_width*."""
    if max_width <= 0:
        return ""

    text_width = string_width(text)
    if text_width <= max_width:
        return text

    ellipsis_width = string_width(ellipsis)
    target = max_width - ellipsis_width
    if target <= 0:
        return ellipsis[:max_width] if ellipsis_width <= max_width else ""

    result: list[str] = []
    current_width = 0
    for segment in parse_ansi_segments(text):
        if segment.escape:
            result.append(segment.escape)
        for _start, _end, cluster in grapheme_clusters(segment.text):
            w = string_width(cluster)
            if current_width + w > target:
                return "".join(result) + ellipsis
            result.append(cluster)
            current_width += w

    return "".join(result) + ellipsis


def wrap_text(text: str, width: int) -> list[str]:
    """Word-wrap *text* to fit within *width* display columns.

    Respects existing newlines.  Long words that exceed *width* are broken.
    """
    if width <= 0:
        return [""]

    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue

        words = paragraph.split(" ")
        current_line: list[str] = []
        current_width = 0

        for word in words:
            w = string_width(word)
            if current_width == 0:
                if w <= width:
                    current_line.append(word)
                    current_width = w
                else:
                    _break_word_into(word, width, lines)
                    current_line = []
                    current_width = 0
            elif current_width + 1 + w <= width:
                current_line.append(word)
                current_width += 1 + w
            else:
                lines.append(" ".join(current_line))
                if w <= width:
                    current_line = [word]
                    current_width = w
                else:
                    _break_word_into(word, width, lines)
                    current_line = []
                    current_width = 0

        if current_line:
            lines.append(" ".join(current_line))

    return lines if lines else [""]


def _break_word_into(word: str, width: int, out: list[str]) -> None:
    """Break a single long *word* across multiple lines of *width*."""
    buf: list[str] = []
    buf_width = 0
    for ch in word:
        w = char_width(ch)
        if buf_width + w > width and buf:
            out.append("".join(buf))
            buf = []
            buf_width = 0
        buf.append(ch)
        buf_width += w
    if buf:
        out.append("".join(buf))


def pad_right(text: str, width: int, fill: str = " ") -> str:
    """Pad *text* on the right so its display width equals *width*."""
    current = string_width(text)
    if current >= width:
        return text
    return text + fill * (width - current)


def pad_center(text: str, width: int, fill: str = " ") -> str:
    """Center *text* within *width* display columns."""
    current = string_width(text)
    if current >= width:
        return text
    total_pad = width - current
    left = total_pad // 2
    right = total_pad - left
    return fill * left + text + fill * right


def clamp(value: int, lo: int, hi: int) -> int:
    """Clamp *value* into the range [lo, hi]."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def visible_chars(text: str) -> list[str]:
    """Return a list of visible characters from *text*, skipping ANSI escapes."""
    result: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\x1b":
            i += 1
            if i < len(text) and text[i] == "[":
                i += 1
                while i < len(text) and not text[i].isalpha():
                    i += 1
                i += 1
            continue
        result.append(text[i])
        i += 1
    return result


# ---------------------------------------------------------------------------
# ANSI Code Tracker (ported from TypeScript utils.ts)
# ---------------------------------------------------------------------------

def extract_ansi_code(text: str, pos: int) -> tuple[str, int] | None:
    """Extract ANSI escape sequence at position. Returns (code, length) or None."""
    if pos >= len(text) or text[pos] != "\x1b":
        return None

    if pos + 1 >= len(text):
        return None

    next_ch = text[pos + 1]

    # CSI sequence: ESC [ ... m/G/K/H/J
    if next_ch == "[":
        j = pos + 2
        while j < len(text) and text[j] not in "mGKHJ":
            j += 1
        if j < len(text):
            return (text[pos : j + 1], j + 1 - pos)
        return None

    # OSC sequence: ESC ] ... BEL or ESC ] ... ST
    if next_ch == "]":
        j = pos + 2
        while j < len(text):
            if text[j] == "\x07":
                return (text[pos : j + 1], j + 1 - pos)
            if text[j] == "\x1b" and j + 1 < len(text) and text[j + 1] == "\\":
                return (text[pos : j + 2], j + 2 - pos)
            j += 1
        return None

    # APC sequence: ESC _ ... BEL or ESC _ ... ST
    if next_ch == "_":
        j = pos + 2
        while j < len(text):
            if text[j] == "\x07":
                return (text[pos : j + 1], j + 1 - pos)
            if text[j] == "\x1b" and j + 1 < len(text) and text[j + 1] == "\\":
                return (text[pos : j + 2], j + 2 - pos)
            j += 1
        return None

    return None


class AnsiCodeTracker:
    """Track active ANSI SGR codes to preserve styling across line breaks.

    Ported from TypeScript utils.ts AnsiCodeTracker.
    """

    __slots__ = (
        "_bold", "_dim", "_italic", "_underline", "_blink",
        "_inverse", "_hidden", "_strikethrough",
        "_fg_color", "_bg_color",
    )

    def __init__(self) -> None:
        self._bold = False
        self._dim = False
        self._italic = False
        self._underline = False
        self._blink = False
        self._inverse = False
        self._hidden = False
        self._strikethrough = False
        self._fg_color: str | None = None
        self._bg_color: str | None = None

    def process(self, ansi_code: str) -> None:
        """Process an ANSI escape code and update tracked state."""
        if not ansi_code.endswith("m"):
            return

        m = re.match(r"\x1b\[([\d;]*)m", ansi_code)
        if not m:
            return

        params = m.group(1)
        if params == "" or params == "0":
            self._reset()
            return

        parts = params.split(";")
        i = 0
        while i < len(parts):
            try:
                code = int(parts[i])
            except (ValueError, IndexError):
                i += 1
                continue

            # 256-color and RGB
            if code in (38, 48):
                if i + 2 < len(parts) and parts[i + 1] == "5":
                    color_code = f"{parts[i]};{parts[i + 1]};{parts[i + 2]}"
                    if code == 38:
                        self._fg_color = color_code
                    else:
                        self._bg_color = color_code
                    i += 3
                    continue
                elif i + 4 < len(parts) and parts[i + 1] == "2":
                    color_code = ";".join(parts[i : i + 5])
                    if code == 38:
                        self._fg_color = color_code
                    else:
                        self._bg_color = color_code
                    i += 5
                    continue

            if code == 0:
                self._reset()
            elif code == 1:
                self._bold = True
            elif code == 2:
                self._dim = True
            elif code == 3:
                self._italic = True
            elif code == 4:
                self._underline = True
            elif code == 5:
                self._blink = True
            elif code == 7:
                self._inverse = True
            elif code == 8:
                self._hidden = True
            elif code == 9:
                self._strikethrough = True
            elif code == 22:
                self._bold = False
                self._dim = False
            elif code == 23:
                self._italic = False
            elif code == 24:
                self._underline = False
            elif code == 25:
                self._blink = False
            elif code == 27:
                self._inverse = False
            elif code == 28:
                self._hidden = False
            elif code == 29:
                self._strikethrough = False
            elif code == 39:
                self._fg_color = None
            elif code == 49:
                self._bg_color = None
            elif (30 <= code <= 37) or (90 <= code <= 97):
                self._fg_color = str(code)
            elif (40 <= code <= 47) or (100 <= code <= 107):
                self._bg_color = str(code)

            i += 1

    def _reset(self) -> None:
        self._bold = False
        self._dim = False
        self._italic = False
        self._underline = False
        self._blink = False
        self._inverse = False
        self._hidden = False
        self._strikethrough = False
        self._fg_color = None
        self._bg_color = None

    def clear(self) -> None:
        """Clear all state for reuse."""
        self._reset()

    def get_active_codes(self) -> str:
        """Return ANSI escape to re-apply all active attributes."""
        codes: list[str] = []
        if self._bold:
            codes.append("1")
        if self._dim:
            codes.append("2")
        if self._italic:
            codes.append("3")
        if self._underline:
            codes.append("4")
        if self._blink:
            codes.append("5")
        if self._inverse:
            codes.append("7")
        if self._hidden:
            codes.append("8")
        if self._strikethrough:
            codes.append("9")
        if self._fg_color:
            codes.append(self._fg_color)
        if self._bg_color:
            codes.append(self._bg_color)
        if not codes:
            return ""
        return f"\x1b[{';'.join(codes)}m"

    def has_active_codes(self) -> bool:
        return bool(
            self._bold or self._dim or self._italic or self._underline
            or self._blink or self._inverse or self._hidden or self._strikethrough
            or self._fg_color is not None or self._bg_color is not None
        )

    def get_line_end_reset(self) -> str:
        """Get reset for attributes that bleed into padding (underline)."""
        if self._underline:
            return "\x1b[24m"
        return ""


def _update_tracker_from_text(text: str, tracker: AnsiCodeTracker) -> None:
    """Scan text for ANSI codes and update the tracker."""
    i = 0
    while i < len(text):
        result = extract_ansi_code(text, i)
        if result:
            code, length = result
            tracker.process(code)
            i += length
        else:
            i += 1


def _split_into_tokens_with_ansi(text: str) -> list[str]:
    """Split text into word/whitespace tokens keeping ANSI codes attached."""
    tokens: list[str] = []
    current = ""
    pending_ansi = ""
    in_whitespace = False
    i = 0

    while i < len(text):
        result = extract_ansi_code(text, i)
        if result:
            code, length = result
            pending_ansi += code
            i += length
            continue

        ch = text[i]
        char_is_space = ch == " "

        if char_is_space != in_whitespace and current:
            tokens.append(current)
            current = ""

        if pending_ansi:
            current += pending_ansi
            pending_ansi = ""

        in_whitespace = char_is_space
        current += ch
        i += 1

    if pending_ansi:
        current += pending_ansi
    if current:
        tokens.append(current)

    return tokens


def _break_long_word(word: str, width: int, tracker: AnsiCodeTracker) -> list[str]:
    """Break a long word into lines of at most `width` visible chars."""
    lines: list[str] = []
    current_line = tracker.get_active_codes()
    current_width = 0

    # Separate ANSI codes from visible content
    segments: list[tuple[str, str]] = []  # (type, value)
    i = 0
    while i < len(word):
        result = extract_ansi_code(word, i)
        if result:
            code, length = result
            segments.append(("ansi", code))
            i += length
        else:
            segments.append(("char", word[i]))
            i += 1

    for seg_type, seg_value in segments:
        if seg_type == "ansi":
            current_line += seg_value
            tracker.process(seg_value)
            continue

        ch = seg_value
        w = char_width(ch)

        if current_width + w > width:
            line_end_reset = tracker.get_line_end_reset()
            if line_end_reset:
                current_line += line_end_reset
            lines.append(current_line)
            current_line = tracker.get_active_codes()
            current_width = 0

        current_line += ch
        current_width += w

    if current_line:
        lines.append(current_line)

    return lines if lines else [""]


def _wrap_single_line(line: str, width: int) -> list[str]:
    """Wrap a single line (no newlines) to fit within width."""
    if not line:
        return [""]

    vis_len = visible_width(line)
    if vis_len <= width:
        return [line]

    wrapped: list[str] = []
    tracker = AnsiCodeTracker()
    tokens = _split_into_tokens_with_ansi(line)

    current_line = ""
    current_visible_length = 0

    for token in tokens:
        token_visible_length = visible_width(token)
        is_whitespace = token.strip() == ""

        # Token too long — break it character by character
        if token_visible_length > width and not is_whitespace:
            if current_line:
                line_end_reset = tracker.get_line_end_reset()
                if line_end_reset:
                    current_line += line_end_reset
                wrapped.append(current_line)
                current_line = ""
                current_visible_length = 0

            broken = _break_long_word(token, width, tracker)
            wrapped.extend(broken[:-1])
            current_line = broken[-1]
            current_visible_length = visible_width(current_line)
            continue

        # Check overflow
        total_needed = current_visible_length + token_visible_length

        if total_needed > width and current_visible_length > 0:
            line_to_wrap = current_line.rstrip()
            line_end_reset = tracker.get_line_end_reset()
            if line_end_reset:
                line_to_wrap += line_end_reset
            wrapped.append(line_to_wrap)
            if is_whitespace:
                current_line = tracker.get_active_codes()
                current_visible_length = 0
            else:
                current_line = tracker.get_active_codes() + token
                current_visible_length = token_visible_length
        else:
            current_line += token
            current_visible_length += token_visible_length

        _update_tracker_from_text(token, tracker)

    if current_line:
        wrapped.append(current_line)

    return [ln.rstrip() for ln in wrapped] if wrapped else [""]


def wrap_text_with_ansi(text: str, width: int) -> list[str]:
    """Wrap text with ANSI codes preserved across line breaks.

    ONLY does word wrapping — NO padding, NO background colors.
    Returns lines where each line is <= width visible chars.
    Active ANSI codes are preserved across line breaks.

    Ported from TypeScript wrapTextWithAnsi().
    """
    if not text:
        return [""]

    input_lines = text.split("\n")
    result: list[str] = []
    tracker = AnsiCodeTracker()

    for input_line in input_lines:
        prefix = tracker.get_active_codes() if result else ""
        result.extend(_wrap_single_line(prefix + input_line, width))
        _update_tracker_from_text(input_line, tracker)

    return result if result else [""]


# ---------------------------------------------------------------------------
# Column-based string slicing (ported from TypeScript utils.ts)
# ---------------------------------------------------------------------------

SegmentType = str  # "text" | "ansi" | "wide"


@dataclass(frozen=True, slots=True)
class Segment:
    """A segment of text classified by type for column-aware operations."""

    type: SegmentType
    value: str
    width: int = 0


def extract_segments(text: str) -> list[Segment]:
    """Break *text* into typed segments for column-aware operations.

    Each segment is either:
    - ``"ansi"``: An ANSI escape sequence (width 0)
    - ``"wide"``: A wide (CJK) character (width 2)
    - ``"text"``: A normal character (width 1 or 0 for control chars)
    """
    segments: list[Segment] = []
    i = 0
    while i < len(text):
        result = extract_ansi_code(text, i)
        if result:
            code, length = result
            segments.append(Segment(type="ansi", value=code, width=0))
            i += length
            continue

        ch = text[i]
        w = char_width(ch)
        if w >= 2:
            segments.append(Segment(type="wide", value=ch, width=w))
        else:
            segments.append(Segment(type="text", value=ch, width=max(w, 0)))
        i += 1

    return segments


def slice_by_column(text: str, start: int, end: int) -> str:
    """Extract a substring by display column range ``[start, end)``.

    Handles ANSI escape sequences (preserved in output) and wide characters.
    If a wide character straddles a boundary it is replaced with a space.
    """
    if start >= end:
        return ""

    segments = extract_segments(text)
    result: list[str] = []
    col = 0

    for seg in segments:
        if seg.type == "ansi":
            # Always include ANSI codes that appear within the visible range
            if col >= start or not result:
                result.append(seg.value)
            continue

        seg_end = col + seg.width
        if seg_end <= start:
            col = seg_end
            continue
        if col >= end:
            break

        # Check for wide char straddling boundaries
        if seg.width >= 2:
            if col < start:
                # Wide char starts before range — replace with space for visible part
                result.append(" ")
            elif seg_end > end:
                # Wide char extends past end — replace with space
                result.append(" ")
            else:
                result.append(seg.value)
        else:
            result.append(seg.value)

        col = seg_end

    return "".join(result)


def slice_with_width(text: str, start_col: int, width: int) -> str:
    """Extract a substring starting at *start_col* with *width* display columns."""
    return slice_by_column(text, start_col, start_col + width)


def apply_background_to_line(
    line: str,
    width: int,
    bg_fn: Callable[[str], str],
) -> str:
    """Apply background color to a line, padding to full width.

    Ported from TypeScript applyBackgroundToLine().
    """
    vis_len = visible_width(line)
    padding_needed = max(0, width - vis_len)
    with_padding = line + " " * padding_needed
    return bg_fn(with_padding)
