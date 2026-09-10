from __future__ import annotations

from wcwidth import wcswidth
from xdog.tui.editor_layout import layout_editor


def test_zwj_emoji_is_one_cursor_cluster() -> None:
    family = "👨‍👩‍👧‍👦"
    layout = layout_editor(f"a{family}b", 20)

    assert layout.rows[0].boundaries == (
        (0, 0),
        (1, 1),
        (1 + len(family), 1 + wcswidth(family)),
        (2 + len(family), 2 + wcswidth(family)),
    )


def test_combining_character_never_splits_across_rows() -> None:
    text = "aéb"
    layout = layout_editor(text, 2)

    assert [row.text for row in layout.rows] == ["aé", "b"]
    assert layout.rows[0].boundaries == ((0, 0), (1, 1), (3, 2))


def test_exact_width_end_cursor_gets_own_row() -> None:
    layout = layout_editor("abcd", 4)

    assert [row.text for row in layout.rows] == ["abcd", ""]
    assert layout.position(4) == (1, 0)


def test_tabs_render_at_the_width_used_for_layout() -> None:
    layout = layout_editor("a\tb", 5)

    assert [row.text for row in layout.rows] == ["a   b", ""]
    assert layout.rows[0].boundaries == ((0, 0), (1, 1), (2, 4), (3, 5))


def test_vertical_target_uses_complete_cluster_boundaries() -> None:
    family = "👨‍👩‍👧‍👦"
    layout = layout_editor(f"ab\n{family}x", 20)

    assert layout.rows[1].offset_for_column(1) in {3, 3 + len(family)}
    assert layout.rows[1].offset_for_column(1) != 4
