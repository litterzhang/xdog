"""Key event parsing from raw terminal input bytes.

Handles standard VT100/xterm escape sequences, control keys, special keys,
and the Kitty keyboard protocol (CSI u sequences).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class SpecialKey(Enum):
    """Well-known special key names."""

    UP = auto()
    DOWN = auto()
    LEFT = auto()
    RIGHT = auto()
    HOME = auto()
    END = auto()
    PAGE_UP = auto()
    PAGE_DOWN = auto()
    INSERT = auto()
    DELETE = auto()
    BACKSPACE = auto()
    TAB = auto()
    ENTER = auto()
    ESCAPE = auto()
    F1 = auto()
    F2 = auto()
    F3 = auto()
    F4 = auto()
    F5 = auto()
    F6 = auto()
    F7 = auto()
    F8 = auto()
    F9 = auto()
    F10 = auto()
    F11 = auto()
    F12 = auto()


class KeyEventType(Enum):
    """Type of key event (for Kitty keyboard protocol)."""

    PRESS = auto()
    REPEAT = auto()
    RELEASE = auto()


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """A parsed key event from terminal input.

    Attributes:
        key: The key character or :class:`SpecialKey` name (lowercase for letters).
        ctrl: ``True`` when the Control modifier is active.
        alt: ``True`` when the Alt/Meta modifier is active.
        shift: ``True`` when the Shift modifier is active.
        event_type: The type of key event (press, repeat, release).
    """

    key: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    event_type: KeyEventType = KeyEventType.PRESS

    def matches(self, descriptor: str) -> bool:
        """Check whether this event matches a human-readable key descriptor.

        Descriptors look like ``"ctrl+a"``, ``"alt+shift+up"``, ``"enter"``, etc.
        """
        parts = [p.strip().lower() for p in descriptor.split("+")]
        want_ctrl = "ctrl" in parts
        want_alt = "alt" in parts
        want_shift = "shift" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "alt", "shift")]
        want_key = key_parts[0] if key_parts else ""
        return (
            self.key.lower() == want_key
            and self.ctrl == want_ctrl
            and self.alt == want_alt
            and self.shift == want_shift
        )


def is_key_release(event: KeyEvent) -> bool:
    """Return ``True`` if *event* is a key release."""
    return event.event_type == KeyEventType.RELEASE


def is_key_repeat(event: KeyEvent) -> bool:
    """Return ``True`` if *event* is a key repeat."""
    return event.event_type == KeyEventType.REPEAT


# Kitty protocol state
_kitty_protocol_active: bool = False


def is_kitty_protocol_active() -> bool:
    """Return ``True`` if the Kitty keyboard protocol is currently active."""
    return _kitty_protocol_active


def set_kitty_protocol_active(active: bool) -> None:
    """Set the Kitty keyboard protocol state."""
    global _kitty_protocol_active
    _kitty_protocol_active = active


# Kitty keyboard protocol key mappings
_KITTY_SPECIAL_KEYS: dict[int, str] = {
    9: "tab",
    13: "enter",
    27: "escape",
    127: "backspace",
    57358: "capslock",
    57359: "scrolllock",
    57360: "numlock",
    57361: "printscreen",
    57362: "pause",
    57363: "menu",
    57376: "f13",
    57377: "f14",
    57378: "f15",
    57379: "f16",
    57380: "f17",
    57381: "f18",
    57382: "f19",
    57383: "f20",
    57399: "kp_0",
    57400: "kp_1",
    57401: "kp_2",
    57402: "kp_3",
    57403: "kp_4",
    57404: "kp_5",
    57405: "kp_6",
    57406: "kp_7",
    57407: "kp_8",
    57408: "kp_9",
    57409: "kp_decimal",
    57410: "kp_divide",
    57411: "kp_multiply",
    57412: "kp_subtract",
    57413: "kp_add",
    57414: "kp_enter",
    57415: "kp_equal",
    57416: "kp_separator",
    57417: "kp_left",
    57418: "kp_right",
    57419: "kp_up",
    57420: "kp_down",
    57421: "kp_page_up",
    57422: "kp_page_down",
    57423: "kp_home",
    57424: "kp_end",
    57425: "kp_insert",
    57426: "kp_delete",
    57427: "kp_begin",
    57428: "media_play",
    57429: "media_pause",
    57430: "media_play_pause",
    57431: "media_reverse",
    57432: "media_stop",
    57433: "media_fast_forward",
    57434: "media_rewind",
    57435: "media_track_next",
    57436: "media_track_previous",
    57437: "media_record",
    57438: "lower_volume",
    57439: "raise_volume",
    57440: "mute_volume",
}

# Standard CSI tilde keys in Kitty protocol (same codes as xterm)
_KITTY_TILDE_KEYS: dict[int, str] = {
    2: "insert",
    3: "delete",
    5: "pageup",
    6: "pagedown",
    7: "home",
    8: "end",
    11: "f1",
    12: "f2",
    13: "f3",
    14: "f4",
    15: "f5",
    17: "f6",
    18: "f7",
    19: "f8",
    20: "f9",
    21: "f10",
    23: "f11",
    24: "f12",
}


def decode_kitty_printable(code: int) -> str:
    """Convert a Kitty key code to a printable character string."""
    if code in _KITTY_SPECIAL_KEYS:
        return _KITTY_SPECIAL_KEYS[code]
    if 1 <= code <= 0x10FFFF:
        try:
            return chr(code)
        except (ValueError, OverflowError):
            return ""
    return ""


def _parse_kitty_modifiers(modifier_val: int) -> tuple[bool, bool, bool, KeyEventType]:
    """Parse Kitty modifier bitmask.

    Returns (shift, alt, ctrl, event_type).
    Kitty modifier encoding: modifier_value = 1 + bitmask
    Bits: 0=shift, 1=alt, 2=ctrl, 3=super, 4=hyper, 5=meta
    Event type is encoded in bits 1-2 of a second sub-parameter (if present).
    """
    bitmask = modifier_val - 1
    shift = bool(bitmask & 0x01)
    alt = bool(bitmask & 0x02)
    ctrl = bool(bitmask & 0x04)
    # super, hyper, meta are bits 3, 4, 5 — we don't expose them currently
    return shift, alt, ctrl, KeyEventType.PRESS


# ---- Escape sequence tables ------------------------------------------------

_CSI_SPECIAL: dict[str, str] = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
}

_CSI_TILDE: dict[str, str] = {
    "1": "home",
    "2": "insert",
    "3": "delete",
    "4": "end",
    "5": "pageup",
    "6": "pagedown",
    "15": "f5",
    "17": "f6",
    "18": "f7",
    "19": "f8",
    "20": "f9",
    "21": "f10",
    "23": "f11",
    "24": "f12",
}

_SS3_MAP: dict[str, str] = {
    "P": "f1",
    "Q": "f2",
    "R": "f3",
    "S": "f4",
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
}

_CTRL_MAP: dict[int, tuple[str, bool]] = {
    0: ("space", True),  # ctrl+space / ctrl+@
    9: ("tab", False),
    10: ("enter", False),  # LF
    13: ("enter", False),  # CR
    27: ("escape", False),
    127: ("backspace", False),
}

# ctrl+a..ctrl+z  (1..26)  except the ones already mapped above
for _i in range(1, 27):
    if _i not in _CTRL_MAP:
        _CTRL_MAP[_i] = (chr(_i + 96), True)  # 1->a, 2->b, ...


def _parse_modifiers(param: str) -> tuple[bool, bool, bool]:
    """Parse xterm modifier parameter (1-based bitmask) returning (shift, alt, ctrl)."""
    try:
        n = int(param) - 1
    except (ValueError, TypeError):
        return False, False, False
    shift = bool(n & 1)
    alt = bool(n & 2)
    ctrl = bool(n & 4)
    return shift, alt, ctrl


def _parse_key_modifiers(param: str) -> tuple[bool, bool, bool, KeyEventType]:
    """Preserve Kitty event subparameters on legacy navigation sequences."""
    parts = param.split(":")
    shift, alt, ctrl = _parse_modifiers(parts[0])
    event_type = KeyEventType.PRESS
    if len(parts) >= 2:
        event_type = {
            "2": KeyEventType.REPEAT,
            "3": KeyEventType.RELEASE,
        }.get(parts[1], KeyEventType.PRESS)
    return shift, alt, ctrl, event_type


def parse_key_events(data: bytes) -> list[KeyEvent]:
    """Parse raw terminal input *data* into a list of :class:`KeyEvent` objects.

    Multiple key events may be encoded in a single read from stdin (e.g. pasted
    text or rapid typing).
    """
    events: list[KeyEvent] = []
    buf = data
    i = 0
    length = len(buf)

    while i < length:
        b = buf[i]

        # ---- ESC-prefixed sequences ----------------------------------------
        if b == 0x1B:
            # Check if this is just a bare Escape
            if i + 1 >= length:
                events.append(KeyEvent(key="escape"))
                i += 1
                continue

            next_byte = buf[i + 1]

            # CSI sequence: ESC [
            if next_byte == 0x5B:  # '['
                i += 2
                event = _parse_csi(buf, i, length)
                if event is not None:
                    parsed_event, consumed = event
                    events.append(parsed_event)
                    i += consumed
                else:
                    # The framer only supplies whole CSI tokens. Unknown terminal
                    # reports are protocol data, not literal Escape/user input.
                    while i < length and not 0x40 <= buf[i] <= 0x7E:
                        i += 1
                    if i < length:
                        i += 1
                continue

            # SS3 sequence: ESC O
            if next_byte == 0x4F:  # 'O'
                i += 2
                if i < length:
                    ch = chr(buf[i])
                    key = _SS3_MAP.get(ch)
                    if key:
                        events.append(KeyEvent(key=key))
                        i += 1
                        continue
                events.append(KeyEvent(key="escape"))
                continue

            # Alt + character: ESC <char>
            if 0x20 <= next_byte < 0x7F:
                ch = chr(next_byte)
                events.append(KeyEvent(key=ch.lower(), alt=True, shift=ch.isupper()))
                i += 2
                continue

            # Alt + ctrl: ESC <ctrl-char>
            if next_byte < 0x20:
                mapped = _CTRL_MAP.get(next_byte)
                if mapped:
                    key, is_ctrl = mapped
                    events.append(KeyEvent(key=key, ctrl=is_ctrl, alt=True))
                    i += 2
                    continue

            # Unknown ESC sequence - emit bare escape
            events.append(KeyEvent(key="escape"))
            i += 1
            continue

        # ---- Control characters ---------------------------------------------
        if b < 0x20 or b == 0x7F:
            mapped = _CTRL_MAP.get(b)
            if mapped:
                key, is_ctrl = mapped
                events.append(KeyEvent(key=key, ctrl=is_ctrl))
            i += 1
            continue

        # ---- UTF-8 characters -----------------------------------------------
        if b < 0x80:
            ch = chr(b)
            events.append(KeyEvent(key=ch, shift=ch.isupper()))
            i += 1
            continue

        # Multi-byte UTF-8
        try:
            # Determine byte length
            if (b & 0xE0) == 0xC0:
                n = 2
            elif (b & 0xF0) == 0xE0:
                n = 3
            elif (b & 0xF8) == 0xF0:
                n = 4
            else:
                i += 1
                continue
            if i + n <= length:
                ch = buf[i : i + n].decode("utf-8", errors="replace")
                events.append(KeyEvent(key=ch))
                i += n
            else:
                i += 1
        except Exception:
            i += 1
            continue

    return events


def _parse_csi(buf: bytes, start: int, length: int) -> tuple[KeyEvent, int] | None:
    """Parse a CSI sequence starting after ``ESC [``.

    Returns ``(KeyEvent, bytes_consumed)`` or ``None`` on failure.
    Handles standard xterm sequences and Kitty keyboard protocol (CSI u).
    """
    i = start
    params: list[str] = []
    current_param: list[str] = []

    # Collect parameters
    while i < length:
        b = buf[i]
        if 0x30 <= b <= 0x39:  # digit
            current_param.append(chr(b))
            i += 1
        elif b == 0x3B:  # ';'
            params.append("".join(current_param))
            current_param = []
            i += 1
        elif b == 0x3A:  # ':' — Kitty sub-parameters separator
            current_param.append(":")
            i += 1
        elif 0x40 <= b <= 0x7E:  # final byte
            params.append("".join(current_param))
            final = chr(b)
            consumed = i - start + 1

            # ----- Kitty keyboard protocol: CSI <keycode>[;<modifiers>[;<event>]] u -----
            if final == "u":
                return _parse_kitty_u(params, consumed)

            # xterm modifyOtherKeys: CSI 27;<modifiers>;<codepoint>~
            if final == "~" and len(params) >= 3 and params[0] == "27":
                try:
                    codepoint = int(params[2])
                except ValueError:
                    return None
                shift, alt, ctrl = _parse_modifiers(params[1])
                key = decode_kitty_printable(codepoint)
                if key:
                    return KeyEvent(
                        key=key,
                        ctrl=ctrl,
                        alt=alt,
                        shift=shift,
                    ), consumed
                return None

            # Tilde sequences: CSI <n> ~
            if final == "~":
                tilde_key = _CSI_TILDE.get(params[0]) if params else None
                if tilde_key:
                    shift, alt, ctrl = False, False, False
                    event_type = KeyEventType.PRESS
                    if len(params) >= 2:
                        shift, alt, ctrl, event_type = _parse_key_modifiers(params[1])
                    return KeyEvent(key=tilde_key, ctrl=ctrl, alt=alt, shift=shift, event_type=event_type), consumed
                return None

            # Arrow / home / end: CSI <modifier?> A/B/C/D/H/F
            special_key = _CSI_SPECIAL.get(final)
            if special_key:
                shift, alt, ctrl = False, False, False
                event_type = KeyEventType.PRESS
                if len(params) >= 2 and params[1]:
                    shift, alt, ctrl, event_type = _parse_key_modifiers(params[1])
                elif len(params) >= 1 and params[0] == "1" and len(params) < 2:
                    pass  # CSI 1 A  -- just the key, no modifier
                return KeyEvent(key=special_key, ctrl=ctrl, alt=alt, shift=shift, event_type=event_type), consumed

            # Backtab: CSI Z is Shift+Tab on legacy xterm-style terminals.
            if final == "Z":
                return KeyEvent(key="tab", shift=True), consumed

            return None
        else:
            # Intermediate byte or unexpected
            i += 1

    return None


def _parse_kitty_u(params: list[str], consumed: int) -> tuple[KeyEvent, int] | None:
    """Parse a Kitty keyboard protocol CSI ... u sequence.

    Params format: ``keycode[;modifiers[:event_type][;text]]``
    Sub-parameters use ``:`` as separator within a parameter.
    """
    if not params or not params[0]:
        return None

    # Parse keycode (first parameter, may have sub-params separated by :)
    keycode_parts = params[0].split(":")
    try:
        keycode = int(keycode_parts[0])
    except (ValueError, IndexError):
        return None

    # Parse modifiers and event type (second parameter)
    shift, alt, ctrl = False, False, False
    event_type = KeyEventType.PRESS

    if len(params) >= 2 and params[1]:
        mod_parts = params[1].split(":")
        try:
            modifier_val = int(mod_parts[0]) if mod_parts[0] else 1
            shift, alt, ctrl, _ = _parse_kitty_modifiers(modifier_val)
        except ValueError:
            pass

        # Event type from sub-parameter
        if len(mod_parts) >= 2:
            try:
                evt = int(mod_parts[1])
                if evt == 1:
                    event_type = KeyEventType.PRESS
                elif evt == 2:
                    event_type = KeyEventType.REPEAT
                elif evt == 3:
                    event_type = KeyEventType.RELEASE
            except ValueError:
                pass

    # Resolve keycode to key name. Kitty may supply shifted/base-layout
    # alternatives as subparameters; prefer the shifted value when Shift is on.
    resolved_code = keycode
    if shift and len(keycode_parts) >= 2 and keycode_parts[1]:
        try:
            resolved_code = int(keycode_parts[1])
        except ValueError:
            pass
    key = decode_kitty_printable(resolved_code)
    if not key:
        return None

    # Legacy events without an alternate shifted codepoint keep lowercase plus
    # the explicit shift flag.
    if len(key) == 1 and key.isalpha() and shift and resolved_code == keycode:
        key = key.lower()

    return KeyEvent(key=key, ctrl=ctrl, alt=alt, shift=shift, event_type=event_type), consumed
