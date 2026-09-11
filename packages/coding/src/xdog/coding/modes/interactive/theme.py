"""Theme definitions for the interactive coding agent TUI.

Provides color palettes, text styling functions, and markdown theme
configuration matching the coding's dark terminal aesthetic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Callable

from xdog.tui.components.markdown import DefaultTextStyle, MarkdownTheme

_RST = "\x1b[0m"


def _color_prefix(hex_color: str, *, background: bool = False) -> str:
    rgb = tuple(int(hex_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    depth = os.environ.get("XDOG_TUI_COLOR", "auto")
    if depth == "auto":
        depth = ("truecolor" if os.environ.get("COLORTERM", "").lower() in {"truecolor", "24bit"}
                 else "256" if "256color" in os.environ.get("TERM", "") else "16")
    channel = 48 if background else 38
    if depth == "truecolor":
        return f"\x1b[{channel};2;{rgb[0]};{rgb[1]};{rgb[2]}m"
    basic = [
        (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
        (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
        (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
    ]
    if depth == "256":
        levels = (0, 95, 135, 175, 215, 255)
        palette = basic + [(r, g, b) for r in levels for g in levels for b in levels]
        palette += [(n, n, n) for n in range(8, 239, 10)]
    else:
        palette = basic
    index = min(range(len(palette)), key=lambda i: sum((a - b) ** 2 for a, b in zip(rgb, palette[i], strict=True)))
    if depth == "256":
        return f"\x1b[{channel};5;{index}m"
    code = (40 if background else 30) + index if index < 8 else (100 if background else 90) + index - 8
    return f"\x1b[{code}m"


def _fg(hex_color: str) -> Callable[[str], str]:
    prefix = _color_prefix(hex_color)
    return lambda text: f"{prefix}{text}\x1b[39m"


def _bg(hex_color: str) -> Callable[[str], str]:
    prefix = _color_prefix(hex_color, background=True)
    return lambda text: f"{prefix}{text}\x1b[49m"


def _bold(text: str) -> str:
    return f"\x1b[1m{text}{_RST}"


def _dim(text: str) -> str:
    return f"\x1b[2m{text}{_RST}"


def _italic(text: str) -> str:
    return f"\x1b[3m{text}{_RST}"


def _inverse(text: str) -> str:
    return f"\x1b[7m{text}\x1b[27m"


# -- Dark palette --

PALETTE = {
    "text": "#E8E3D5",
    "dim": "#7B7F87",
    "accent": "#F6C453",
    "accent_soft": "#F2A65A",
    "border": "#3C414B",
    "user_bg": "#2B2F36",
    "user_text": "#F3EEE0",
    "system_text": "#9BA3B2",
    "quote": "#8CC8FF",
    "quote_border": "#3B4D6B",
    "code": "#F0C987",
    "code_block": "#1E232A",
    "code_border": "#343A45",
    "link": "#7DD3A5",
    "error": "#F97066",
    "success": "#7DD3A5",
    "tool": "#A0C4FF",
    "diff_added": "#b5bd68",
    "diff_removed": "#cc6666",
    "diff_context": "#808080",
}


@dataclass(frozen=True)
class Theme:
    """Resolved theme with callable styling functions."""

    fg: Callable[[str], str]
    dim: Callable[[str], str]
    accent: Callable[[str], str]
    accent_soft: Callable[[str], str]
    border: Callable[[str], str]
    user_bg: Callable[[str], str]
    user_text: Callable[[str], str]
    system: Callable[[str], str]
    error: Callable[[str], str]
    success: Callable[[str], str]
    tool: Callable[[str], str]
    bold: Callable[[str], str]
    italic: Callable[[str], str]
    header: Callable[[str], str]
    diff_added: Callable[[str], str]
    diff_removed: Callable[[str], str]
    diff_context: Callable[[str], str]
    inverse: Callable[[str], str]
    markdown: MarkdownTheme
    user_default_text: DefaultTextStyle


def _palette_theme(palette: dict[str, str]) -> Theme:
    """Create the default dark theme."""
    fg = _fg(palette["text"])
    dim_fn = _fg(palette["dim"])
    accent = _fg(palette["accent"])
    accent_soft = _fg(palette["accent_soft"])
    border = _fg(palette["border"])
    user_bg = _bg(palette["user_bg"])
    user_text = _fg(palette["user_text"])
    system = _fg(palette["system_text"])
    error = _fg(palette["error"])
    success = _fg(palette["success"])
    tool = _fg(palette["tool"])

    md_theme = MarkdownTheme(
        heading=lambda t: _bold(_fg(palette["accent"])(t)),
        link=_fg(palette["link"]),
        link_url=lambda t: _dim(t),
        code=_fg(palette["code"]),
        code_block=_fg(palette["code"]),
        code_block_border=_fg(palette["code_border"]),
        quote=_fg(palette["quote"]),
        quote_border=_fg(palette["quote_border"]),
        hr=border,
        list_bullet=_fg(palette["accent_soft"]),
        bold=_bold,
        italic=_italic,
    )

    user_default = DefaultTextStyle(
        color=user_text,
        bg_color=user_bg,
    )

    return Theme(
        fg=fg,
        dim=dim_fn,
        accent=accent,
        accent_soft=accent_soft,
        border=border,
        user_bg=user_bg,
        user_text=user_text,
        system=system,
        error=error,
        success=success,
        tool=tool,
        bold=_bold,
        italic=_italic,
        header=lambda t: _bold(accent(t)),
        diff_added=_fg(palette["diff_added"]),
        diff_removed=_fg(palette["diff_removed"]),
        diff_context=_fg(palette["diff_context"]),
        inverse=_inverse,
        markdown=md_theme,
        user_default_text=user_default,
    )



def create_default_theme() -> Theme:
    """Resolve terminal-native (default), dark, light, or plain styling."""
    mode = os.environ.get("XDOG_TUI_THEME", "native").lower()
    plain = "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb" or mode == "plain"
    palette = dict(PALETTE)
    if mode == "light":
        palette.update(
            user_bg="#EEF1F5", user_text="#252A34",
            accent="#805500", accent_soft="#805500", dim="#555B66", border="#667085",
            system_text="#555B66", error="#B42318", success="#146C43", tool="#175CD3",
            quote="#175CD3", quote_border="#667085", code="#704800", code_border="#667085",
            link="#146C43", diff_added="#146C43", diff_removed="#B42318", diff_context="#555B66",
        )
    theme = _palette_theme(palette)
    def identity(text: str) -> str:
        return text
    if plain:
        md = MarkdownTheme(
            heading=identity, link=identity, link_url=identity, code=identity,
            code_block=identity, code_block_border=identity, quote=identity,
            quote_border=identity, hr=identity, list_bullet=identity,
            bold=identity, italic=identity,
        )
        return Theme(
            **{name: identity for name in Theme.__dataclass_fields__ if name not in {"markdown", "user_default_text"}},
            markdown=md, user_default_text=DefaultTextStyle(color=identity, bg_color=identity),
        )
    if mode == "dark":
        return theme
    # Default terminal colors honor the user's background and contrast choices.
    accent = theme.accent
    dim = theme.dim
    return replace(
        theme, fg=identity, accent=accent,
        dim=dim, header=lambda text: _bold(accent(text)),
        user_default_text=theme.user_default_text,
    )


def format_tokens(count: int) -> str:
    """Format token count for display.

    < 1,000:       raw number (e.g. "500")
    < 10,000:      one decimal (e.g. "5.2k")
    < 1,000,000:   rounded thousands (e.g. "150k")
    >= 1,000,000:  one decimal millions (e.g. "5.2M")
    """
    if count < 1_000:
        return str(count)
    if count < 10_000:
        return f"{count / 1_000:.1f}k"
    if count < 1_000_000:
        return f"{count // 1_000}k"
    if count < 10_000_000:
        return f"{count / 1_000_000:.1f}M"
    return f"{count // 1_000_000}M"
