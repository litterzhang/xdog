"""Extension layout helpers are removed, native grapheme clipping remains."""
import importlib.util

import pytest
from xdog.tui.utils import slice_by_column, string_width, strip_ansi, truncate_to_width


def test_extension_custom_layout_module_is_removed():
    assert importlib.util.find_spec('xdog.tui.components.custom_layout') is None


@pytest.mark.parametrize('glyph', ['界', '👩\u200d💻', '🇺🇸', '1️⃣', 'e\u0301'])
def test_native_clipping_preserves_whole_graphemes(glyph):
    width = string_width(glyph)
    assert strip_ansi(truncate_to_width(glyph * 2, width, '')) == glyph
    assert strip_ansi(slice_by_column('x' + glyph + 'z', 1, 1 + width)) == glyph
