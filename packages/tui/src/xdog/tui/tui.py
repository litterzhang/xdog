"""TUI engine — string-based component rendering with differential updates.

Direct port of the original TypeScript pi-tui architecture:

- ``Component.render(width) → list[str]`` — returns ANSI-styled lines
- ``Container`` — concatenates children's ``render()`` output
- ``TUI extends Container`` — diffs string arrays for differential rendering

Key design: renders in the **main terminal buffer** (no alternate screen)
so terminal scrollback is preserved. Uses relative cursor movements
and natural ``\\r\\n`` scrolling, matching the TypeScript implementation exactly.

Usage::

    tui = TUI()
    tui.add_child(Text("Hello"))
    tui.add_child(Spacer(1))
    tui.add_child(editor)
    tui.set_focus(editor)
    tui.start()  # blocking
"""

from __future__ import annotations

import os
import signal
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Literal

from xdog.tui.keys import KeyEvent, is_key_release, parse_key_events
from xdog.tui.main_buffer_renderer import MainBufferRenderer
from xdog.tui.stdin_buffer import EndOfInput, InputFrame, KeyBytes, Paste, RejectedPaste, StdinBuffer
from xdog.tui.terminal_protocol import TerminalProtocol
from xdog.tui.utils import string_width

# ---------------------------------------------------------------------------
# Focusable protocol and cursor marker
# ---------------------------------------------------------------------------

CURSOR_MARKER = "\x1b_pi:c\x07"
"""APC escape sequence used by focused components to mark the cursor position.

TUI finds and strips this marker, then positions the hardware cursor there.
"""


class Focusable:
    """Mix-in for components that can receive focus and display a hardware cursor."""

    focused: bool = False


def is_focusable(component: Component | None) -> bool:
    """Return ``True`` if *component* implements the :class:`Focusable` protocol."""
    return component is not None and hasattr(component, "focused")


# ---------------------------------------------------------------------------
# Overlay types
# ---------------------------------------------------------------------------

OverlayAnchor = Literal[
    "center",
    "top-left", "top-right",
    "bottom-left", "bottom-right",
    "top-center", "bottom-center",
    "left-center", "right-center",
]

SizeValue = int | str  # int for absolute, "50%" for percentage


@dataclass(frozen=True)
class OverlayMargin:
    """Margin from terminal edges for overlays."""

    top: int = 0
    right: int = 0
    bottom: int = 0
    left: int = 0


@dataclass(frozen=True)
class OverlayOptions:
    """Configuration for overlay positioning and sizing."""

    width: SizeValue | None = None
    min_width: int | None = None
    max_height: SizeValue | None = None
    anchor: OverlayAnchor = "center"
    offset_x: int = 0
    offset_y: int = 0
    row: SizeValue | None = None
    col: SizeValue | None = None
    margin: OverlayMargin | int | None = None
    visible: Callable[[int, int], bool] | None = None
    non_capturing: bool = False


def _parse_size_value(value: SizeValue | None, reference: int) -> int | None:
    """Resolve a :class:`SizeValue` to an absolute int."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.endswith("%"):
        try:
            pct = float(value[:-1])
            return int(reference * pct / 100)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Component interface (matches TypeScript Component)
# ---------------------------------------------------------------------------

class Component(ABC):
    """Base class for all TUI components.

    Subclasses implement ``render(width) → list[str]``.
    """

    wants_key_release: bool = False
    """Set to ``True`` if this component needs key release events (Kitty protocol)."""

    @abstractmethod
    def render(self, width: int) -> list[str]:
        """Render to ANSI-styled terminal lines."""
        ...

    def handle_input(self, event: KeyEvent) -> bool:
        """Handle keyboard input. Return True if consumed."""
        return False

    def handle_paste(self, text: str) -> bool:
        """Handle an atomic bracketed-paste payload."""
        return False

    def invalidate(self) -> None:
        """Clear cached rendering state."""
        pass

    def set_height(self, height: int) -> None:
        """Provide an available-height hint to adaptive inline components."""

    def handle_input_error(self, message: str) -> None:
        """Notify an editor when input was rejected without inserting it."""


# ---------------------------------------------------------------------------
# Input listener type
# ---------------------------------------------------------------------------

InputListenerResult = dict[str, object] | None  # {"consume": bool, "data": str} or None
InputListener = Callable[[KeyEvent], InputListenerResult]


# ---------------------------------------------------------------------------
# Container (matches TypeScript Container)
# ---------------------------------------------------------------------------

class Container(Component):
    """Stacks children vertically by concatenating their ``render()`` output."""

    def __init__(self) -> None:
        self.children: list[Component] = []

    def add_child(self, component: Component) -> None:
        self.children.append(component)

    def remove_child(self, component: Component) -> None:
        if component in self.children:
            self.children.remove(component)

    def clear(self) -> None:
        self.children.clear()

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        for child in self.children:
            lines.extend(child.render(width))
        return lines


# ---------------------------------------------------------------------------
# Overlay handle
# ---------------------------------------------------------------------------

class OverlayHandle:
    """Handle for controlling an overlay shown via :meth:`TUI.show_overlay`."""

    def __init__(self, tui: TUI, entry: _OverlayEntry) -> None:
        self._tui = tui
        self._entry = entry

    def hide(self) -> None:
        """Permanently remove the overlay and restore its prior focus."""
        if self._entry in self._tui._overlay_stack:
            was_focused = self._tui._focused is self._entry.component
            self._tui._overlay_stack.remove(self._entry)
            if was_focused:
                replacement = self._tui._get_topmost_visible_overlay()
                self._tui.set_focus(
                    replacement.component
                    if replacement is not None
                    else self._entry.previous_focus
                )
            if not self._tui._overlay_stack:
                sys.stdout.write("\x1b[?25l")  # Hide cursor
            self._tui.request_render()

    def set_hidden(self, hidden: bool) -> None:
        """Temporarily hide or show the overlay."""
        self._entry.hidden = hidden

    def is_hidden(self) -> bool:
        """Check if overlay is temporarily hidden."""
        return self._entry.hidden

    def focus(self) -> None:
        """Focus this overlay and bring it to the front."""
        self._tui._focused = self._entry.component

    def unfocus(self) -> None:
        """Release focus to the previous target."""
        self._tui._focused = None

    def is_focused(self) -> bool:
        """Check if this overlay has focus."""
        return self._tui._focused is self._entry.component


class _OverlayEntry:
    """Internal overlay state."""

    __slots__ = ("component", "options", "hidden", "previous_focus")

    def __init__(
        self,
        component: Component,
        options: OverlayOptions | None,
        previous_focus: Component | None = None,
    ) -> None:
        self.component = component
        self.options = options or OverlayOptions()
        self.hidden = False
        self.previous_focus = previous_focus


# ---------------------------------------------------------------------------
# TUI (matches TypeScript TUI extends Container)
# ---------------------------------------------------------------------------

class TUI(Container):
    """Main TUI with differential string-line rendering and overlay support.

    Faithful port of the TypeScript ``TUI`` class. Renders in the **main
    terminal buffer** (no alternate screen) so terminal scrollback is
    preserved.
    """

    def __init__(self, *, fullscreen: bool = False) -> None:
        super().__init__()
        self.fullscreen = fullscreen
        self._previous_lines: list[str] = []
        self._previous_width: int = 0
        self._previous_height: int = 0
        self._focused: Component | None = None
        self._render_requested: bool = False
        self._cursor_row: int = 0
        self._hardware_cursor_row: int = 0
        self._max_lines_rendered: int = 0
        self._previous_viewport_top: int = 0
        self._stopped: bool = False
        self._running: bool = False
        self._tick_callbacks: list[Callable[[], None]] = []
        self._frame_rate: float = 30.0
        self._overlay_stack: list[_OverlayEntry] = []
        self._input_listeners: list[InputListener] = []
        self._full_redraw_counter: int = 0
        self._suspend_requested = False
        self._painter = MainBufferRenderer()
        self._force_render = False

    def _terminal_enter_sequence(self) -> str:
        prefix = "\x1b[?1049h" if self.fullscreen else ""
        return prefix + "\x1b[?7l\x1b[?25l"

    def _terminal_leave_sequence(self) -> str:
        suffix = "\x1b[?1049l" if self.fullscreen else ""
        return "\x1b[?7h\x1b[?25h" + suffix

    # -- overlay management --------------------------------------------------

    def show_overlay(
        self,
        component: Component,
        options: OverlayOptions | None = None,
    ) -> OverlayHandle:
        """Show an overlay component on top of the main content."""
        entry = _OverlayEntry(component, options, self._focused)
        self._overlay_stack.append(entry)
        if not (options and options.non_capturing):
            self._focused = component
        self._render_requested = True
        return OverlayHandle(self, entry)

    def hide_overlay(self) -> None:
        """Pop the topmost overlay."""
        if self._overlay_stack:
            self._overlay_stack.pop()
            if not self._overlay_stack:
                sys.stdout.write("\x1b[?25l")
                self._focused = None
        self._render_requested = True

    def has_overlay(self) -> bool:
        """Return ``True`` if any overlay is currently visible."""
        return any(self._is_overlay_visible(e) for e in self._overlay_stack)

    def _is_overlay_visible(self, entry: _OverlayEntry) -> bool:
        if entry.hidden:
            return False
        if entry.options.visible is not None:
            w = _terminal_width()
            h = _terminal_height()
            return entry.options.visible(w, h)
        return True

    def _get_topmost_visible_overlay(self) -> _OverlayEntry | None:
        for i in range(len(self._overlay_stack) - 1, -1, -1):
            entry = self._overlay_stack[i]
            if entry.options.non_capturing:
                continue
            if self._is_overlay_visible(entry):
                return entry
        return None

    # -- input listeners -----------------------------------------------------

    def add_input_listener(self, listener: InputListener) -> None:
        """Register an input listener called before normal dispatch."""
        self._input_listeners.append(listener)

    def remove_input_listener(self, listener: InputListener) -> None:
        """Remove a previously registered input listener."""
        if listener in self._input_listeners:
            self._input_listeners.remove(listener)

    # -- focus ---------------------------------------------------------------

    def set_focus(self, component: Component | None) -> None:
        # Update Focusable state
        if is_focusable(self._focused):
            self._focused.focused = False  # type: ignore[union-attr]
        self._focused = component
        if is_focusable(component):
            component.focused = True  # type: ignore[union-attr]

    # -- tick callbacks ------------------------------------------------------

    def on_tick(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked each frame."""
        self._tick_callbacks.append(callback)

    # -- render request ------------------------------------------------------

    def request_render(self, force: bool = False) -> None:
        """Mark the screen as dirty so it will be redrawn."""
        if force:
            self._force_render = True
            self._full_redraw_counter += 1
        self._render_requested = True

    # -- properties ----------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    # -- main loop -----------------------------------------------------------

    def suspend(self) -> bool:
        """Request a controlled POSIX job-control suspend."""
        if os.name == "nt" or not hasattr(signal, "SIGTSTP"):
            return False
        self._suspend_requested = True
        self._running = False
        return True

    def start(self) -> None:
        """Run a single owned terminal session, re-entering after suspend."""
        self._stopped = False
        resumed = False
        while not self._stopped:
            self._running = True
            self._suspend_requested = False
            stdin = StdinBuffer()
            protocol = TerminalProtocol()
            enabled = False
            try:
                stdin.enter_raw()
                enabled = True
                sys.stdout.write(self._terminal_enter_sequence())
                if not resumed:
                    sys.stdout.write("\r\n")
                sys.stdout.write(protocol.startup())
                sys.stdout.flush()
                self._anchor_terminal(stdin, protocol, resumed=resumed)
                self.request_render(force=resumed)
                self._input_loop(stdin, protocol)
            finally:
                self._running = False
                try:
                    if enabled:
                        if not self._suspend_requested:
                            sys.stdout.write(self._painter.finish())
                        sys.stdout.write(protocol.cleanup())
                        sys.stdout.write(self._terminal_leave_sequence())
                        sys.stdout.flush()
                finally:
                    stdin.restore()
            if not self._suspend_requested:
                break
            self._suspend_process()
            resumed = True

    def _suspend_process(self) -> None:
        """Temporarily restore normal job control without nesting run loops."""
        previous = signal.getsignal(signal.SIGTSTP)
        try:
            signal.signal(signal.SIGTSTP, signal.SIG_DFL)
            os.killpg(os.getpgrp(), signal.SIGTSTP)
        finally:
            signal.signal(signal.SIGTSTP, previous)

    def _anchor_terminal(
        self, stdin: StdinBuffer, protocol: TerminalProtocol, *, resumed: bool,
    ) -> None:
        # CPR is optional: a non-reporting terminal uses a conservative bottom
        # row origin, and only the rows written by this process become owned.
        pending: list[InputFrame] = []
        deadline = time.monotonic() + 0.15
        while protocol.cursor_position is None and time.monotonic() < deadline:
            data = stdin.read(timeout=0.01)
            if stdin.eof:
                self.stop()
                break
            for frame in stdin.feed(data):
                pending.extend(protocol.filter_frame(frame))
        position = protocol.cursor_position
        row = min(_terminal_height() - 1, max(0, position[0] - 1)) if position else _terminal_height() - 1
        if resumed:
            self._painter.reanchor(row, max(0, position[1] - 1) if position else 0)
        else:
            self._painter = MainBufferRenderer(origin_row=row)
        for frame in pending:
            self._handle_frame(frame)
        output = protocol.pending_output()
        if output:
            sys.stdout.write(output)
            sys.stdout.flush()

    def _handle_frame(self, frame: InputFrame) -> bool:
        if isinstance(frame, EndOfInput):
            self.stop()
            return False
        if isinstance(frame, RejectedPaste):
            target = self._focused
            if target is not None:
                target.handle_input_error(frame.reason)
            return True
        if isinstance(frame, Paste):
            return self._dispatch_paste(frame.text)
        consumed = False
        for event in parse_key_events(frame.data):
            if is_key_release(event) and not (
                self._focused and self._focused.wants_key_release
            ):
                continue
            consumed = self._dispatch_input(event) or consumed
        return consumed

    def _input_loop(self, stdin: StdinBuffer, protocol: TerminalProtocol) -> None:
        interval = 1.0 / self._frame_rate
        while self._running:
            data = stdin.read(timeout=interval)
            if stdin.eof:
                self.stop()
                break
            frames = stdin.feed(data) if data else stdin.flush_expired()
            for frame in frames:
                for filtered in protocol.filter_frame(frame):
                    if self._handle_frame(filtered):
                        self.request_render()
            expired = protocol.expire_input()
            if expired and self._handle_frame(KeyBytes(expired)):
                self.request_render()
            output = protocol.expire_negotiation() + protocol.pending_output()
            if output:
                sys.stdout.write(output)
                sys.stdout.flush()
            if not self._running:
                break
            if (self._previous_width, self._previous_height) != (_terminal_width(), _terminal_height()):
                if self._previous_width:
                    sys.stdout.write(protocol.request_cursor_position())
                    sys.stdout.flush()
                    self._anchor_terminal(stdin, protocol, resumed=True)
                self.request_render()
            for callback in self._tick_callbacks:
                callback()
            if self._render_requested:
                self._render_requested = False
                self._do_render()

    def stop(self) -> None:
        """Signal the main loop to exit."""
        self._stopped = True
        self._running = False

    # -- input dispatch ------------------------------------------------------

    def _dispatch_paste(self, text: str) -> bool:
        overlay = self._get_topmost_visible_overlay()
        if overlay is not None and overlay.component.handle_paste(text):
            return True
        if self._focused is not None and self._focused is not (
            overlay.component if overlay is not None else None
        ):
            if self._focused.handle_paste(text):
                return True
        return self.handle_paste(text)

    def _dispatch_input(self, event: KeyEvent) -> bool:
        """Dispatch input and report whether a component consumed it."""
        # Escape belongs to the most specific active surface first. This lets a
        # permission panel deny one call instead of the global listener
        # canceling the entire turn.
        if event.matches("escape"):
            overlay = self._get_topmost_visible_overlay()
            offered = overlay.component if overlay is not None else None
            if offered is not None and offered.handle_input(event):
                return True
            if (
                self._focused is not None
                and self._focused is not offered
                and self._focused.handle_input(event)
            ):
                return True

        # Global listeners get first pass for non-contextual shortcuts.
        for listener in self._input_listeners:
            result = listener(event)
            if result and result.get("consume"):
                return True

        # Overlay gets focus if topmost.
        overlay = self._get_topmost_visible_overlay()
        if overlay is not None:
            if event.matches("escape"):
                return False
            return overlay.component.handle_input(event)

        if self._focused is not None and not event.matches("escape"):
            if self._focused.handle_input(event):
                return True
        return self.handle_input(event)

    # -- overlay compositing -------------------------------------------------

    def _composite_overlays(self, base_lines: list[str], width: int, height: int) -> list[str]:
        """Composite overlay content on top of base content."""
        if not self._overlay_stack:
            return base_lines

        # Overlay coordinates are viewport-relative, while base_lines contains
        # the entire main-buffer history. Positioning at row 0 of base_lines
        # puts a dialog near the beginning of a long conversation, outside the
        # currently visible terminal viewport. Translate screen rows to the
        # tail viewport before compositing.
        result = list(base_lines)
        viewport_top = max(0, len(result) - height)
        while len(result) < viewport_top + height:
            result.append("")

        for entry in self._overlay_stack:
            if not self._is_overlay_visible(entry):
                continue

            opts = entry.options
            margin = opts.margin
            if isinstance(margin, int):
                margin = OverlayMargin(top=margin, right=margin, bottom=margin, left=margin)
            elif margin is None:
                margin = OverlayMargin()

            # Determine overlay width
            ov_width = _parse_size_value(opts.width, width)
            if ov_width is None:
                ov_width = width - margin.left - margin.right
            if opts.min_width is not None:
                ov_width = max(ov_width, opts.min_width)
            ov_width = min(ov_width, width - margin.left - margin.right)

            # Render overlay
            ov_lines = entry.component.render(ov_width)

            # Apply max_height
            max_h = _parse_size_value(opts.max_height, height)
            if max_h is not None:
                ov_lines = ov_lines[:max_h]

            ov_height = len(ov_lines)

            # Calculate position based on anchor
            row, col = self._calculate_overlay_position(
                opts, ov_width, ov_height, width, height, margin
            )

            # Composite overlay onto result
            for i, ov_line in enumerate(ov_lines):
                target_row = viewport_top + row + i
                if 0 <= target_row < len(result):
                    base = result[target_row]
                    # Pad base line to width if needed
                    base_padded = base + " " * max(0, width - len(base))
                    # Replace columns [col, col+ov_width) with overlay content
                    prefix = base_padded[:col] if col < len(base_padded) else base_padded
                    suffix_start = col + ov_width
                    suffix = base_padded[suffix_start:] if suffix_start < len(base_padded) else ""
                    result[target_row] = prefix + ov_line + suffix

        return result[:max(len(base_lines), viewport_top + height)]

    def _calculate_overlay_position(
        self,
        opts: OverlayOptions,
        ov_width: int,
        ov_height: int,
        term_width: int,
        term_height: int,
        margin: OverlayMargin,
    ) -> tuple[int, int]:
        """Calculate (row, col) for an overlay based on its anchor."""
        # Explicit row/col takes priority
        if opts.row is not None or opts.col is not None:
            row = _parse_size_value(opts.row, term_height) or 0
            col = _parse_size_value(opts.col, term_width) or 0
            return row + opts.offset_y, col + opts.offset_x

        anchor = opts.anchor
        avail_w = term_width - margin.left - margin.right
        avail_h = term_height - margin.top - margin.bottom

        # Vertical position
        if anchor.startswith("top"):
            row = margin.top
        elif anchor.startswith("bottom"):
            row = margin.top + avail_h - ov_height
        else:  # center, left-center, right-center
            row = margin.top + (avail_h - ov_height) // 2

        # Horizontal position
        if anchor.endswith("left"):
            col = margin.left
        elif anchor.endswith("right"):
            col = margin.left + avail_w - ov_width
        else:  # center, top-center, bottom-center
            col = margin.left + (avail_w - ov_width) // 2

        return max(0, row + opts.offset_y), max(0, col + opts.offset_x)

    # -- differential renderer (matches TypeScript TUI.doRender) -------------

    def _do_render(self) -> None:
        """Paint only owned main-buffer rows, keeping logical history separate."""
        if self._stopped:
            return
        width, height = _terminal_width(), _terminal_height()
        resized = bool(self._previous_width and (
            self._previous_width != width or self._previous_height != height
        ))
        if resized:
            self.invalidate()
            for entry in self._overlay_stack:
                entry.component.invalidate()
        for child in self.children:
            child.set_height(height)
        lines = self.render(width)
        transient_start = len(lines)
        offset = 0
        for child in self.children:
            if hasattr(child, "transient_start"):
                transient_start = offset + int(child.transient_start)
                break
            offset += len(child.render(width)) if len(self.children) > 1 else 0
        if self._overlay_stack:
            lines = self._composite_overlays(lines, width, height)
        cursor: tuple[int, int] | None = None
        clean_lines: list[str] = []
        for index, line in enumerate(lines):
            marker = line.find(CURSOR_MARKER)
            if marker >= 0:
                cursor = (index, string_width(line[:marker]))
            clean_lines.append(line.replace(CURSOR_MARKER, ""))
        output = self._painter.render(
            clean_lines, width, height, cursor, transient_start,
            force=self._force_render,
        )
        self._force_render = False
        if output:
            sys.stdout.write(output)
            sys.stdout.flush()
        self._previous_lines = clean_lines
        self._previous_width, self._previous_height = width, height
        self._hardware_cursor_row = self._painter.state.cursor_row



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _terminal_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def _terminal_height() -> int:
    try:
        return os.get_terminal_size().lines
    except OSError:
        return 24
