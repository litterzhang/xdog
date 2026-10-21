"""Owner-fenced, event-only child correspondence in the main chat log.

This is a live projection, not a persisted/replayed conversation or execution owner.
Only the supervisor can attest admission/reception; roster polls carry no messages.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from xdog.tui.components.extension_command import ExtensionCommand
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi


class ChildMessage(ExtensionCommand):
    """Reuse the chat's existing full Details/Transcript artifact protocol."""

    def __init__(self, event: dict[str, Any], owner: str) -> None:
        super().__init__('child-message', {'content': []}, event['text'], owner=owner)
        self.child_id = event['child_id']
        self.message_id = event['message_id']
        self.direction = event['direction']
        self.label = event.get('label', '')
        self.status = event['status']

    @property
    def detail_title(self) -> str:
        child = 'child ' + self.child_id + (f' ({self.label})' if self.label else '')
        route = f'Parent → {child}' if self.direction == 'parent_to_child' else f'{child} → Parent'
        return sanitize_terminal_text(f'{route} · {self.status} · message {self.message_id}')

    def render(self, width: int) -> list[str]:
        width = max(1, width)
        # Headers wrap too: narrow terminals must not silently lose identity/status.
        header = wrap_text_with_ansi(self.detail_title, width)
        body = wrap_text_with_ansi(self.detail_body, width)
        preview = body if self._expanded else body[:4]
        rows = [*header, *preview]
        if len(preview) < len(body):
            rows.extend(wrap_text_with_ansi(
                '[Preview only · Ctrl+O Details / transcript: full message · /agents inspect '
                + self.child_id + ': actual child history]', width))
        return [truncate_to_width(row, width, '') for row in rows]


class ChildMessagePresenter:
    def __init__(self, chat: Any, publish: Callable[[dict[str, Any]], None]) -> None:
        self.chat, self.publish = chat, publish
        self._owner: tuple[Any, ...] | None = None
        self._lease = ''
        self._unsubscribe: Callable[[], None] | None = None
        self._messages: dict[tuple[str, str, str], ChildMessage] = {}

    def bind(self, supervisor: Any, owner: tuple[Any, ...]) -> None:
        if self._owner == owner:
            return
        self.close()
        self._owner, self._lease = owner, uuid.uuid4().hex
        lease = self._lease

        def receive(event: dict[str, Any]) -> None:
            if event.get('type') != 'child_message':
                return
            # Snapshot only the public text envelope, never tool/thinking/snapshot data.
            data = {key: event.get(key) for key in
                    ('child_id', 'message_id', 'direction', 'text', 'status', 'label')}
            self.publish({'type': 'child_message_ui', 'lease': lease, 'message': data})

        try:
            self._unsubscribe = supervisor.subscribe(receive)
        except RuntimeError:
            self._owner = None  # A retired supervisor cannot acquire a display lease.
            self._lease = ''

    def display(self, event: dict[str, Any], owner: tuple[Any, ...]) -> bool:
        if owner != self._owner or not self._lease or event.get('lease') != self._lease:
            return False
        data = event.get('message')
        if not isinstance(data, dict) or any(not isinstance(data.get(key), str) for key in
                ('child_id', 'message_id', 'direction', 'text', 'status')):
            return False
        if not data['child_id'] or not data['message_id']:
            return False
        if (data['direction'], data['status']) not in {
                ('parent_to_child', 'admitted'), ('child_to_parent', 'received'),
                ('child_to_parent', 'completed'), ('child_to_parent', 'error'), ('child_to_parent', 'aborted')}:
            return False
        data = {**data, 'label': data.get('label') if isinstance(data.get('label'), str) else ''}
        key = data['child_id'], data['direction'], data['message_id']
        if key in self._messages:
            return False
        component = ChildMessage(data, self._lease)
        self._messages[key] = component
        self.chat.add_child(component)
        return True

    def close(self) -> None:
        # Revoke before unsubscribing: an already queued/in-flight callback is inert.
        self._lease, self._owner = '', None
        remove, self._unsubscribe = self._unsubscribe, None
        self._messages.clear()
        if remove is not None:
            remove()  # Never cancel, close or wait on child work.
