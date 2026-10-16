"""Full-paint provider auth review with local masked input and no automatic URL open."""
from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from typing import Any

from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.components.json_review import _json, _JsonReview
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import truncate_to_width


class ExtensionProviderAuth(ExtensionDialog):
    requires_current_viewport_input = True

    def __init__(self, extension: str, dialog: dict[str, Any], answer: Callable[[Any], None], *,
                 open_url: Callable[[str], None] | None = None) -> None:
        snapshot = json.loads(_json(dialog))
        body = snapshot.get('provider_auth')
        if (not isinstance(body, dict) or body.get('phase') not in {'consent', 'on_auth', 'on_prompt'}
                or not isinstance(snapshot.get('provider_auth_host'), str)
                or len(snapshot['provider_auth_host']) != 32
                or snapshot.get('default', '') != ''
                or snapshot.get('kind') != ('input' if body['phase'] == 'on_prompt' else 'confirm')):
            raise ValueError('Invalid host provider authentication review')
        super().__init__(extension, snapshot, answer)
        self._secret = ''
        self._secret_cursor = 0
        self._editing = False
        self._opened = False
        self._input_visible = False
        self._input_width = 0
        self._open_url = open_url
        encoded = _json(body)
        if hashlib.sha256(encoded.encode()).hexdigest() != snapshot.get('provider_auth_digest'):
            raise ValueError('Provider authentication review digest mismatch')
        event = {'type': 'json_review', 'phase': 'review', 'id': secrets.token_hex(16),
                 'receipt': secrets.token_hex(16), 'sha256': hashlib.sha256(encoded.encode()).hexdigest(),
                 'body': body, 'notice': 'Explicit extension provider authentication only. '
                 'Read every page before continuing. Login/refresh is not a model/tool permission. '
                 'URL acknowledgment does not open a browser. Sensitive input is not shown or saved.'}
        self._review = _JsonReview(event, self._reviewed, lambda: self._finish(None))

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if self._focused != value:
            self.invalidate()
        self._focused = value
        if not value:
            self._secret, self._secret_cursor = '', 0
        if hasattr(self, '_review'):
            self._review.focused = value and not self._editing
        self._input.focused = value and self._editing

    def invalidate(self) -> None:
        self._input_visible = False
        if hasattr(self, '_review'):
            self._review.invalidate()

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, min(12, rows))
        self._review.set_render_budget(self._budget)
        self._input_visible = False

    def _reviewed(self, _receipt: dict[str, Any]) -> None:
        if self.dialog['provider_auth']['phase'] == 'on_prompt':
            self._editing = True
            self._review.focused = False
            self._input.focused = self.focused
            self._input_visible = False
        else:
            self._finish(True)

    def _finish(self, value: Any) -> None:
        self._secret, self._secret_cursor = '', 0  # No generic undo/history/kill-ring stores sensitive text.
        self._input.focused = False
        super()._finish(value)

    def _submit(self, value: str) -> None:
        if (self.focused and self._input_visible and value.strip() and len(value.encode()) <= 8192
                and not any(ord(c) < 32 or ord(c) == 127 for c in value)):
            self._finish(value)

    def _insert(self, text: str) -> None:
        if (len((self._secret + text).encode()) > 8192
                or any(ord(c) < 32 or ord(c) == 127 for c in text)):
            return  # Never silently normalize an authorization code.
        self._secret = self._secret[:self._secret_cursor] + text + self._secret[self._secret_cursor:]
        self._secret_cursor += len(text)
        self._input_visible = False

    def handle_paste(self, text: str) -> bool:
        if self.focused and self._editing and not self._resolved:
            self._insert(text)
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        if not self.focused or self._resolved or event.event_type is not KeyEventType.PRESS:
            return True
        if event.matches('escape') or event.matches('ctrl+c'):
            self._finish(None)
        elif self._editing:
            if event.matches('enter'):
                self._submit(self._secret)
            elif event.matches('backspace') and self._secret_cursor:
                self._secret = self._secret[:self._secret_cursor - 1] + self._secret[self._secret_cursor:]
                self._secret_cursor -= 1
                self._input_visible = False
            elif event.matches('delete'):
                self._secret = self._secret[:self._secret_cursor] + self._secret[self._secret_cursor + 1:]
                self._input_visible = False
            elif event.matches('ctrl+u'):
                self._secret, self._secret_cursor = '', 0
                self._input_visible = False
            elif event.matches('left'):
                self._secret_cursor = max(0, self._secret_cursor - 1)
            elif event.matches('right'):
                self._secret_cursor = min(len(self._secret), self._secret_cursor + 1)
            elif len(event.key) == 1 and not event.ctrl and not event.alt:
                self._insert(event.key)
        elif event.matches('o') and self.dialog['provider_auth']['phase'] == 'on_auth':
            if self._review._visible and not self._opened and self._open_url is not None:
                self._opened = True
                self._open_url(self.dialog['provider_auth']['url'])
        else:
            self._review.handle_input(event)
        return True

    def render(self, width: int) -> list[str]:
        if self._resolved:
            return []
        if not self._editing:
            rows = self._review.render(width)
            if rows:
                rows[0] = truncate_to_width('Provider authentication · ' + self.dialog['provider_auth']['phase'], width)
                if self.dialog['provider_auth']['phase'] == 'on_auth' and self._review._visible:
                    rows[-1] = truncate_to_width(
                        ('o opens once · ' if self._open_url is not None else 'Manual URL only · ')
                        + 'Enter acknowledges only · Esc cancels', width)
            return rows
        self._input_visible = self.focused and self._budget >= 4 and width >= 24
        self._input_width = width
        if not self._input_visible:
            return []
        # Never call the ordinary input renderer with sensitive text. No secret
        # enters render lines, event dictionaries, history or parent editor state.
        count = len(self._secret)
        return [truncate_to_width(line, width) for line in [
            'Provider authentication · sensitive input',
            'Prompt reviewed · value hidden · not saved',
            '*' * min(count, max(0, width - 1)),
            'Enter sends only to original provider · Esc erases/cancels']]
