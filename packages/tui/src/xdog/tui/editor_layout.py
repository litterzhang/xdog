"""Immutable, grapheme-safe display-cell layout for prompt editors."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from wcwidth import wcswidth

_ZWJ = "‍"
_TAB_TEXT = "   "


@dataclass(frozen=True, slots=True)
class VisualRow:
    """One rendered row and its source-offset/display-column boundaries."""

    start: int
    end: int
    text: str
    boundaries: tuple[tuple[int, int], ...]

    def offset_for_column(self, column: int) -> int:
        return min(self.boundaries, key=lambda item: (abs(item[1] - column), item[1]))[0]

    def column_for_offset(self, offset: int) -> int:
        eligible = [item for item in self.boundaries if item[0] <= offset]
        return eligible[-1][1] if eligible else 0


@dataclass(frozen=True, slots=True)
class EditorLayout:
    """Complete visual layout of editor source text."""

    rows: tuple[VisualRow, ...]

    def position(self, offset: int) -> tuple[int, int]:
        for index, row in enumerate(self.rows):
            if row.start <= offset <= row.end:
                if offset == row.end and index + 1 < len(self.rows) and self.rows[index + 1].start == offset:
                    continue
                return index, row.column_for_offset(offset)
        last = self.rows[-1]
        return len(self.rows) - 1, last.column_for_offset(last.end)


def grapheme_clusters(text: str) -> tuple[tuple[int, int, str], ...]:
    """Return practical extended-grapheme clusters with source offsets.

    This intentionally stays dependency-light while covering the terminal cases
    editors need: combining marks, variation selectors, emoji modifiers, ZWJ
    sequences, keycaps, and paired regional indicators.
    """
    clusters: list[tuple[int, int, str]] = []
    start = 0
    regional_count = 0
    for index, char in enumerate(text):
        if index == start:
            regional_count = 1 if _is_regional(char) else 0
            continue
        previous = text[index - 1]
        joins = (
            unicodedata.combining(char) != 0
            or _is_variation(char)
            or _is_modifier(char)
            or char == "⃣"
            or previous == _ZWJ
            or char == _ZWJ
            or (_is_regional(char) and regional_count == 1)
        )
        if joins:
            if _is_regional(char):
                regional_count += 1
            continue
        clusters.append((start, index, text[start:index]))
        start = index
        regional_count = 1 if _is_regional(char) else 0
    if text:
        clusters.append((start, len(text), text[start:]))
    return tuple(clusters)


def layout_editor(text: str, width: int) -> EditorLayout:
    """Lay out source text using exactly the cells emitted by ``VisualRow.text``."""
    width = max(1, width)
    rows: list[VisualRow] = []
    row_start = row_end = column = 0
    row_parts: list[str] = []
    boundaries: list[tuple[int, int]] = [(0, 0)]

    def finish() -> None:
        nonlocal row_start, row_end, column, row_parts, boundaries
        rows.append(VisualRow(row_start, row_end, "".join(row_parts), tuple(boundaries)))
        row_start = row_end
        column = 0
        row_parts = []
        boundaries = [(row_start, 0)]

    for start, end, cluster in grapheme_clusters(text):
        if cluster == "\n":
            row_end = start
            if boundaries[-1][0] != start:
                boundaries.append((start, column))
            finish()
            row_start = row_end = end
            boundaries = [(end, 0)]
            continue

        display = _TAB_TEXT if cluster == "\t" else cluster
        measured = wcswidth(display)
        cluster_width = max(0, measured)
        if column > 0 and column + cluster_width > width:
            row_end = start
            finish()
        if boundaries[-1][0] != start:
            boundaries.append((start, column))
        row_parts.append(display)
        column += cluster_width
        row_end = end
        boundaries.append((end, column))

    final_end = max(row_end, row_start)
    rows.append(VisualRow(row_start, final_end, "".join(row_parts), tuple(boundaries)))
    if rows[-1].boundaries[-1][1] == width:
        rows.append(VisualRow(len(text), len(text), "", ((len(text), 0),)))
    return EditorLayout(tuple(rows))


def _is_variation(char: str) -> bool:
    return "︀" <= char <= "️" or "\U000e0100" <= char <= "\U000e01ef"


def _is_modifier(char: str) -> bool:
    return "\U0001f3fb" <= char <= "\U0001f3ff"


def _is_regional(char: str) -> bool:
    return "\U0001f1e6" <= char <= "\U0001f1ff"
