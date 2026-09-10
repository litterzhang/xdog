"""Main-buffer painting with explicit logical-to-physical row ownership.

Only newly appended lines scroll. Shrinking chrome clears owned rows, never the
screen or native scrollback. Component formatting remains outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FrameState:
    lines: tuple[str, ...] = ()
    origin: int = 0  # Physical row of logical line zero; negative after scrolling.
    cursor_row: int = 0
    cursor_column: int = 0
    logical_cursor: int = 0
    width: int = 0
    height: int = 0
    transient_start: int = 0


class MainBufferRenderer:
    def __init__(self, *, origin_row: int = 0) -> None:
        self.state = FrameState(origin=origin_row, cursor_row=origin_row)

    def reanchor(self, row: int, column: int = 0) -> None:
        """Accept a terminal cursor report after a resize or resume."""
        from dataclasses import replace
        self.state = replace(
            self.state, origin=row - self.state.logical_cursor,
            cursor_row=row, cursor_column=column,
        )

    def render(
        self, lines: list[str], width: int, height: int,
        cursor: tuple[int, int] | None = None, transient_start: int | None = None,
        *, force: bool = False,
    ) -> str:
        width, height = max(1, width), max(1, height)
        old = self.state
        transient = len(lines) if transient_start is None else transient_start
        origin = old.origin
        row, column = min(old.cursor_row, height - 1), old.cursor_column
        output: list[str] = []

        def move(target: int, col: int = 0) -> None:
            nonlocal row, column
            target = max(0, min(height - 1, target))
            if target != row:
                output.append(f"\x1b[{abs(target - row)}{'B' if target > row else 'A'}")
            if column != col:
                output.append("\r" if col == 0 else f"\x1b[{col + 1}G")
            row, column = target, col

        resized = bool(old.width and (old.width != width or old.height != height))
        if resized:
            # Terminal reflow is not reversible. Repaint the current owned tail
            # instead of attempting to re-create historical terminal rows.
            logical_anchor = cursor[0] if cursor is not None else max(0, len(lines) - 1)
            origin = min(row - logical_anchor, height - len(lines))
            force = True

        cleared: set[int] = set()
        if len(lines) > len(old.lines) and not resized:
            # Old composer/progress rows must never be scrolled into history
            # when new transcript lines are inserted ahead of them.
            for index in range(old.transient_start, len(old.lines)):
                physical = old.origin + index
                if 0 <= physical < height:
                    move(physical)
                    output.append("\x1b[2K")
                    cleared.add(index)

        for index, line in enumerate(lines):
            physical = origin + index
            if physical < 0:
                continue  # Native scrollback is immutable.
            unchanged = index < len(old.lines) and old.lines[index] == line
            if unchanged and index not in cleared and not force:
                continue
            if physical >= height:
                move(height - 1)
                count = physical - height + 1
                output.append("\r\n" * count)
                origin -= count
                physical = height - 1
                row, column = physical, 0
            move(physical)
            output.extend(("\x1b[2K", line, "\r"))
            column = 0

        # Erase vacated rows in the previously owned area; do not scroll.
        new_bottom = origin + len(lines)
        old_bottom = min(height, old.origin + len(old.lines))
        if resized:
            old_bottom = min(height, max(old_bottom, new_bottom))
        for physical in range(max(0, new_bottom), old_bottom):
            move(physical)
            output.append("\x1b[2K")

        logical_cursor = max(0, len(lines) - 1)
        if cursor is not None and 0 <= origin + cursor[0] < height:
            logical_cursor = cursor[0]
            move(origin + cursor[0], min(width - 1, max(0, cursor[1])))
        elif lines:
            move(origin + logical_cursor)
        self.state = FrameState(
            tuple(lines), origin, row, column, logical_cursor,
            width, height, max(0, min(len(lines), transient)),
        )
        if not output:
            return ""
        return "\x1b[?2026h" + "".join(output) + "\x1b[?2026l"

    def finish(self) -> str:
        """Leave the shell below the visible owned tail, at column zero."""
        state = self.state
        if not state.lines:
            return ""
        bottom = min(max(0, state.height - 1), state.origin + len(state.lines) - 1)
        delta = bottom - state.cursor_row
        move = f"\x1b[{abs(delta)}{'B' if delta > 0 else 'A'}" if delta else ""
        return move + "\r\n"
