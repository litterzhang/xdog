"""Elicitation request/form/response review; host owns schema validation and IPC.

Reuse the exact-data full-paint review renderer. No agent dependency, browser,
login, provider or implicit clipboard/navigation calls in this component.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, cast

from xdog.tui.components.input_panel import InputPanel
from xdog.tui.components.mcp_sampling_panel import McpSamplingPanel, _json
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


class _Theme:
    accent = bold = dim = border = staticmethod(lambda text: text)


class McpElicitationPanel(Component):
    """Request review precedes editing; edited reply needs a local whole-data review.

    Accepted replies are subsequently reviewed again by the host's response phase,
    which binds canonical validated bytes. Decline/cancel sends no form data.
    """
    requires_visible_confirmation = True
    requires_current_viewport_input = True

    def __init__(self, event: dict[str, Any], answer: Callable[[dict[str, Any]], None],
                 cancel: Callable[[], None], *, open_url: Callable[[str], None] | None = None) -> None:
        value = json.loads(_json(event))
        if (not isinstance(value, dict) or value.get('type') != 'elicitation_review'
                or value.get('phase') not in ('request', 'response') or not isinstance(value.get('body'), dict)
                or not isinstance(value.get('sha256'), str)):
            raise ValueError('Invalid elicitation review envelope')
        if value.get('protocol') not in (None, '2025-11-25', '2026-07-28'):
            raise ValueError('Unsupported elicitation review protocol')
        self._modern = value.get('protocol') == '2026-07-28'
        self._event = value
        self.answer, self.on_cancel = answer, cancel
        self.open_url = open_url
        self._opened = False
        self._phase = value['phase']
        self._mode = 'request' if self._phase == 'request' else 'response'
        self._focused = False
        self._disabled = False
        self._used = False
        self._budget = 8
        self._reply: dict[str, Any] | None = None
        self._message = ''
        self.editor = InputPanel(_Theme(), max_rows=3)
        self.editor.on_submit = self._prepare_reply
        self.editor.on_escape = self._cancel
        draft = value.get('form_draft', {})
        if not isinstance(draft, dict) or value['phase'] != 'request' and 'form_draft' in value:
            raise ValueError('Invalid retained elicitation draft')
        self.editor.set_text(_json(draft))  # Retain explicit user edits, never schema defaults.
        # Synthetic display-only event: preserve digest, body, identity and notice.
        # The reused renderer never sends a sampling action; callbacks stay local.
        self._review = self._display(value['body'], value['sha256'], self._reviewed_request)
        if self._phase == 'request':
            params = value['body'].get('params')
            if (value['body'].get('method') != 'elicitation/create' or not isinstance(params, dict)
                    or params.get('mode', 'form') not in ('form', 'url')
                    or not isinstance(params.get('message'), str)):
                raise ValueError('Invalid elicitation request display')
            if params.get('mode', 'form') == 'form':
                schema = params.get('requestedSchema')
                if (not isinstance(schema, dict) or schema.get('type') != 'object'
                        or not isinstance(schema.get('properties'), dict) or len(schema['properties']) > 32):
                    raise ValueError('Invalid elicitation form display')
            elif (not isinstance(params.get('url'), str)
                    or (self._modern and 'elicitationId' in params)
                    or (not self._modern and not isinstance(params.get('elicitationId'), str))):
                raise ValueError('Invalid elicitation URL display')

    def _display(self, body: dict[str, Any], digest: str,
                 callback: Callable[[dict[str, Any]], None]) -> McpSamplingPanel:
        event = {**self._event, 'type': 'sampling_review', 'phase': 'output', 'body': body, 'sha256': digest}
        request = body if self._phase == 'request' else body.get('request', {})
        if self._modern and request.get('params', {}).get('mode') == 'url':
            event['notice'] = (str(event.get('notice', '')) + ' Modern URL acknowledgment only: '
                               'the original request continuation checks the outcome. '
                               'There is no external-completion notification or automatic browser/login.')
        panel = McpSamplingPanel(event, (), callback, self._cancel)
        panel.focused = self.focused
        panel.set_render_budget(self._budget)
        return panel

    @property
    def event(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(_json(self._event)))

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if self._focused != value:
            self.invalidate()
        self._focused = value
        self._review.focused = value and self._mode != 'form' and not self._disabled
        self.editor.focused = value and self._mode == 'form' and not self._disabled

    def invalidate(self) -> None:
        if hasattr(self, '_review'):
            self._review.invalidate()

    def disable(self, message: str) -> None:
        self._disabled = True
        self._message = sanitize_terminal_text(message)
        self.editor.focused = False
        self._review.disable(message)

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, min(12, rows))
        self._review.set_render_budget(self._budget)

    def _cancel(self) -> None:
        if not self._used:
            self._used = True
            self.editor.focused = False
            self.on_cancel()

    def _send(self, response: dict[str, Any]) -> None:
        if not self.focused or self._disabled or self._used:
            return
        self._used = True
        self.answer({'id': self._event['id'], 'phase': self._phase, 'receipt': self._event['receipt'],
                     'sha256': self._event['sha256'], 'response': json.loads(_json(response))})

    def _reviewed_request(self, _receipt: dict[str, Any]) -> None:
        if self._phase == 'response':
            self._send({'action': 'accept'})
        elif self._event['body']['params'].get('mode', 'form') == 'url':
            self._send({'action': 'accept'})  # Acknowledgment only; never opens the URI.
        else:
            self._mode = 'form'
            self._review.focused = False
            self.editor.focused = self.focused

    def _prepare_reply(self, value: str) -> None:
        self.editor.set_text(value)  # Preserve malformed/declined editing data.
        if not self.focused or self._disabled or self._used:
            return
        try:
            content = json.loads(value)
            if (not isinstance(content, dict) or set(content) - self._event['body']['params']['requestedSchema']
                    ['properties'].keys()):
                raise ValueError('Unrequested fields')
            schema = self._event['body']['params']['requestedSchema']
            required = schema.get('required', [])
            if (not isinstance(required, list) or any(not isinstance(key, str) for key in required)
                    or not set(required) <= content.keys()):
                raise ValueError('Missing required form fields')
            # Fail early for local editing mistakes without consuming the host
            # review receipt. Full constraints/formats remain checked by owner.
            for name, item in content.items():
                field = schema['properties'][name]
                if not isinstance(field, dict):
                    raise ValueError('Invalid field schema')
                kind = field.get('type')
                valid = ((kind == 'string' and isinstance(item, str))
                         or (kind == 'boolean' and type(item) is bool)
                         or (kind == 'integer' and type(item) is int)
                         or (kind == 'number' and type(item) in (int, float))
                         or (kind == 'array' and isinstance(item, list)
                             and all(isinstance(part, str) for part in item)))
                if not valid:
                    raise ValueError('Wrong declared primitive type')
            # UI type checks; canonical field constraints are verified by owner.
            for item in content.values():
                if type(item) not in (str, int, float, bool) and not (
                        isinstance(item, list) and all(isinstance(part, str) for part in item)):
                    raise ValueError('Invalid primitive reply')
            self._reply = {'action': 'accept', 'content': json.loads(_json(content))}
            body = {'request': self._event['body'], 'response': self._reply}
            encoded = _json(body)
        except (ValueError, TypeError, KeyError):
            self._message = 'Enter only requested typed JSON fields; invalid reply kept, no data sent'
            return
        self._message = ''
        self._mode = 'reply'
        self.editor.focused = False
        self._review = self._display(body, hashlib.sha256(encoded.encode()).hexdigest(),
                                    lambda _receipt: self._send(cast(dict[str, Any], self._reply)))

    def handle_paste(self, text: str) -> bool:
        if self.focused and not self._disabled and not self._used and self._mode == 'form':
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
        if (event.matches('o') and self._phase == 'request'
                and self._event['body']['params'].get('mode') == 'url'):
            if self._review._visible and not self._opened and self.open_url is not None:
                self._opened = True  # One-use request, never automatic retry after uncertain effects.
                self.open_url(self._event['body']['params']['url'])
            return True
        if event.matches('ctrl+d'):
            self._send({'action': 'decline'})  # Never includes editable private data.
        elif self._mode == 'form':
            self.editor.handle_input(event)
        elif self._mode == 'reply' and event.matches('e'):
            self._reply = None
            self._mode = 'form'
            self.editor.focused = True
            self.invalidate()
        else:
            self._review.handle_input(event)
        return True

    def render(self, width: int) -> list[str]:
        title = 'Elicitation ' + self._mode + ' · ' + self._event.get('server', 'MCP peer')
        if self._modern and self._event['body'].get('params', {}).get('mode') == 'url':
            title += ' · ack only; original request checks outcome'
        if self._mode == 'form' and not self._disabled:
            if self._budget < 3 or width < 12:
                self.invalidate()
                return []
            self.editor.set_render_budget(max(1, self._budget - 2), show_borders=False)
            return [truncate_to_width(title, width), *self.editor.render(width),
                    truncate_to_width(self._message or
                                      'Enter reviews exact reply · Ctrl+d decline · Esc cancel', width)][:self._budget]
        rendered = self._review.render(width)
        if rendered:
            rendered[0] = truncate_to_width(title, width)
            rendered[-1] = truncate_to_width(('Enter continues reviewed data' if self._review._visible else
                                             'Read all pages · PgDown/PgUp') + ' · Ctrl+d decline · Esc cancel', width)
        if rendered and self._phase == 'request' and self._event['body']['params'].get('mode') == 'url':
            rendered[-1] = truncate_to_width(('o opens reviewed URL once · Enter acknowledges · Esc cancel'
                                             if self._review._visible else
                                             'Read all pages before opening · PgDown/PgUp · Esc cancel'), width)
        return rendered
