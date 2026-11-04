"""flow.builder.style — truecolor ANSI styling + display-width padding.

Small dependency-free helpers (mirroring the SGR idiom in
``claw.channels.tui.tui_app``) used by the builder to render a colored,
column-aligned TUI.  Foreground/background use scoped resets (``\\x1b[39m`` /
``\\x1b[49m``) so a colored foreground composes over a colored background — needed
for the selection highlight.  Padding/truncation use :func:`tui.string_width`
(wcwidth-based, ANSI-aware) so styled cells still align to display columns.
"""

from __future__ import annotations

import re

from xdog.tui import string_width, strip_ansi

_FG_RESET = "\x1b[39m"
_BG_RESET = "\x1b[49m"
_RESET = "\x1b[0m"

# Whitespace that would break single-line cell rendering: newlines/carriage
# returns split a fixed-width cell across physical rows (corrupting the
# differential renderer and box borders), and tabs advance to a tab stop rather
# than one column — both defeat display-width accounting.  A cell is always one
# physical line, so we flatten these to a single space before measuring/padding.
_CONTROL_WS = re.compile(r"[\n\r\t\v\f]+")


def _flatten(text: str) -> str:
    """Collapse embedded control whitespace (newlines/tabs/…) to single spaces."""
    return _CONTROL_WS.sub(" ", text)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def fg(text: str, hex_color: str) -> str:
    """Wrap *text* in a truecolor foreground (scoped reset)."""
    r, g, b = _rgb(hex_color)
    return f"\x1b[38;2;{r};{g};{b}m{text}{_FG_RESET}"


def bg(text: str, hex_color: str) -> str:
    """Wrap *text* in a truecolor background (scoped reset)."""
    r, g, b = _rgb(hex_color)
    return f"\x1b[48;2;{r};{g};{b}m{text}{_BG_RESET}"


def bold(text: str) -> str:
    return f"\x1b[1m{text}{_RESET}"


def dim(text: str) -> str:
    return f"\x1b[2m{text}{_RESET}"


# --- palette -----------------------------------------------------------------

TITLE = "#8ab4f8"      # header bar
AGENT = "#8ab4f8"      # agent node accent (blue)
SCRIPT = "#81c995"     # script node accent (green)
HUMAN = "#c58af9"      # human node accent (purple)
SUBFLOW = "#f8b48a"    # subflow node accent (orange)
ENTRY = "#fdd663"      # entry marker (amber)
SELECT_BG = "#334155"  # selected-row highlight background
OK = "#81c995"         # valid status (green)
ERR = "#f28b82"        # error status (red)
MUTED = "#9aa0a6"      # dim labels / rules


def pad(cell: str, width: int) -> str:
    """Pad or truncate *cell* to *width* DISPLAY columns (ANSI/wide-char aware).

    Uses :func:`tui.string_width` so styled cells align.  Truncation drops
    trailing (visible) characters; any dangling ANSI is closed with a full reset.
    Embedded newlines/tabs are flattened to spaces first, so a cell is always a
    single physical line of exactly *width* columns.
    """
    if width <= 0:
        return ""
    cell = _flatten(cell)
    vis = string_width(cell)
    if vis == width:
        return cell
    if vis < width:
        return cell + " " * (width - vis)
    # Too wide: truncate by visible columns, keeping ANSI prefixes intact.
    out: list[str] = []
    shown = 0
    i = 0
    n = len(cell)
    while i < n and shown < width:
        ch = cell[i]
        if ch == "\x1b":  # copy the whole escape sequence verbatim (0 width)
            j = i + 1
            while j < n and cell[j] not in "mK":
                j += 1
            out.append(cell[i : j + 1])
            i = j + 1
            continue
        w = string_width(ch)
        if shown + w > width:
            break
        out.append(ch)
        shown += w
        i += 1
    result = "".join(out)
    # If the cell carried styling, ensure we don't bleed it past the cell.
    if strip_ansi(result) != result:
        result += _RESET
    if shown < width:
        result += " " * (width - shown)
    return result
