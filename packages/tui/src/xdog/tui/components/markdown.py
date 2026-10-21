"""Markdown renderer component -- renders Markdown to styled terminal text.

Ported from TypeScript markdown.ts to use the string-based rendering model.
Supports headings, code blocks, bold, italic, inline code, links, lists,
blockquotes, horizontal rules, strikethrough, and tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from xdog.tui.markdown_transforms import apply_markdown_transforms
from xdog.tui.tui import Component
from xdog.tui.utils import (
    apply_background_to_line,
    visible_width,
    wrap_text_with_ansi,
)

# ---------------------------------------------------------------------------
# Theme / style interfaces (matching TypeScript MarkdownTheme)
# ---------------------------------------------------------------------------

@dataclass
class DefaultTextStyle:
    """Default text styling for markdown content."""

    color: Callable[[str], str] | None = None
    bg_color: Callable[[str], str] | None = None
    bold: bool = False
    italic: bool = False
    strikethrough: bool = False
    underline: bool = False


@dataclass
class MarkdownTheme:
    """Theme functions for markdown elements.

    Each function takes text and returns styled text with ANSI codes.
    """

    heading: Callable[[str], str] = field(default_factory=lambda: _identity)
    link: Callable[[str], str] = field(default_factory=lambda: _identity)
    link_url: Callable[[str], str] = field(default_factory=lambda: _identity)
    code: Callable[[str], str] = field(default_factory=lambda: _identity)
    code_block: Callable[[str], str] = field(default_factory=lambda: _identity)
    code_block_border: Callable[[str], str] = field(default_factory=lambda: _identity)
    quote: Callable[[str], str] = field(default_factory=lambda: _identity)
    quote_border: Callable[[str], str] = field(default_factory=lambda: _identity)
    hr: Callable[[str], str] = field(default_factory=lambda: _identity)
    list_bullet: Callable[[str], str] = field(default_factory=lambda: _identity)
    bold: Callable[[str], str] = field(default_factory=lambda: _identity)
    italic: Callable[[str], str] = field(default_factory=lambda: _identity)
    strikethrough: Callable[[str], str] = field(default_factory=lambda: _identity)
    underline: Callable[[str], str] = field(default_factory=lambda: _identity)
    highlight_code: Callable[[str, str | None], list[str]] | None = None
    code_block_indent: str = "  "


def _identity(text: str) -> str:
    return text


def _default_theme() -> MarkdownTheme:
    """Create a default ANSI theme for markdown rendering."""
    return MarkdownTheme(
        heading=lambda t: f"\x1b[1;36m{t}\x1b[0m",
        link=lambda t: f"\x1b[34m{t}\x1b[0m",
        link_url=lambda t: f"\x1b[2;34m{t}\x1b[0m",
        code=lambda t: f"\x1b[33m{t}\x1b[0m",
        code_block=lambda t: f"\x1b[33m{t}\x1b[0m",
        code_block_border=lambda t: f"\x1b[2m{t}\x1b[0m",
        quote=lambda t: f"\x1b[37m{t}\x1b[0m",
        quote_border=lambda t: f"\x1b[2;36m{t}\x1b[0m",
        hr=lambda t: f"\x1b[2m{t}\x1b[0m",
        list_bullet=lambda t: f"\x1b[36m{t}\x1b[0m",
        bold=lambda t: f"\x1b[1m{t}\x1b[0m",
        italic=lambda t: f"\x1b[3m{t}\x1b[0m",
        strikethrough=lambda t: f"\x1b[9m{t}\x1b[0m",
        underline=lambda t: f"\x1b[4m{t}\x1b[0m",
    )


# ---------------------------------------------------------------------------
# Simple markdown tokenizer (equivalent to marked.lexer())
# ---------------------------------------------------------------------------

@dataclass
class Token:
    """A parsed markdown token."""

    type: str
    text: str = ""
    depth: int = 0
    lang: str = ""
    raw: str = ""
    tokens: list[Token] = field(default_factory=list)
    items: list[Token] = field(default_factory=list)
    ordered: bool = False
    start: int = 1
    header: list[Token] = field(default_factory=list)
    rows: list[list[Token]] = field(default_factory=list)


def _tokenize_inline(text: str) -> list[Token]:
    """Parse inline markdown formatting into tokens."""
    tokens: list[Token] = []
    pos = 0
    length = len(text)

    while pos < length:
        # Inline code
        if text[pos] == "`":
            end = text.find("`", pos + 1)
            if end > pos:
                tokens.append(Token(type="codespan", text=text[pos + 1 : end]))
                pos = end + 1
                continue

        # Bold + italic (*** or ___)
        if pos + 2 < length and text[pos : pos + 3] in ("***", "___"):
            marker = text[pos : pos + 3]
            end = text.find(marker, pos + 3)
            if end > pos:
                inner = text[pos + 3 : end]
                inner_tokens = _tokenize_inline(inner)
                # Apply both bold and italic via separate tokens
                em_token = Token(type="em", tokens=[
                    Token(type="strong", tokens=inner_tokens)
                ])
                tokens.append(em_token)
                pos = end + 3
                continue

        # Bold (** or __)
        if pos + 1 < length and text[pos : pos + 2] in ("**", "__"):
            marker = text[pos : pos + 2]
            end = text.find(marker, pos + 2)
            if end > pos:
                inner = text[pos + 2 : end]
                inner_tokens = _tokenize_inline(inner)
                tokens.append(Token(type="strong", tokens=inner_tokens))
                pos = end + 2
                continue

        # Strikethrough ~~text~~
        if pos + 1 < length and text[pos : pos + 2] == "~~":
            end = text.find("~~", pos + 2)
            if end > pos:
                inner = text[pos + 2 : end]
                inner_tokens = _tokenize_inline(inner)
                tokens.append(Token(type="del", tokens=inner_tokens))
                pos = end + 2
                continue

        # Italic (* or _)
        if text[pos] in ("*", "_") and pos + 1 < length and text[pos + 1] != " ":
            marker = text[pos]
            end = text.find(marker, pos + 1)
            if end > pos and text[end - 1] != " ":
                inner = text[pos + 1 : end]
                inner_tokens = _tokenize_inline(inner)
                tokens.append(Token(type="em", tokens=inner_tokens))
                pos = end + 1
                continue

        # Link [text](url)
        if text[pos] == "[":
            bracket_end = text.find("]", pos + 1)
            if bracket_end > pos and bracket_end + 1 < length and text[bracket_end + 1] == "(":
                paren_end = text.find(")", bracket_end + 2)
                if paren_end > bracket_end:
                    link_text = text[pos + 1 : bracket_end]
                    link_url = text[bracket_end + 2 : paren_end]
                    link_tokens = _tokenize_inline(link_text)
                    token = Token(
                        type="link",
                        text=link_text,
                        raw=link_url,
                        tokens=link_tokens,
                    )
                    # Store href in raw field
                    tokens.append(token)
                    pos = paren_end + 1
                    continue

        # Regular character
        if tokens and tokens[-1].type == "text":
            tokens[-1] = Token(
                type="text",
                text=tokens[-1].text + text[pos],
            )
        else:
            tokens.append(Token(type="text", text=text[pos]))
        pos += 1

    return tokens


def _tokenize_markdown(text: str) -> list[Token]:
    """Tokenize markdown text into block-level tokens."""
    tokens: list[Token] = []
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Code block
        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # Skip closing ```
            tokens.append(Token(
                type="code",
                text="\n".join(code_lines),
                lang=lang,
            ))
            continue

        # Empty line -> space token
        if not line.strip():
            tokens.append(Token(type="space"))
            i += 1
            continue

        # Heading
        heading_match = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2)
            inline_tokens = _tokenize_inline(heading_text)
            tokens.append(Token(
                type="heading",
                text=heading_text,
                depth=level,
                tokens=inline_tokens,
            ))
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^[-*_]{3,}\s*$", line.strip()):
            tokens.append(Token(type="hr"))
            i += 1
            continue

        # Table detection
        if (
            i + 1 < len(lines)
            and "|" in line
            and re.match(r"^\s*\|?[\s:]*[-]+[\s:]*(\|[\s:]*[-]+[\s:]*)*\|?\s*$", lines[i + 1])
        ):
            table_token, consumed = _parse_table(lines, i)
            if table_token:
                tokens.append(table_token)
                i += consumed
                continue

        # Blockquote
        if line.startswith(">"):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].startswith(">"):
                stripped = lines[i][1:]
                if stripped.startswith(" "):
                    stripped = stripped[1:]
                quote_lines.append(stripped)
                i += 1
            quote_text = "\n".join(quote_lines)
            quote_tokens = _tokenize_markdown(quote_text)
            tokens.append(Token(
                type="blockquote",
                tokens=quote_tokens,
            ))
            continue

        # Unordered list
        list_match = re.match(r"^(\s*)([-*+])\s+(.*)", line)
        if list_match:
            list_token, consumed = _parse_list(lines, i, ordered=False)
            tokens.append(list_token)
            i += consumed
            continue

        # Ordered list
        ol_match = re.match(r"^(\s*)(\d+)\.\s+(.*)", line)
        if ol_match:
            list_token, consumed = _parse_list(lines, i, ordered=True)
            tokens.append(list_token)
            i += consumed
            continue

        # Paragraph (collect consecutive non-blank lines)
        para_lines: list[str] = []
        while i < len(lines) and lines[i].strip() and not _is_block_start(lines[i]):
            para_lines.append(lines[i])
            i += 1
        if para_lines:
            para_text = " ".join(para_lines)
            inline_tokens = _tokenize_inline(para_text)
            tokens.append(Token(
                type="paragraph",
                text=para_text,
                tokens=inline_tokens,
            ))
            continue

        i += 1

    return tokens


def _is_block_start(line: str) -> bool:
    """Check if a line starts a new block-level element."""
    if re.match(r"^#{1,6}\s+", line):
        return True
    if line.strip().startswith("```"):
        return True
    if re.match(r"^[-*_]{3,}\s*$", line.strip()):
        return True
    if line.startswith(">"):
        return True
    if re.match(r"^\s*[-*+]\s+", line):
        return True
    if re.match(r"^\s*\d+\.\s+", line):
        return True
    return False


def _parse_list(
    lines: list[str], start: int, ordered: bool
) -> tuple[Token, int]:
    """Parse a list starting at the given line index."""
    items: list[Token] = []
    i = start
    start_number = 1

    if ordered:
        m = re.match(r"^\s*(\d+)\.\s+", lines[i])
        if m:
            start_number = int(m.group(1))

    # Determine the indent level of this list
    first_indent = len(lines[i]) - len(lines[i].lstrip())

    while i < len(lines):
        line = lines[i]

        # Check for list item at this indent level
        if ordered:
            item_match = re.match(r"^(\s*)(\d+)\.\s+(.*)", line)
        else:
            item_match = re.match(r"^(\s*)([-*+])\s+(.*)", line)

        if not item_match:
            break

        item_indent = len(item_match.group(1))
        if item_indent != first_indent:
            break

        item_text = item_match.group(3)
        i += 1

        # Collect continuation lines and nested content
        item_content_lines = [item_text]
        continuation_indent = first_indent + 2

        while i < len(lines):
            next_line = lines[i]

            # Empty line might be between items
            if not next_line.strip():
                # Check if this is between items or end of list
                if i + 1 < len(lines):
                    next_real = lines[i + 1]
                    real_indent = len(next_real) - len(next_real.lstrip())
                    if real_indent >= continuation_indent:
                        item_content_lines.append("")
                        i += 1
                        continue
                break

            next_indent = len(next_line) - len(next_line.lstrip())

            # Nested content
            if next_indent >= continuation_indent:
                item_content_lines.append(next_line[continuation_indent:])
                i += 1
                continue

            break

        # Parse item content into tokens
        full_content = "\n".join(item_content_lines)
        item_tokens = _tokenize_markdown(full_content)

        # Keep block boundaries: inline tokens belong to one paragraph, not
        # separate list-item rows (codespans/emphasis must flow with the text).
        item_token = Token(type="list_item", tokens=item_tokens)

        items.append(item_token)

    return Token(
        type="list",
        items=items,
        ordered=ordered,
        start=start_number,
    ), i - start


def _parse_table(
    lines: list[str], start: int
) -> tuple[Token | None, int]:
    """Parse a markdown table starting at the given line."""
    if start + 1 >= len(lines):
        return None, 1

    header_line = lines[start]
    separator_line = lines[start + 1]

    # Verify separator line
    if not re.match(r"^\s*\|?[\s:]*[-]+[\s:]*(\|[\s:]*[-]+[\s:]*)*\|?\s*$", separator_line):
        return None, 1

    # Parse header cells
    header_cells = _parse_table_row(header_line)
    if not header_cells:
        return None, 1

    header_tokens = [
        Token(type="table_cell", text=cell, tokens=_tokenize_inline(cell))
        for cell in header_cells
    ]

    # Parse data rows
    rows: list[list[Token]] = []
    i = start + 2
    while i < len(lines):
        line = lines[i]
        if not line.strip() or "|" not in line:
            break
        cells = _parse_table_row(line)
        # Pad cells to match header
        while len(cells) < len(header_cells):
            cells.append("")
        row_tokens = [
            Token(type="table_cell", text=cell, tokens=_tokenize_inline(cell))
            for cell in cells[:len(header_cells)]
        ]
        rows.append(row_tokens)
        i += 1

    raw_text = "\n".join(lines[start:i])
    return Token(
        type="table",
        header=header_tokens,
        rows=rows,
        raw=raw_text,
    ), i - start


def _parse_table_row(line: str) -> list[str]:
    """Parse a table row into cell contents."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


# ---------------------------------------------------------------------------
# Markdown component
# ---------------------------------------------------------------------------

class Markdown(Component):
    """Markdown component that renders to ANSI-styled string lines.

    Ported from TypeScript Markdown class.
    """

    def __init__(
        self,
        text: str = "",
        padding_x: int = 1,
        padding_y: int = 1,
        theme: MarkdownTheme | None = None,
        default_text_style: DefaultTextStyle | None = None,
        *, compact_paragraphs: bool = False,
    ) -> None:
        super().__init__()
        self._text = apply_markdown_transforms(text)
        self._padding_x = padding_x
        self._padding_y = padding_y
        self._theme = theme or _default_theme()
        self._compact_paragraphs = compact_paragraphs
        self._default_text_style = default_text_style
        self._default_style_prefix: str | None = None
        # Cache
        self._cached_text: str | None = None
        self._cached_width: int | None = None
        self._cached_lines: list[str] | None = None

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self._text = value
        self.invalidate()

    def set_text(self, text: str) -> None:
        """Set the markdown text content."""
        self._text = apply_markdown_transforms(text)
        self.invalidate()

    def invalidate(self) -> None:
        self._cached_text = None
        self._cached_width = None
        self._cached_lines = None
        self._default_style_prefix = None

    def render(self, width: int) -> list[str]:
        # Legacy cell-buffer model
        # New string-based model
        return self._render_lines(width)

    def preferred_height(self, width: int) -> int:
        """Return the number of rendered lines at the given width."""
        if not self._text:
            return 1
        lines = self._render_lines(width)
        return max(1, len(lines))

    def _render_lines(self, width: int) -> list[str]:
        """Core render logic returning string lines."""
        # Check cache
        if (
            self._cached_lines is not None
            and self._cached_text == self._text
            and self._cached_width == width
        ):
            return self._cached_lines

        content_width = max(1, width - self._padding_x * 2)

        # Empty text
        if not self._text or self._text.strip() == "":
            result: list[str] = []
            self._cached_text = self._text
            self._cached_width = width
            self._cached_lines = result
            return result

        # Replace tabs
        normalized = self._text.replace("\t", "   ")

        # Tokenize and render
        tokens = _tokenize_markdown(normalized)
        if self._compact_paragraphs:
            # Compact prose only: keep code/list/table/quote boundaries intact.
            compacted: list[Token] = []
            pending_spaces: list[Token] = []
            previous_block = ""
            for token in tokens:
                if token.type == "space":
                    pending_spaces.append(token)
                    continue
                if previous_block != "paragraph" or token.type != "paragraph":
                    compacted.extend(pending_spaces)
                pending_spaces.clear()
                compacted.append(token)
                previous_block = token.type
            tokens = [*compacted, *pending_spaces]
        rendered_lines: list[str] = []

        for idx, token in enumerate(tokens):
            next_type = tokens[idx + 1].type if idx + 1 < len(tokens) else None
            token_lines = self._render_token(token, content_width, next_type)
            rendered_lines.extend(token_lines)

        # Wrap lines
        wrapped_lines: list[str] = []
        for line in rendered_lines:
            wrapped_lines.extend(wrap_text_with_ansi(line, content_width))

        # Add margins and background
        left_margin = " " * self._padding_x
        right_margin = " " * self._padding_x
        bg_fn = self._default_text_style.bg_color if self._default_text_style else None
        content_lines: list[str] = []

        for line in wrapped_lines:
            # We apply margins but DO NOT ADD ANSI resets until AFTER apply_background_to_line
            # This ensures apply_background_to_line calculates padding using the clean strings
            # and applies the background BEFORE any reset.
            line_with_margins = left_margin + line + right_margin
            if bg_fn:
                content_lines.append(apply_background_to_line(line_with_margins, width, bg_fn))
            else:
                vis_len = visible_width(line_with_margins)
                pad_needed = max(0, width - vis_len)
                content_lines.append(line_with_margins + " " * pad_needed)

        # Add top/bottom padding
        empty_line = " " * width
        empty_lines: list[str] = []
        for _ in range(self._padding_y):
            if bg_fn:
                # Apply background to an empty padded line
                empty_lines.append(apply_background_to_line(" " * width, width, bg_fn))
            else:
                empty_lines.append(empty_line)

        result = [*empty_lines, *content_lines, *empty_lines]

        # Update cache
        self._cached_text = self._text
        self._cached_width = width
        self._cached_lines = result

        return result if result else [""]

    # -- style helpers -------------------------------------------------------

    def _apply_default_style(self, text: str) -> str:
        """Apply default text style to a string."""
        if not self._default_text_style:
            return text

        styled = text
        if self._default_text_style.color:
            styled = self._default_text_style.color(styled)
        if self._default_text_style.bold:
            styled = self._theme.bold(styled)
        if self._default_text_style.italic:
            styled = self._theme.italic(styled)
        if self._default_text_style.strikethrough:
            styled = self._theme.strikethrough(styled)
        if self._default_text_style.underline:
            styled = self._theme.underline(styled)
        return styled

    def _get_default_style_prefix(self) -> str:
        """Get the ANSI prefix for default text style."""
        if not self._default_text_style:
            return ""
        if self._default_style_prefix is not None:
            return self._default_style_prefix

        sentinel = "\x00"
        styled = sentinel
        if self._default_text_style.color:
            styled = self._default_text_style.color(styled)
        if self._default_text_style.bold:
            styled = self._theme.bold(styled)
        if self._default_text_style.italic:
            styled = self._theme.italic(styled)
        if self._default_text_style.strikethrough:
            styled = self._theme.strikethrough(styled)
        if self._default_text_style.underline:
            styled = self._theme.underline(styled)

        idx = styled.find(sentinel)
        self._default_style_prefix = styled[:idx] if idx >= 0 else ""
        return self._default_style_prefix

    def _get_style_prefix(self, style_fn: Callable[[str], str]) -> str:
        """Get the ANSI prefix from a style function."""
        sentinel = "\x00"
        styled = style_fn(sentinel)
        idx = styled.find(sentinel)
        return styled[:idx] if idx >= 0 else ""

    # -- token rendering -----------------------------------------------------

    def _render_token(
        self,
        token: Token,
        width: int,
        next_token_type: str | None,
        style_context: tuple[Callable[[str], str], str] | None = None,
    ) -> list[str]:
        """Render a block-level token to lines."""
        lines: list[str] = []

        if token.type == "heading":
            prefix = "#" * token.depth + " "
            heading_text = self._render_inline_tokens(token.tokens, style_context)
            if token.depth == 1:
                styled = self._theme.heading(self._theme.bold(self._theme.underline(heading_text)))
            elif token.depth == 2:
                styled = self._theme.heading(self._theme.bold(heading_text))
            else:
                styled = self._theme.heading(self._theme.bold(prefix + heading_text))
            lines.append(styled)
            if next_token_type != "space":
                lines.append("")

        elif token.type == "paragraph":
            para_text = self._render_inline_tokens(token.tokens, style_context)
            lines.append(para_text)
            if (next_token_type and next_token_type not in ("list", "space")
                    and not (self._compact_paragraphs and next_token_type == "paragraph")):
                lines.append("")

        elif token.type == "code":
            indent = self._theme.code_block_indent
            lines.append(self._theme.code_block_border(f"```{token.lang}"))
            if self._theme.highlight_code:
                highlighted = self._theme.highlight_code(token.text, token.lang or None)
                for hl_line in highlighted:
                    lines.append(f"{indent}{hl_line}")
            else:
                for code_line in token.text.split("\n"):
                    lines.append(f"{indent}{self._theme.code_block(code_line)}")
            lines.append(self._theme.code_block_border("```"))
            if next_token_type != "space":
                lines.append("")

        elif token.type == "list":
            list_lines = self._render_list(token, 0, style_context)
            lines.extend(list_lines)

        elif token.type == "table":
            table_lines = self._render_table(token, width, style_context)
            lines.extend(table_lines)

        elif token.type == "blockquote":
            def quote_style(t: str) -> str:
                return self._theme.quote(self._theme.italic(t))
            quote_prefix = self._get_style_prefix(quote_style)

            quote_content_width = max(1, width - 2)
            quote_ctx: tuple[Callable[[str], str], str] = (_identity, "")

            rendered_quote: list[str] = []
            for q_idx, q_token in enumerate(token.tokens):
                q_next = token.tokens[q_idx + 1].type if q_idx + 1 < len(token.tokens) else None
                rendered_quote.extend(
                    self._render_token(q_token, quote_content_width, q_next, quote_ctx)
                )

            # Remove trailing empty lines
            while rendered_quote and rendered_quote[-1] == "":
                rendered_quote.pop()

            for q_line in rendered_quote:
                if quote_prefix:
                    styled_line = q_line.replace("\x1b[0m", f"\x1b[0m{quote_prefix}")
                    styled_line = quote_style(styled_line)
                else:
                    styled_line = quote_style(q_line)
                for wrapped in wrap_text_with_ansi(styled_line, quote_content_width):
                    lines.append(self._theme.quote_border("\u2502 ") + wrapped)

            if next_token_type != "space":
                lines.append("")

        elif token.type == "hr":
            lines.append(self._theme.hr("\u2500" * min(width, 80)))
            if next_token_type != "space":
                lines.append("")

        elif token.type == "html":
            if token.raw:
                lines.append(self._apply_default_style(token.raw.strip()))

        elif token.type == "space":
            lines.append("")

        else:
            if token.text:
                lines.append(token.text)

        return lines

    def _render_inline_tokens(
        self,
        tokens: list[Token],
        style_context: tuple[Callable[[str], str], str] | None = None,
    ) -> str:
        """Render inline tokens to a styled string."""
        if style_context is None:
            apply_text: Callable[[str], str] = self._apply_default_style
            style_prefix = self._get_default_style_prefix()
        else:
            apply_text, style_prefix = style_context

        def apply_text_newlines(text: str) -> str:
            return "\n".join(apply_text(seg) for seg in text.split("\n"))

        result = ""
        ctx = (apply_text, style_prefix)

        for token in tokens:
            if token.type == "text":
                if token.tokens:
                    result += self._render_inline_tokens(token.tokens, ctx)
                else:
                    result += apply_text_newlines(token.text)

            elif token.type == "paragraph":
                result += self._render_inline_tokens(token.tokens, ctx)

            elif token.type == "strong":
                bold_content = self._render_inline_tokens(token.tokens, ctx)
                result += self._theme.bold(bold_content) + style_prefix

            elif token.type == "em":
                italic_content = self._render_inline_tokens(token.tokens, ctx)
                result += self._theme.italic(italic_content) + style_prefix

            elif token.type == "codespan":
                result += self._theme.code(token.text) + style_prefix

            elif token.type == "link":
                link_text_rendered = self._render_inline_tokens(token.tokens, ctx)
                href = token.raw
                href_cmp = href[7:] if href.startswith("mailto:") else href
                if token.text == href or token.text == href_cmp:
                    result += (
                        self._theme.link(self._theme.underline(link_text_rendered))
                        + style_prefix
                    )
                else:
                    result += (
                        self._theme.link(self._theme.underline(link_text_rendered))
                        + self._theme.link_url(f" ({href})")
                        + style_prefix
                    )

            elif token.type == "br":
                result += "\n"

            elif token.type == "del":
                del_content = self._render_inline_tokens(token.tokens, ctx)
                result += self._theme.strikethrough(del_content) + style_prefix

            elif token.type == "html":
                if token.raw:
                    result += apply_text_newlines(token.raw)

            else:
                if token.text:
                    result += apply_text_newlines(token.text)

        return result

    # -- list rendering ------------------------------------------------------

    def _render_list(
        self,
        token: Token,
        depth: int,
        style_context: tuple[Callable[[str], str], str] | None = None,
    ) -> list[str]:
        """Render a list with proper nesting."""
        lines: list[str] = []
        indent = "  " * depth
        start_num = token.start

        for idx, item in enumerate(token.items):
            if token.ordered:
                bullet = f"{start_num + idx}. "
            else:
                bullet = "- "

            item_lines = self._render_list_item(item.tokens, depth, style_context)

            if item_lines:
                first_line = item_lines[0]
                # Check if first line is a nested list (already indented)
                is_nested = first_line.startswith("  ") and re.search(
                    r"^\s+\x1b\[", first_line
                )

                if is_nested:
                    lines.append(first_line)
                else:
                    lines.append(
                        indent + self._theme.list_bullet(bullet) + first_line
                    )

                for j in range(1, len(item_lines)):
                    sub_line = item_lines[j]
                    is_nested_line = sub_line.startswith("  ") and re.search(
                        r"^\s+\x1b\[", sub_line
                    )
                    if is_nested_line:
                        lines.append(sub_line)
                    else:
                        lines.append(f"{indent}  {sub_line}")
            else:
                lines.append(indent + self._theme.list_bullet(bullet))

        return lines

    def _render_list_item(
        self,
        tokens: list[Token],
        parent_depth: int,
        style_context: tuple[Callable[[str], str], str] | None = None,
    ) -> list[str]:
        """Render list item content."""
        lines: list[str] = []

        for token in tokens:
            if token.type == "list":
                nested = self._render_list(token, parent_depth + 1, style_context)
                lines.extend(nested)
            elif token.type == "text":
                if token.tokens:
                    text = self._render_inline_tokens(token.tokens, style_context)
                else:
                    text = token.text
                lines.append(text)
            elif token.type == "paragraph":
                text = self._render_inline_tokens(token.tokens, style_context)
                lines.append(text)
            elif token.type == "code":
                indent = self._theme.code_block_indent
                lines.append(self._theme.code_block_border(f"```{token.lang}"))
                if self._theme.highlight_code:
                    highlighted = self._theme.highlight_code(token.text, token.lang or None)
                    for hl_line in highlighted:
                        lines.append(f"{indent}{hl_line}")
                else:
                    for code_line in token.text.split("\n"):
                        lines.append(f"{indent}{self._theme.code_block(code_line)}")
                lines.append(self._theme.code_block_border("```"))
            else:
                text = self._render_inline_tokens([token], style_context)
                if text:
                    lines.append(text)

        return lines

    # -- table rendering -----------------------------------------------------

    def _render_table(
        self,
        token: Token,
        available_width: int,
        style_context: tuple[Callable[[str], str], str] | None = None,
    ) -> list[str]:
        """Render a markdown table with width-aware cell wrapping."""
        lines: list[str] = []
        num_cols = len(token.header)

        if num_cols == 0:
            return lines

        border_overhead = 3 * num_cols + 1
        available_for_cells = available_width - border_overhead

        if available_for_cells < num_cols:
            if token.raw:
                fallback = wrap_text_with_ansi(token.raw, available_width)
                fallback.append("")
                return fallback
            return [""]

        # Calculate column widths
        natural_widths: list[int] = []
        for i, header_cell in enumerate(token.header):
            text = self._render_inline_tokens(header_cell.tokens, style_context)
            natural_widths.append(visible_width(text))

        for row in token.rows:
            for i, cell in enumerate(row):
                text = self._render_inline_tokens(cell.tokens, style_context)
                while i >= len(natural_widths):
                    natural_widths.append(0)
                natural_widths[i] = max(natural_widths[i], visible_width(text))

        # Simple column width allocation
        total_natural = sum(natural_widths)
        if total_natural + border_overhead <= available_width:
            col_widths = [max(w, 1) for w in natural_widths]
        else:
            # Distribute proportionally
            col_widths = []
            for nw in natural_widths:
                ratio = nw / max(total_natural, 1)
                col_widths.append(max(1, int(ratio * available_for_cells)))
            # Fix rounding
            allocated = sum(col_widths)
            leftover = available_for_cells - allocated
            for i in range(min(leftover, num_cols)):
                col_widths[i] += 1

        # Box-drawing join separators. Kept as locals (not inline in the
        # f-strings below) so the expression part contains no backslash escapes \u2014
        # that syntax only parses on Python 3.12+.
        cell_sep = " \u2502 "  # \u2502 between cells within a row
        top_sep = "\u2500\u252c\u2500"  # \u2500\u252c\u2500 between columns on the top border
        mid_sep = "\u2500\u253c\u2500"  # \u2500\u253c\u2500 between columns on the separator row
        bot_sep = "\u2500\u2534\u2500"  # \u2500\u2534\u2500 between columns on the bottom border

        # Top border
        top_cells = ["\u2500" * w for w in col_widths]
        lines.append(f"\u250c\u2500{top_sep.join(top_cells)}\u2500\u2510")

        # Header
        header_cell_lines: list[list[str]] = []
        for i, cell in enumerate(token.header):
            text = self._render_inline_tokens(cell.tokens, style_context)
            header_cell_lines.append(wrap_text_with_ansi(text, col_widths[i]))

        header_height = max((len(cl) for cl in header_cell_lines), default=1)
        for line_idx in range(header_height):
            parts = []
            for col_idx, cell_lines in enumerate(header_cell_lines):
                text = cell_lines[line_idx] if line_idx < len(cell_lines) else ""
                pad = max(0, col_widths[col_idx] - visible_width(text))
                parts.append(self._theme.bold(text + " " * pad))
            lines.append(f"\u2502 {cell_sep.join(parts)} \u2502")

        # Separator
        sep_cells = ["\u2500" * w for w in col_widths]
        separator = f"\u251c\u2500{mid_sep.join(sep_cells)}\u2500\u2524"
        lines.append(separator)

        # Data rows
        for row_idx, row in enumerate(token.rows):
            row_cell_lines: list[list[str]] = []
            for i in range(num_cols):
                if i < len(row):
                    text = self._render_inline_tokens(row[i].tokens, style_context)
                else:
                    text = ""
                row_cell_lines.append(wrap_text_with_ansi(text, col_widths[i]))

            row_height = max((len(cl) for cl in row_cell_lines), default=1)
            for line_idx in range(row_height):
                parts = []
                for col_idx, cell_lines in enumerate(row_cell_lines):
                    text = cell_lines[line_idx] if line_idx < len(cell_lines) else ""
                    pad = max(0, col_widths[col_idx] - visible_width(text))
                    parts.append(text + " " * pad)
                lines.append(f"\u2502 {cell_sep.join(parts)} \u2502")

            if row_idx < len(token.rows) - 1:
                lines.append(separator)

        # Bottom border
        bot_cells = ["\u2500" * w for w in col_widths]
        lines.append(f"\u2514\u2500{bot_sep.join(bot_cells)}\u2500\u2518")

        lines.append("")  # spacing after table
        return lines


# Backward compatibility alias
MarkdownComponent = Markdown
