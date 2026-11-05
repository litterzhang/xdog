"""UI-037: public renderer/container behavior, not application policy."""
from __future__ import annotations

import pytest
from xdog.tui import Component
from xdog.tui.tui import CURSOR_MARKER
from xdog.tui.utils import slice_by_column, string_width, strip_ansi, wrap_text_with_ansi


@pytest.mark.parametrize('glyph', ['👩\u200d💻', '🇺🇸', '1️⃣', 'e\u0301'])
@pytest.mark.parametrize('styled', [True])
def test_wrapped_long_words_preserve_whole_graphemes(glyph, styled):
    width = string_width(glyph)
    decorated = glyph[0] + '\x1b[31m' + glyph[1:] if styled else glyph
    lines = wrap_text_with_ansi(decorated * 3, width)
    assert [strip_ansi(line) for line in lines] == [glyph] * 3
    assert all(string_width(line) == width for line in lines)


def test_word_wrap_and_explicit_newline_keep_active_styles_without_changing_ascii_word_boundaries():
    value = wrap_text_with_ansi('\x1b[31mhello world\nnext word\x1b[0m', 7)
    assert [strip_ansi(line) for line in value] == ['hello', 'world', 'next', 'word']
    assert all('\x1b[31m' in line for line in value)


@pytest.mark.parametrize('start,end,expected', [(1, 3, '👩\u200d💻'), (2, 4, ' e\u0301')])
def test_host_overlay_cell_slicing_treats_embedded_styles_as_invisible(start, end, expected):
    value = slice_by_column('A👩\x1b[31m\u200d💻e\x1b[1m\u0301Z', start, end)
    assert strip_ansi(value) == expected
    assert string_width(value) == end - start


class CachedComponent(Component):
    """A real cache must be explicitly invalidated by its worker owner."""

    def __init__(self, text: str = 'old') -> None:
        self.text = text
        self.cache: dict[int, list[str]] = {}
        self.invalidations = 0
        self.heights: list[int] = []

    def invalidate(self) -> None:
        self.cache.clear()
        self.invalidations += 1

    def set_height(self, height: int) -> None:
        self.heights.append(height)

    def render(self, width: int) -> list[str]:
        return self.cache.setdefault(width, [self.text])


def test_slicing_carries_style_changes_from_inside_a_fully_cropped_glyph():
    line = '👩\x1b[31m\u200d💻Z'
    value = slice_by_column(line, 2, 3)
    assert strip_ansi(value) == 'Z' and '\x1b[31m' in value
    marked = 'A' + CURSOR_MARKER + '👩\x1b[31m\u200d💻B'
    assert CURSOR_MARKER in slice_by_column(marked, 1, 3)
    assert CURSOR_MARKER not in slice_by_column(marked, 2, 4)
