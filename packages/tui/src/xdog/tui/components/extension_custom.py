"""Viewport-owned custom extension data. Rendering never performs worker/socket I/O."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Any

from xdog.tui.editor_layout import grapheme_clusters
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import CURSOR_MARKER, Component, OverlayHandle, OverlayMargin, OverlayOptions
from xdog.tui.utils import sanitize_terminal_text, string_width, truncate_to_width, wrap_text_with_ansi

_CSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
_SGR = re.compile(r'\x1b\[([0-9;]*)m')
_SIMPLE = {0, 1, 2, 3, 4, 7, 9, 22, 23, 24, 27, 29, 39, 49,
           *range(30, 38), *range(40, 48), *range(90, 98), *range(100, 108)}


def _sgr(text: str) -> bool:
    match = _SGR.fullmatch(text)
    if match is None or len(text) > 128:
        return False
    values = [int(item or '0') for item in match[1].split(';')]
    index = 0
    while index < len(values):
        value = values[index]
        index += 1
        if value in _SIMPLE:
            continue
        if value not in (38, 48) or index >= len(values):
            return False
        mode = values[index]
        count = 1 if mode == 5 else 3 if mode == 2 else 0
        index += 1
        if not count or index + count > len(values) or any(not 0 <= v <= 255 for v in values[index:index + count]):
            return False
        index += count
    return True


def safe_custom_line(text: str) -> str:
    """Allow only vetted SGR styles, never OSC/graphics/cursor/private host markers."""
    result, index = [], 0
    while index < len(text):
        char = text[index]
        if char == '\x1b':
            if index + 1 < len(text) and text[index + 1] in ']_P^X':
                ends = [end for end in (text.find('\a', index + 2), text.find('\x1b\\', index + 2)) if end >= 0]
                if not ends:
                    break
                end = min(ends)
                index = end + (2 if text[end] == '\x1b' else 1)
                continue
            match = _CSI.match(text, index)
            if match is not None:
                if _sgr(match[0]):
                    result.append(match[0])
                index = match.end()
            else:
                index += 1
                while index < len(text) and ' ' <= text[index] <= '/':
                    index += 1
                if index < len(text) and '0' <= text[index] <= '~':
                    index += 1
            continue
        if char == '\t':
            result.append('   ')
        elif not unicodedata.category(char).startswith('C') or char == '\u200d':
            result.append(char)
        index += 1
    return ''.join(result)


def _plain_offsets(line: str) -> tuple[str, list[int]]:
    # Styles may occur INSIDE a grapheme (e.g. between a base and accent/ZWJ).
    # Segment-by-segment width/caret iteration would split that logical glyph.
    chars, offsets, index = [], [], 0
    while index < len(line):
        match = _SGR.match(line, index)
        if match is not None:
            index = match.end()
        else:
            chars.append(line[index])
            offsets.append(index)
            index += 1
    return ''.join(chars), offsets


def _clip(line: str, width: int) -> str:
    plain, offsets = _plain_offsets(line)
    cell = 0
    for start, _end, cluster in grapheme_clusters(plain):
        following = cell + string_width(cluster)
        if following > width:
            return line[:offsets[start]] + ' ' * max(0, width - cell)
        cell = following
    return line


def _cursor(line: str, column: int) -> str:
    """Place a host marker at a whole grapheme boundary, rounding left inside wide cells."""
    plain, offsets = _plain_offsets(line)
    cell = 0
    for start, _end, cluster in grapheme_clusters(plain):
        cells = string_width(cluster)
        if cell >= column or cell + cells > column:
            point = offsets[start]
            return line[:point] + CURSOR_MARKER + line[point:]
        cell += cells
    return line + ' ' * max(0, column - cell) + CURSOR_MARKER


def overlay_state(value: Any, previous: Any = 0) -> dict[str, Any] | None:
    if type(previous) is not int or not 0 <= previous < 2**53:
        previous = 0  # An invalid initial wire snapshot cannot poison later valid owner updates.
    if (not isinstance(value, dict) or set(value) != {'sequence', 'hidden', 'capturing'}
            or type(value['sequence']) is not int or not previous < value['sequence'] < 2**53
            or type(value['hidden']) is not bool or type(value['capturing']) is not bool):
        return None
    return dict(value)


class ExtensionCustom(Component):
    requires_visible_confirmation = True
    requires_current_viewport_input = True

    def __init__(self, extension: str, dialog: dict[str, Any], cancel: Callable[[Any], None],
                 request: Callable[[dict[str, Any]], bool]) -> None:
        self.extension, self.dialog, self.cancel, self.request = extension, dialog, cancel, request
        self._focused = False
        self._budget = 8
        self._geometry = (0, 0)
        self._generation = 0
        self._requested: tuple[int, int, int] | None = None
        self._frame: dict[str, Any] | None = None
        self._revision = 0
        self._painted = -1
        self._inflight = 0
        self._error = ''
        self._resolved = False
        self.fullscreen = False
        self.overlay = False
        self._overlay_visible = False
        self.overlay_hidden = False
        self.overlay_capturing = not (dialog.get('overlay') or {}).get('non_capturing', False)
        self._control_sequence = 0
        if 'overlay_state' in dialog and not self.accept_control(dialog['overlay_state']):
            self.dialog.pop('overlay_state', None)
        self._key_events = frozenset(dialog.get('key_events', ['press']))
        self.wants_key_release = 'release' in self._key_events
        self._details = bool(dialog.get('message', ''))
        self._detail_scroll = 0

    @property
    def frame_visible(self) -> bool:
        return self._focused or self.overlay and self._overlay_visible

    def set_overlay_visible(self, value: bool) -> None:
        if self._overlay_visible != value:
            self._overlay_visible = value
            self.invalidate()

    def accept_control(self, value: Any) -> bool:
        if (not self.dialog.get('live_overlay') or self.dialog.get('overlay') is None
                or overlay_state(value, self._control_sequence) is None):
            return False
        self._control_sequence = value['sequence']
        self.overlay_hidden, self.overlay_capturing = value['hidden'], value['capturing']
        self.dialog['overlay_state'] = dict(value)
        self.invalidate()
        return True

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if value != self._focused:
            self.invalidate()
        self._focused = value

    def invalidate(self) -> None:
        self._generation += 1
        self._painted = -1
        self._frame = None
        self._requested = None

    def set_height(self, height: int) -> None:
        if self.fullscreen or self.overlay:
            self.set_render_budget(height)

    def set_render_budget(self, rows: int) -> None:
        budget = max(0, min(203, rows))
        if budget != self._budget:
            self._budget = budget
            self.invalidate()

    def _send(self, kind: str, **options: Any) -> bool:
        width, height = self._geometry
        if kind == 'resize' and not self.frame_visible:
            height = 0
        if width <= 0 or self._resolved:
            return False
        payload = {'kind': kind, 'width': width, 'height': height, 'painted': self._painted,
                   'generation': self._generation, **options}
        # The callback only enqueues on the owning host; it must not perform I/O.
        try:
            accepted = not (self._inflight >= 32 and kind != 'resize') and self.request(payload)
        except (ValueError, RuntimeError, OSError):
            accepted = False
        if not accepted:
            self._error = 'Input queue busy/refused; input not sent'
            return False
        self._inflight += 1
        self._error = ''
        return True

    def poll(self) -> bool:
        target = (self._geometry[0], self._geometry[1] if self.frame_visible else 0, self._generation)
        if self._geometry[0] > 0 and target != self._requested:
            if self._send('resize'):
                self._requested = target
                return True
        return False

    def accept(self, data: dict[str, Any]) -> bool:
        if data.get('id') != self.dialog['id'] or self._resolved:
            return False
        kind = str(data.get('type', ''))
        if kind.endswith('_ack'):
            if data.get('accepted') is not True:
                generation = data.get('generation')
                if 'generation' in data and (type(generation) is not int
                        or not 0 <= generation <= min(self._generation, 2**53 - 1)):
                    return False  # Malformed/future metadata owns no pending input or paint proof.
                self._inflight = max(0, self._inflight - 1)
                if generation is not None and generation < self._generation:
                    # Settle only the refused old request. Clearing a newer _requested
                    # here duplicates its resize and races the freshly painted Enter.
                    return True
                self._error = 'Host refused custom input; review or cancel'
                self._requested = None
                self._painted = -1
            return True
        self._inflight = max(0, self._inflight - 1)
        if kind.endswith('_error'):
            self._error = 'Custom rendering/input failed; view canceled'
            self._painted = -1
            return True
        revision = data.get('revision')
        if type(revision) is not int or revision <= self._revision:
            return False
        self._revision = revision
        if (data.get('generation') != self._generation or (data.get('width'), data.get('height')) != self._geometry
                or not self.frame_visible):
            return False
        lines = data.get('lines')
        if (not isinstance(lines, list) or len(lines) > 200
                or any(not isinstance(line, str) or '\n' in line or '\r' in line or len(line.encode()) > 8192
                       for line in lines) or sum(len(line.encode()) for line in lines) > 128 * 1024):
            self._error = 'Invalid custom frame; cancel to recover'
            self._painted = -1
            return True
        cursor = data.get('cursor')
        if cursor is not None and (not isinstance(cursor, list) or len(cursor) != 2
                or any(type(item) is not int for item in cursor)
                or not 0 <= cursor[0] < min(len(lines), self._geometry[1]) or not 0 <= cursor[1] < self._geometry[0]):
            cursor = None
        self._frame = {'lines': [safe_custom_line(line) for line in lines],
                       'cursor': list(cursor) if cursor is not None else None,
                       'revision': revision, 'review_required': data.get('review_required') is True}
        self._painted = -1
        return True

    def handle_paste(self, text: str) -> bool:
        if len(text.encode()) > 16000:
            self._error = 'Paste exceeds 16,000 UTF-8 bytes; not sent'
        elif self._focused and self._painted > 0:
            self._send('paste', text=text)
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        if self._resolved:
            return True
        if event.key.lower() == 'escape' or event.ctrl and event.key.lower() == 'c':
            if event.event_type is KeyEventType.PRESS:
                self._resolved = True
                self.cancel(None)
        elif event.ctrl and event.key.lower() in ('d', 'l', 'z'):
            return event.event_type is not KeyEventType.PRESS  # Reserved host controls.
        elif event.matches('f1') and self.dialog.get('message'):
            if event.event_type is KeyEventType.PRESS:
                self._details = not self._details
                self.invalidate()
        elif self._details:
            if event.event_type in (KeyEventType.PRESS, KeyEventType.REPEAT):
                if event.key in ('up', 'pageup', 'down', 'pagedown', 'home', 'end'):
                    step = max(1, self._budget - 3) if event.key in ('pageup', 'pagedown') else 1
                    self._detail_scroll = (0 if event.key == 'home' else 100000 if event.key == 'end' else
                        max(0, self._detail_scroll + (-step if event.key in ('up', 'pageup') else step)))
        elif event.event_type.name.lower() in self._key_events and self._focused and self._painted > 0:
            try:
                valid = (0 < len(event.key.encode()) <= 128
                         and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in event.key))
            except UnicodeError:
                valid = False
            if valid:
                options: dict[str, Any] = {}
                if event.event_type is not KeyEventType.PRESS or self._key_events != frozenset({'press'}):
                    options['event_type'] = event.event_type.name.lower()
                self._send('key', key=event.key, ctrl=event.ctrl, alt=event.alt, shift=event.shift, **options)
            else:
                self._error = 'Unsupported key encoding; input not sent'
        return True

    def render(self, width: int) -> list[str]:
        width = max(0, min(10000, width))
        geometry = width, max(0, self._budget - 3) if width >= 24 and not self._details else 0
        if geometry != self._geometry:
            self._geometry = geometry
            self.invalidate()
        self._painted = -1
        if not self._budget or not width:
            return []
        lines = [_clip(safe_custom_line(f'Extension {self.extension} · custom view'), width),
                 _clip(safe_custom_line(self.dialog['title']), width)]
        if self._details:
            body = wrap_text_with_ansi(sanitize_terminal_text(self.dialog.get('message', '')), max(1, width))
            count = max(0, self._budget - 3)
            self._detail_scroll = min(self._detail_scroll, max(0, len(body) - count))
            lines.extend(_clip(line, width) for line in body[self._detail_scroll:self._detail_scroll + count])
        elif self._frame is not None and self.frame_visible and geometry[1]:
            body = [_clip(line, width) for line in self._frame['lines'][:geometry[1]]]
            cursor = self._frame['cursor']
            if self._focused and cursor is not None and cursor[0] < len(body):
                body[cursor[0]] = _cursor(body[cursor[0]], min(width - 1, cursor[1]))
            lines.extend(body)
            self._painted = self._frame['revision']
        elif geometry[1]:
            lines.append('Waiting for owned frame…')
        footer = self._error or ('Review updated frame · ' if self._frame and self._frame['review_required'] else '')
        if self.overlay and not self._focused and not self._details:
            footer += 'Non-capturing overlay · parent input stays focused · NOT tool approval'
        else:
            footer += ('F1 view · PgUp/PgDn details · Esc/Ctrl+C cancel · NOT tool approval' if self._details else
                   ('F1 details · ' if self.dialog.get('message') else '')
                   + f'Esc/Ctrl+C cancel · {self._inflight} pending · NOT tool approval')
        lines.append(footer)
        # Reset each line's SGR independently; no style can leak into another host component.
        return [truncate_to_width(line, width, '') + '\x1b[0m' for line in lines[:self._budget]]


class ExtensionCustomPresentation:
    """Own only a temporary screen presentation; the host supplies visibility/policy."""

    def __init__(self, tui: Any, layout: Any) -> None:
        self.tui, self.layout = tui, layout
        self.overlay_handle: OverlayHandle | None = None
        self.overlay_panel: ExtensionCustom | None = None

    def blocked(self) -> bool:
        """Our owned overlay does not preempt itself; any other visible surface does."""
        return bool(self.tui.has_overlay(exclude=self.overlay_panel))

    def sync(self, panel: ExtensionCustom | None, visible: bool) -> None:
        overlay = panel.dialog.get('overlay') if panel is not None and visible else None
        if self.overlay_handle is not None and (self.overlay_panel is not panel or overlay is None):
            previous = self.overlay_panel
            self.overlay_handle.hide()  # Remove ONLY our handle, never another host overlay.
            self.overlay_handle, self.overlay_panel = None, None
            if previous is not None:
                previous.overlay = False
                previous.set_overlay_visible(False)
                previous.invalidate()
        if overlay is not None and panel is not None and self.overlay_handle is None:
            placement = dict(overlay)
            if isinstance(placement.get('margin'), dict):
                placement['margin'] = OverlayMargin(**placement['margin'])
            panel.overlay = True
            panel.invalidate()
            self.overlay_panel = panel
            self.overlay_handle = self.tui.show_overlay(panel, OverlayOptions(**placement))
        if overlay is not None and panel is not None and self.overlay_handle is not None:
            handle = self.overlay_handle
            handle.set_hidden(panel.overlay_hidden)
            panel.set_overlay_visible(not panel.overlay_hidden)
            # Desired state is retained across host-owned blocking surfaces. Focus
            # requests only affect our selected overlay, never another host owner.
            if panel.overlay_capturing != handle.is_capturing():
                if panel.overlay_capturing and not panel.overlay_hidden:
                    handle.focus()
                elif not panel.overlay_capturing:
                    handle.unfocus()
        owner = self.layout.fullscreen_view
        requested = panel is not None and panel.dialog.get('fullscreen') is True and visible
        if isinstance(owner, ExtensionCustom) and (owner is not panel or not requested):
            owner.fullscreen = False
            owner.invalidate()
            self.layout.fullscreen_view = None
            self.tui.request_fullscreen(False)
        if requested and panel is not None:
            if (not panel.fullscreen or not self.tui.fullscreen and self.tui._screen_change is not True):
                if not self.tui.request_fullscreen(True):
                    return  # No inline downgrade during handoff/suspend; wait or cancel.
                panel.fullscreen = True
                panel.invalidate()
            self.layout.fullscreen_view = panel
