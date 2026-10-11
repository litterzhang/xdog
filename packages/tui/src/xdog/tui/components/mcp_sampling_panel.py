"""Sampling input/output full-paint review; hosts own models, consent leases and IPC.

No agent imports: the dependency-light TUI validates display/receipt data only.
Host must enforce request schema, connection ownership and actual permission gates.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, cast

from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi

_MAX_BYTES = 256 * 1024


class _Theme:
    accent = bold = dim = border = staticmethod(lambda text: text)


def _json(value: Any) -> str:
    # Shape walk avoids a hostile deeply nested document consuming the renderer.
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 64 or count > 50000:
            raise ValueError('Sampling review JSON shape exceeds capacity')
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('Sampling review object keys must be strings')
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError('Sampling review must contain JSON data')
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode()) > _MAX_BYTES:
            raise ValueError('Sampling review JSON exceeds capacity')
        return encoded
    except (TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid sampling review JSON') from exc


class McpSamplingPanel(Component):
    """Selection form followed by exact request review; separate output release."""
    requires_visible_confirmation = True
    requires_current_viewport_input = True

    def __init__(self, event: dict[str, Any], models: tuple[str, ...],
                 answer: Callable[[dict[str, Any]], None], cancel: Callable[[], None]) -> None:
        # Snapshot before inspecting any externally mutable event object.
        value = json.loads(_json(event))
        if (not isinstance(value, dict) or value.get('type') != 'sampling_review'
                or value.get('phase') not in ('input', 'context', 'output') or not isinstance(value.get('body'), dict)
                or not isinstance(value.get('notice'), str) or len(value['notice'].encode()) > 8192
                or any(ord(c) < 0x20 and c != '\n' or ord(c) == 0x7f for c in value['notice'])):
            raise ValueError('Invalid sampling review envelope')
        for key in ('id', 'receipt'):
            item = value.get(key)
            if (not isinstance(item, str) or len(item) != 32
                    or any(c not in '0123456789abcdef' for c in item)):
                raise ValueError('Invalid sampling review identity')
        for key in ('server', 'lease'):
            if key in value and (not isinstance(value[key], str) or not 0 < len(value[key].encode()) <= 128
                                 or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value[key])):
                raise ValueError('Invalid sampling review host identity')
        body = _json(value['body'])
        if hashlib.sha256(body.encode()).hexdigest() != value.get('sha256'):
            raise ValueError('Sampling review digest mismatch')
        if (not isinstance(models, tuple) or len(models) > 576
                or any(not isinstance(model, str) or not 0 < len(model.encode()) <= 256
                       or any(ord(c) < 0x20 or ord(c) == 0x7f for c in model) for model in models)
                or len(set(models)) != len(models)):
            raise ValueError('Invalid sampling model choices')
        self._event = value
        self._models = models
        self.answer, self.on_cancel = answer, cancel
        self._phase = value['phase']
        self._mode = 'select' if self._phase == 'input' else 'review'
        self._selection: dict[str, Any] | None = None
        self._focused = False
        self._used = False
        self._disabled = False
        self._budget = 8
        self._scroll = 0
        self._seen: set[int] = set()
        self._layout = (0, 0, '')
        self._lines: list[str] = []
        self._visible = False
        self._message = ''
        self.editor = InputPanel(_Theme(), max_rows=2)
        self.editor.on_submit = self._select
        self.editor.on_escape = self._cancel
        if self._phase == 'input':
            params = value['body'].get('params')
            if (not isinstance(params, dict) or type(params.get('maxTokens')) is not int
                    or not 1 <= params['maxTokens'] <= 65536):
                raise ValueError('Invalid sampling request selection ceiling')
            self.editor.set_text(json.dumps({'model': '', 'max_tokens': min(1024, params['maxTokens']),
                                            'context': 'none', 'tools': []}))

    @property
    def event(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(_json(self._event)))

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if value != self._focused:
            self.invalidate()
        self._focused = value
        self.editor.focused = value and self._mode == 'select'

    def invalidate(self) -> None:
        self._seen.clear()
        self._visible = False
        self._layout = (0, 0, '')
        self._scroll = 0

    def disable(self, message: str) -> None:
        """Host withdrawal permanently revokes this panel; late repaint cannot reenable."""
        self._disabled = True
        self._message = sanitize_terminal_text(message)
        self.invalidate()
        self.editor.focused = False

    def set_render_budget(self, rows: int) -> None:
        rows = max(0, min(12, rows))
        if rows != self._budget:
            self.invalidate()
        self._budget = rows

    def _cancel(self) -> None:
        if not self._used:
            self._used = True
            self.editor.focused = False
            self.on_cancel()

    def _select(self, value: str) -> None:
        # InputPanel clears on submit; always preserve rejected editing data.
        self.editor.set_text(value)
        if not self.focused or self._used or self._disabled:
            return
        try:
            chosen = json.loads(value)
            params = self._event['body']['params']
            if (not isinstance(chosen, dict) or set(chosen) != {'model', 'max_tokens', 'context', 'tools'}
                    or chosen['model'] not in self._models or type(chosen['max_tokens']) is not int
                    or not 1 <= chosen['max_tokens'] <= params['maxTokens']
                    or chosen['context'] not in ('none', 'thisServer', 'allServers')
                    or not isinstance(chosen['tools'], list)
                    or any(not isinstance(name, str) for name in chosen['tools'])
                    or len(set(chosen['tools'])) != len(chosen['tools'])):
                raise ValueError('Choose an explicit allowed model/token/context/tool selection')
            ranks = {'none': 0, 'thisServer': 1, 'allServers': 2}
            ceiling = params.get('includeContext', 'none')
            tools = params.get('tools', [])
            if (ceiling not in ranks or not isinstance(tools, list)
                    or any(not isinstance(tool, dict) or not isinstance(tool.get('name'), str) for tool in tools)
                    or ranks[chosen['context']] > ranks[ceiling]
                    or not set(chosen['tools']) <= {tool['name'] for tool in tools}):
                raise ValueError('Sampling selection exceeds reviewed request ceiling')
            self._selection = json.loads(_json(chosen))
        except (ValueError, TypeError, KeyError):
            self._message = 'Invalid selection; inspect model/token/context/tool limits (no request sent)'
            return
        self._message = ''
        self._mode = 'review'
        self.editor.focused = False
        self.invalidate()

    def handle_paste(self, text: str) -> bool:
        if self.focused and not self._used and not self._disabled and self._mode == 'select':
            self.editor.handle_paste(text)
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        if not self.focused or self._used or event.event_type != KeyEventType.PRESS:
            return True
        if event.matches('escape') or event.matches('ctrl+c'):
            self._cancel()
            return True
        if self._disabled:
            return True
        if self._mode == 'select':
            self.editor.handle_input(event)
        elif event.matches('e') and self._phase == 'input':
            self._mode = 'select'
            self.editor.focused = True
            self._selection = None
            self.invalidate()
        elif event.matches('pagedown') or event.matches('down'):
            self._scroll += max(1, self._budget - 2) if event.matches('pagedown') else 1
        elif event.matches('pageup') or event.matches('up'):
            self._scroll = max(0, self._scroll - (max(1, self._budget - 2) if event.matches('pageup') else 1))
        elif event.matches('enter') and self._visible:
            self._used = True
            self.answer({'id': self._event['id'], 'phase': self._phase, 'receipt': self._event['receipt'],
                         'sha256': self._event['sha256'], 'approve': True,
                         **({'selection': json.loads(_json(self._selection))} if self._phase == 'input' else {})})
        return True

    def render(self, width: int) -> list[str]:
        if self._budget < 3 or width < 12:
            self.invalidate()
            return []
        title = 'Sampling ' + self._phase + ' review · ' + self._event.get('server', 'MCP peer')
        if self._disabled:
            return [truncate_to_width(title, width), truncate_to_width(self._message, width), 'Esc cancels']
        if self._mode == 'select':
            self.editor.set_render_budget(max(1, self._budget - 3), show_borders=False)
            lines = [title, truncate_to_width('Models: ' + ', '.join(self._models), width),
                     *self.editor.render(width), self._message or 'Enter prepares exact review · Esc denies']
            return lines[:self._budget]
        encoded = _json({'selection': self._selection, 'request': self._event['body']}
                        if self._phase == 'input' else self._event['body'])
        layout = (width, self._budget, encoded)
        if self._layout != layout:
            self.invalidate()
            self._layout = layout
            self._lines = wrap_text_with_ansi(sanitize_terminal_text(self._event['notice']), width)
            # JSON escapes controls instead of removing them; all byte metadata
            # remains inspectable, including image/audio data, without rendering it.
            # Word wrapping strips boundary spaces: inappropriate for exact
            # approval JSON. ASCII escaping also prevents C1/bidi controls from
            # becoming terminal instructions while preserving original values.
            displayed = json.dumps(json.loads(encoded), ensure_ascii=True, allow_nan=False,
                                   separators=(',', ':'))
            self._lines.extend(displayed[offset:offset + width] for offset in range(0, len(displayed), width))
        count = self._budget - 2
        self._scroll = min(self._scroll, max(0, len(self._lines) - count))
        if self.focused and not self._used:
            self._seen.update(range(self._scroll, min(len(self._lines), self._scroll + count)))
            self._visible = len(self._seen) == len(self._lines)
        footer = ('Enter approves exact ' + self._phase + ' · Esc denies' if self._visible else
                  'Read all pages · PgDown/PgUp · Esc denies')
        if self._phase == 'input':
            footer += ' · e edits selection'
        return [truncate_to_width(title, width), *self._lines[self._scroll:self._scroll + count],
                truncate_to_width(footer, width)]
