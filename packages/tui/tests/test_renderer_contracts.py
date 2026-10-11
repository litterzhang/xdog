"""UI-037: public renderer/container behavior, not application policy."""
from __future__ import annotations

import pytest
from xdog.tui import Component, Container
from xdog.tui.components.container import Container as ComponentContainer
from xdog.tui.components.custom_layout import (
    CustomBox,
    CustomComponent,
    CustomPadding,
    CustomText,
    CustomViewport,
    custom_frame,
)
from xdog.tui.tui import CURSOR_MARKER
from xdog.tui.utils import slice_by_column, string_width, strip_ansi, truncate_to_width, wrap_text_with_ansi


@pytest.mark.parametrize('glyph', ['👩\u200d💻', '🇺🇸', '1️⃣', '界', 'e\u0301'])
@pytest.mark.parametrize('styled', [False, True])
def test_wrapped_long_words_preserve_whole_graphemes(glyph, styled):
    width = string_width(glyph)
    decorated = glyph[0] + '\x1b[31m' + glyph[1:] if styled else glyph
    value = custom_frame(CustomText(decorated * 3, wrap=True), width, 10)
    assert [strip_ansi(line) for line in value['lines']] == [glyph] * 3
    assert all(string_width(line) == width for line in value['lines'])
    assert all(line.endswith('\x1b[0m') for line in value['lines'])


@pytest.mark.parametrize('glyph', ['👩\u200d💻', '🇺🇸', '1️⃣', '界'])
def test_glyph_wider_than_wrapped_viewport_is_one_blank_cell_not_partial_output(glyph):
    # A too-wide glyph cannot be displayed, but still occupies one bounded row.
    value = custom_frame(CustomText(glyph * 2, wrap=True), 1, 10)
    assert len(value['lines']) == 2
    assert all(not strip_ansi(line).strip() for line in value['lines'])


def test_word_wrap_and_explicit_newline_keep_active_styles_without_changing_ascii_word_boundaries():
    value = wrap_text_with_ansi('\x1b[31mhello world\nnext word\x1b[0m', 7)
    assert [strip_ansi(line) for line in value] == ['hello', 'world', 'next', 'word']
    assert all('\x1b[31m' in line for line in value)


@pytest.mark.parametrize('start,end,expected', [(1, 3, '👩\u200d💻'), (2, 4, ' e\u0301'),
                                               (0, 2, 'A '), (3, 4, 'e\u0301')])
def test_host_overlay_cell_slicing_treats_embedded_styles_as_invisible(start, end, expected):
    value = slice_by_column('A👩\x1b[31m\u200d💻e\x1b[1m\u0301Z', start, end)
    assert strip_ansi(value) == expected
    assert string_width(value) == end - start


@pytest.mark.parametrize('width,expected', [(1, ''), (2, '1️⃣'), (3, '1️⃣')])
def test_host_truncation_does_not_cut_styled_keycap_graphemes(width, expected):
    value = truncate_to_width('1\x1b[31m️⃣1\x1b[32m️⃣', width, '')
    assert strip_ansi(value) == expected
    assert string_width(value) <= width


@pytest.mark.parametrize('ellipsis', ['e\u0301', '👩\u200d💻', '\x1b[31m1️⃣\x1b[0m'])
def test_grapheme_ellipsis_exactly_fitting_width_is_not_sliced_by_codepoints(ellipsis):
    assert truncate_to_width('long text', string_width(ellipsis), ellipsis) == ellipsis


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


@pytest.mark.parametrize('marked', ['👩' + CURSOR_MARKER + '\x1b[31m\u200d💻',
                                   'e' + CURSOR_MARKER + '\x1b[1m\u0301',
                                   '1' + CURSOR_MARKER + '️⃣',
                                   '🇺' + CURSOR_MARKER + '🇸'])
def test_adapted_cursor_inside_grapheme_rounds_left_before_container_translation(marked):
    component = CachedComponent('x' + marked + 'tail')
    layout = CustomBox(CustomComponent(component))
    value = custom_frame(layout, 12, 3)
    assert value['cursor'] == [1, 2]
    assert not any(CURSOR_MARKER in line for line in value['lines'])
    # After horizontal scrolling, the glyph boundary is offscreen: no phantom
    # cursor on its trailing half or on the following glyph.
    scrolled = custom_frame(CustomViewport(CustomComponent(component), 20, 1, left=2), 8, 1)
    assert scrolled['cursor'] is None


def test_container_public_alias_nested_invalidation_and_worker_adapter_height_contract():
    assert ComponentContainer is Container
    leaf = CachedComponent()
    inner, outer = Container(), Container()
    inner.add_child(leaf)
    outer.add_child(inner)
    assert outer.render(20) == ['old']
    leaf.text = 'new'
    assert outer.render(20) == ['old']
    outer.invalidate()
    assert leaf.invalidations == 1 and outer.render(20) == ['new']
    # Container is an unbounded vertical concatenator, not a height allocator.
    # Bounded CustomComponent supplies the leaf's height and strips frame controls.
    assert custom_frame(CustomComponent(leaf), 20, 3)['lines']
    assert leaf.heights == [3]
    assert custom_frame(CustomComponent(leaf), 20, 0) == {'lines': [], 'cursor': None}
    assert leaf.heights == [3]
    inner.remove_child(leaf)
    inner.remove_child(leaf)
    assert outer.render(20) == []
    outer.clear()
    outer.invalidate()
    assert leaf.invalidations == 1


def test_padding_emits_its_right_cells_as_well_as_left_top_and_bottom():
    value = custom_frame(CustomPadding(CustomText('x'), top=1, right=2, bottom=1, left=1), 10, 3)
    assert [strip_ansi(line) for line in value['lines']] == [' ' * 10, ' x' + ' ' * 8, ' ' * 10]


def test_slicing_carries_style_changes_from_inside_a_fully_cropped_glyph():
    line = '👩\x1b[31m\u200d💻Z'
    value = slice_by_column(line, 2, 3)
    assert strip_ansi(value) == 'Z' and '\x1b[31m' in value
    marked = 'A' + CURSOR_MARKER + '👩\x1b[31m\u200d💻B'
    assert CURSOR_MARKER in slice_by_column(marked, 1, 3)
    assert CURSOR_MARKER not in slice_by_column(marked, 2, 4)
